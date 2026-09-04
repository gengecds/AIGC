"""Agent 1 - 剧本生成

用户输入（一句话/大纲/小说）→ 本地 Ollama 解析 → 结构化剧本 JSON
"""

import json
import logging
import os
from datetime import datetime
from typing import Union

from agents.base import Agent, AgentResult
from providers.base import LLMProvider
from providers.llm import OllamaProvider, get_llm_provider

logger = logging.getLogger(__name__)

# 剧本结构 prompt
SCRIPT_SYSTEM_PROMPT = """你是一个专业的AI漫剧剧本创作助手。请根据用户输入生成结构化剧本。

输出格式为JSON：
{
  "title": "剧本标题",
  "genre": "题材类型（科幻/奇幻/都市/古风/末世等）",
  "summary": "故事简介（100字以内）",
  "characters": [
    {
      "name": "角色名",
      "gender": "男/女",
      "age": "青年/中年/老年",
      "appearance": "外貌特征描述（用于SD出图）",
      "personality": "性格描述",
      "role": "主角/配角/反派"
    }
  ],
  "episodes": [
    {
      "episode_number": 1,
      "title": "第1集标题",
      "plot": "本集剧情概要",
      "dialogues": [
        {
          "scene": "场景描述",
          "character": "说话角色",
          "line": "对话内容"
        }
      ]
    }
  ]
}

要求：
1. 如果用户只输入一句话，自动扩展为一个完整剧本大纲
2. 对话框对话按场景分组，每个场景的对话连续；scene（场景）必须写成"时间+地点+光源"的具体描述，不允许空泛
3. 角色外貌描写要详细，包含发型/脸型/身材/服饰（用于后续SD出图）
4. 一集建议4-6个场景，每个场景3-8句对话
5. 必须返回合法的JSON，不要包含其他文字
6. **对白 line 绝不能为空**：每条 dialogue 都必须是完整的台词文本。口语化、生活化，像真人聊天：用短句、带语气词（啊/吧/嘛/嘞/唉/哟），可有停顿和口头禅；禁止书面语、排比句、成语堆砌、"时光飞逝"式文艺腔
7. 广告/推广题材时，广告语也避免过度押韵和堆砌，用一句大白话点出卖点即可
"""


def _as_dict_list(value) -> list:
    """把任意 value 归一化为 list[dict]。

    原因：qwen3:8b 在 json 模式下偶发把角色/剧集/对话错包成嵌套数组
    （如 episodes=[[{...}]]）或把单个对象包进数组（characters=[{...}] 反向）；
    这些都会让后续 `ep.get(...)` 因元素是 list 而抛 `'list' object has no attribute 'get'`。
    这里递归扁平数组、包裹单个 dict，保证返回的每一项都是 dict。
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append(item)
            else:
                out.extend(_as_dict_list(item))
        return out
    return []


def _fill_script_defaults(script: dict) -> dict:
    """兜底补全剧本：对白 line 为空 / scene 为空都补成具体内容，避免空壳剧本。

    目标：即使 LLM 漏输出台词或场景，也让前端能读到完整对白，分镜也能消费。
    """
    for ep in script.get("episodes", []) or []:
        if not isinstance(ep, dict):
            continue
        # 每集剧情概要兜底
        if not (ep.get("plot") or "").strip():
            ep["plot"] = ep.get("title") or "剧情推进"
        for dlg in ep.get("dialogues", []) or []:
            if not isinstance(dlg, dict):
                continue
            # 对白 line 为空 → 用角色名+场景生成一句能用的台词
            if not (dlg.get("line") or "").strip():
                char = dlg.get("character") or "角色"
                dlg["line"] = f"{char}：{dlg.get('scene') or '……'}（此处对白请补充）"
            # scene 为空 → 用角色/剧情补一类可渲染的场景
            if not (dlg.get("scene") or "").strip():
                dlg["scene"] = f"{script.get('title', '故事')}场景"
    return script


class ScriptAgent(Agent):
    """Agent 1：剧本生成"""

    name = "script_agent"

    def __init__(self, llm_provider: Union[str, LLMProvider] = "auto"):
        super().__init__(name="script_agent")
        if isinstance(llm_provider, LLMProvider):
            # 用户显式传了 Provider 实例 → 直接用（方便测试 mock）
            self.llm = llm_provider
        else:
            # 剧本生成长文本场景：优先用 V4-Flash（速度快、便宜）
            # 模型名从 config.yaml engine.llm_model_script 读，取不到就退回默认（None 交给 Provider 处理）
            from config.settings import settings
            default_model = getattr(settings.engine, "llm_model_script", None)
            # 走统一工厂函数：自动按 config.yaml 的 engine.llm_provider 选择后端；
            # 若选了 deepseek 但没配 Key，会自动回退到 Ollama，不会抛错
            self.llm = get_llm_provider(llm_provider, default_model=default_model)

    async def run(self, user_input: str, plan: dict | None = None) -> AgentResult:
        logger.info(f"[ScriptAgent] 收到输入: {user_input[:50]}...")

        if not user_input or len(user_input.strip()) < 2:
            return AgentResult(
                success=False,
                error="输入内容太短，请输入至少2个字符",
            )

        try:
            # 若存在研究方案（research_agent 产出），把风格/关键词/文案要点注入剧本 prompt
            prompt = user_input
            if plan:
                plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
                prompt = (
                    f"用户需求：{user_input}\n\n"
                    f"制作方案（务必遵循，尤其是风格方向、渲染引擎/光影关键词、文案要点）：\n"
                    f"{plan_json}\n\n"
                    f"请基于以上方案生成完整剧本，对白口语化生活化，广告语用大白话。"
                )

            # 注入当前所选风格的量化关键词（config 的 styles.<风格>.keywords），
            # 让剧本的场景/氛围描写贴合所选风格，而不是千篇一律。
            from config.style_resolver import style_keywords, style_label
            _kw = style_keywords()
            if _kw:
                prompt += (
                    f"\n\n本片风格为「{style_label()}」。请在场景、氛围、光影描写中自然融入以下关键词："
                    f"{'、'.join(_kw)}。"
                )

            raw = await self.llm.generate(
                prompt=prompt,
                system_prompt=SCRIPT_SYSTEM_PROMPT,
                # 不指定 model，让 OllamaProvider 用 config.yaml 的 ollama.model
                # json_mode=True 强制 Ollama 输出合法 JSON，避免格式错误
                json_mode=True,
            )

            # 用自愈解析器处理 LLM 输出：截断/杂质/尾逗号/围栏都能救回。
            # 只有自愈也救不回时才抛异常 → retry_async 捕获后自动重试。
            from providers.json_repair import repair_json
            try:
                parsed = repair_json(raw)
            except ValueError:
                logger.error(f"[ScriptAgent] LLM输出无法解析，前200字符: {raw[:200]}")
                raise

            # 防御：qwen3 偶发把剧本包进数组（[{...}]）或整体输出成 list。
            # 剧本必须是一个 object，若解析为 list 则尝试"剥壳"取第一个 dict，否则交给上层重试。
            script = parsed
            if isinstance(script, list):
                if len(script) == 1 and isinstance(script[0], dict):
                    script = script[0]
                    logger.warning("[ScriptAgent] LLM输出为单元素数组，已剥壳为对象")
                else:
                    logger.error(f"[ScriptAgent] LLM输出解析为list而非对象: {str(script)[:200]}")
                    raise ValueError("LLM输出解析为list而非剧本对象")
            if not isinstance(script, dict):
                logger.error(f"[ScriptAgent] LLM输出解析为非dict类型: {type(script)}")
                raise ValueError("LLM输出解析为非dict类型")

            # 归一化 characters / episodes / dialogues：qwen3 偶发把数组包成 [[...]]，
            # 或把单个对象错当数组元素，统一转成 list[dict]，避免后续 .get() 报错。
            script["characters"] = _as_dict_list(script.get("characters"))
            script["episodes"] = _as_dict_list(script.get("episodes"))
            for ep in script["episodes"]:
                if isinstance(ep, dict):
                    ep["dialogues"] = _as_dict_list(ep.get("dialogues"))

            # 基本校验
            if "title" not in script:
                script["title"] = user_input[:20]

            # ── 剧本字段兜底：对白 line 为空时补具体台词，scene 为空时补环境 ──
            _fill_script_defaults(script)

            result = AgentResult(
                success=True,
                data=script,
                metadata={
                    "agent": self.name,
                    "timestamp": datetime.utcnow().isoformat(),
                    "characters": len(script.get("characters", [])),
                    "episodes": len(script.get("episodes", [])),
                },
            )

            logger.info(
                f"[ScriptAgent] 完成: {script['title']}, "
                f"{result.metadata['characters']}角色, "
                f"{result.metadata['episodes']}集"
            )
            return result

        except Exception as e:
            # 重要：这里必须「重新抛出异常」而不是 return success=False。
            # 原因：scheduler 用 retry_async 包裹本方法，只有「抛异常」才会触发重试；
            # 若返回 success=False，retry_async 会当作"正常返回"直接返回，永不重试，
            # 导致 Ollama 偶发坏 JSON（如只输出单个 {）时管线第一次就失败（写真那单就死在这）。
            logger.error(f"[ScriptAgent] 生成/解析失败，抛给上层重试: {e}")
            raise
