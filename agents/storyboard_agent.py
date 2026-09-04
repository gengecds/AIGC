"""Agent 2 - 分镜生成 + 校验层

剧本JSON → 本地 Ollama 拆分镜 → 校验层（shot_type/duration/prompt）→ 分镜JSON
"""

import json
import logging
import os
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.base import LLMProvider
from providers.llm import OllamaProvider, get_llm_provider

logger = logging.getLogger(__name__)

VALID_SHOT_TYPES = {"远", "中", "近", "特写", "俯拍", "仰拍", "航拍", "跟随", "全景", "远景", "中景", "近景", "广角", "大特写"}

# 景别标准化映射
SHOT_TYPE_MAP = {
    "全景": "远", "远景": "远", "广角": "远", "大远景": "远",
    "中景": "中", "中近景": "中",
    "近景": "近",
    "特写": "特写", "大特写": "特写", "局部特写": "特写",
    "俯视": "俯拍", "俯瞰": "俯拍",
    "仰视": "仰拍", "低角度": "仰拍",
    "航拍": "航拍", "空中": "航拍",
    "跟拍": "跟随", "移动": "跟随", "跟随": "跟随",
}

STORYBOARD_SYSTEM_PROMPT = """你是一个专业的AI漫剧分镜师。请根据剧本生成详细、可量化、可直接出图的复杂分镜表。

⚠️ 绝对禁令：任何字段都不能用"有画面感""唯美""氛围好"这类空泛词。每一镜的 scene/action/background/lighting/dialogue/sd_prompt 都必须写成"能直接交给AI渲染引擎执行"的具体描述。宁长勿空。

输出格式为JSON数组（每个元素是一个分镜，6个字段一个都不能缺）：
[
  {
    "shot_id": 1,
    "scene": "具体场景（时间+地点+天气+光源，如：深夜老城区巷口，暖黄路灯，地面微湿反光）",
    "shot_type": "远/中/近/特写/俯拍/仰拍",
    "duration": 5,
    "camera_movement": "固定/推/拉/摇/移/跟 + 进距变化（如：缓慢前推，由中景推至近景）",
    "characters": ["角色1", "角色2"],
    "action": "角色具体动作+神态（如：林小满低头搅动咖啡，皱眉，抬头看陈默，抿嘴笑）。⚠️人物镜头必须写『情绪路线图』：按秒写清 眉毛/眼睛/嘴角/呼吸/停顿 五控制点的变化，禁止只写抽象情绪词（如"悲伤""尴尬"）。例：『0-2s 眉头轻蹙、低头；2-4s 眼睑微垂、睫毛颤动；4-5s 抿住嘴角、停顿半拍才抬眼』",
    "emotion": "本镜人物的主导情绪+强度档（10%/40%/80%），供微表情词库匹配（如：强忍泪水·40%）",
    "background": "具体可识别环境元素（如：木质吧台，悬垂暖光吊灯，墙上挂满黄铜器具）",
    "lighting": "具体光源（如：暖色侧逆光，光从窗户斜射，桌面有杯影）",
    "dialogue": "本分镜中的对白原文（无对白填\"\"）或旁白",
    "sd_prompt": "完整SD出图英文prompt：<主体,含外观年龄>, <动作>, <场景>, <风格词>, <光影>, <镜头词>, masterpiece, best quality, highly detailed",
    "sd_negative": "负面prompt（固定含 bad anatomy, extra hands, deformed face, missing fingers, blurry, watermark）",
    "transition": "cut/dissolve/fade/wipe"
  }
]

量化要求（严格按此写，禁止省略）：
1. shot_type 仅限：远、中、近、特写、俯拍、仰拍
2. duration 3-8秒：对白/情绪场景6-8秒，动作/转场场景3-4秒
3. sd_prompt 必须覆盖6层：①主体 ②动作 ③场景 ④风格 ⑤光影镜头 ⑥画质词；且主体要带外观（年龄/发型/服饰）让角色可辨识
4. sd_negative 必须含："bad anatomy, extra hands, deformed face, missing fingers, ugly, low quality, blurry, watermark"
5. 一集6-10个分镜，不超过10个
6. **镜头节奏**：每3-5个分镜换一次景别，避免连续3个同景别；开场远/全景建空间，对话中/近景，情绪转折特写
7. **剧情连贯**：相邻分镜动作/位置逻辑连续（A进屋→走到桌前→坐下说话），不跳场景、不自创剧情
8. **同场景背景一致**：同一间屋子 background 描述前后保持一致，不许变
9. **对白从剧本原样照抄**，不许改写、不许加戏
10. **镜头真实感**：sd_prompt 加入相机词（35mm, f/2.8, shallow depth of field, cinematic lighting），构图避免人物正中央摆拍，用三分法/侧面/背影/局部，像真实电影剧照而非插画
11. **情绪路线图（人物镜头必写）**：凡 characters 非空且 shot_type 为 近/特写/近景 的镜头，action 必须按"0-Ns 眉毛→眼睛→嘴角→呼吸→停顿"逐秒写出五控制点的变化，并填好 emotion 字段（主导情绪+强度档），禁止只用"感伤/愤怒/开心"这类抽象词
12. 必须返回合法JSON数组，不加任何注释或其他文字"""


# ── 分镜空字段兜底 ────────────────────────────

def _fill_shot_defaults(shot: dict) -> dict:
    """补全分镜缺失字段，并生成前端可读的 desc 摘要。

    目标：即便 LLM 某字段漏输出，也绝不让前端看到"只有景别的空壳分镜"。
    兜底逻辑：用 scene/action/background/dialogue 互相补全，再汇总成 desc。
    """
    scene = (shot.get("scene") or "").strip()
    action = (shot.get("action") or "").strip()
    background = (shot.get("background") or "").strip()
    lighting = (shot.get("lighting") or "").strip()
    dialogue = (shot.get("dialogue") or "").strip()

    # 1) scene 空 → 用 background 补；background 空 → 用 scene 补
    if not scene:
        shot["scene"] = background
        scene = background
    if not background:
        shot["background"] = scene
        background = scene

    # 2) action 空 → 用对白/场景推断（去掉引号后截取）
    if not action:
        action = dialogue.lstrip('"“').strip()
        shot["action"] = action

    # 3) lighting 空 → 给出通用棚拍/自然光兜底
    if not lighting:
        shot["lighting"] = "自然柔和光，浅景深"

    # 3.5) emotion 空 → 从 action/dialogue 里取原文作兜底（供 image_agent 匹配微表情词）
    #      否则 image_agent 拿不到 hint，只能注入中性表情，人物不够"有戏"
    if not (shot.get("emotion") or "").strip():
        _emo = (action or dialogue or "").strip()
        shot["emotion"] = _emo or "自然"

    # 4) sd_prompt 仍太弱 → 用 scene+action+background 拼一段英文
    prompt = (shot.get("sd_prompt") or "").strip()
    if not prompt or len(prompt) < 20:
        from config.style_resolver import style_keywords
        _kw = style_keywords()
        parts = [p for p in (action, scene, background) if p] + _kw + ["cinematic lighting", "shallow depth of field"]
        shot["sd_prompt"] = ", ".join(parts) + ", masterpiece, best quality, highly detailed"

    # 5) sd_negative 空 → 用当前风格的主负向词（config 的 styles.<风格>.neg_prompt），
    #    无则回退通用负面词。
    if not (shot.get("sd_negative") or "").strip():
        from config.style_resolver import style_neg_prompt
        shot["sd_negative"] = style_neg_prompt() or "bad anatomy, extra hands, deformed face, missing fingers, ugly, low quality, blurry, watermark"

    # 6) 生成前端可读 desc 摘要（combine scene/action/background/dialogue）
    desc_parts = []
    if scene:
        desc_parts.append(scene)
    if action:
        desc_parts.append(action)
    if dialogue:
        desc_parts.append(dialogue)
    if not desc_parts:
        desc_parts = ["（本镜无具体描述，需人工补充）"]
    shot["desc"] = "；".join(desc_parts)
    return shot


# ── 校验层 ────────────────────────────

class ShotValidator:
    """分镜校验层"""

    MAX_RETRIES = 3

    def normalize_shots(self, shots: list[dict]) -> list[dict]:
        """标准化所有分镜，修正 shot_type 等"""
        for shot in shots:
            st = shot.get("shot_type", "")
            if st in SHOT_TYPE_MAP:
                shot["shot_type"] = SHOT_TYPE_MAP[st]
            elif st not in VALID_SHOT_TYPES:
                shot["shot_type"] = "中"  # 默认回退
            # 确保 duration 合法
            dur = shot.get("duration", 0)
            if not isinstance(dur, (int, float)) or dur < 3 or dur > 12:
                shot["duration"] = 5
            # 确保 sd_prompt 存在
            prompt = shot.get("sd_prompt", "")
            if not prompt or len(prompt) < 20:
                prompt = shot.get("action", "") or shot.get("scene", "")
                shot["sd_prompt"] = prompt + ", masterpiece, best quality"
            # 补画质词
            if "masterpiece" not in prompt.lower() or "best quality" not in prompt.lower():
                shot["sd_prompt"] = prompt + ", masterpiece, best quality"
            # ── 空字段兜底：即便 LLM 漏了字段，也要用剧本/场景信息补全，避免"空壳分镜" ──
            _fill_shot_defaults(shot)
        return shots
    
    def validate(self, shots: list[dict]) -> list[dict]:
        """校验所有分镜，返回 warnings 列表（不再阻塞）"""
        warnings = []
        for i, shot in enumerate(shots):
            shot_id = shot.get("shot_id", i + 1)
            st = shot.get("shot_type", "")
            if st not in VALID_SHOT_TYPES:
                warnings.append({
                    "shot_id": shot_id,
                    "field": "shot_type",
                    "message": f"无效镜头类型: {st}，已自动修正",
                })
            dur = shot.get("duration", 0)
            if not isinstance(dur, (int, float)) or dur < 3 or dur > 12:
                warnings.append({
                    "shot_id": shot_id,
                    "field": "duration",
                    "message": f"无效时长: {dur}s，已自动修正",
                })
        return warnings

    def fix_prompt(self, shot: dict) -> dict:
        """自动补全缺少的prompt元素（如有需要）"""
        prompt = shot.get("sd_prompt", "")
        needed = {
            "主体": shot.get("action", ""),
            "场景": shot.get("background", ""),
            "光影": shot.get("lighting", ""),
        }
        for key, val in needed.items():
            if isinstance(val, str) and val and val not in prompt:
                prompt += f", {val}"
        shot["sd_prompt"] = prompt
        return shot


# ── Agent 2 ────────────────────────────

class StoryboardAgent(Agent):
    """Agent 2：分镜生成"""

    name = "storyboard_agent"

    def __init__(self, llm_provider: Union[str, LLMProvider] = "auto"):
        super().__init__(name="storyboard_agent")
        if isinstance(llm_provider, LLMProvider):
            # 用户显式传了 Provider 实例 → 直接用（方便测试 mock）
            self.llm = llm_provider
        else:
            # 分镜生成：结构化 JSON + 人物/景别/运镜推理多，优先用 V4-Pro（推理强、一致性好）
            # 模型名从 config.yaml engine.llm_model_storyboard 读，取不到就交给 Provider 默认
            from config.settings import settings
            default_model = getattr(settings.engine, "llm_model_storyboard", None)
            # 走统一工厂函数：自动按 config.yaml 的 engine.llm_provider 选择后端；
            # 若选了 deepseek 但没配 Key，会自动回退到 Ollama，不会抛错
            self.llm = get_llm_provider(llm_provider, default_model=default_model)
        self.validator = ShotValidator()

    async def run(self, script: dict) -> AgentResult:
        logger.info("[StoryboardAgent] 开始拆分镜")

        if not script or "episodes" not in script:
            return AgentResult(
                success=False,
                error="剧本数据不完整，缺少 episodes 字段",
            )

        all_episodes = []

        for ep in script.get("episodes", []):
            ep_input = json.dumps(ep, ensure_ascii=False, indent=2)
            characters_info = json.dumps(
                script.get("characters", []),
                ensure_ascii=False,
                indent=2,
            )

            prompt = (
                f"剧本：{ep_input}\n\n"
                f"角色列表：{characters_info}\n"
                f"请为本集生成分镜，要求合法的JSON数组。"
            )
            # 注入当前所选风格的量化关键词（config 的 styles.<风格>.keywords），
            # 让每个分镜的画面描述/光影贴合所选风格。
            from config.style_resolver import style_keywords, style_label
            _kw = style_keywords()
            if _kw:
                prompt += (
                    f"\n\n本片风格为「{style_label()}」。请在画面、背景、光影描述中自然融入："
                    f"{'、'.join(_kw)}。"
                )

            # 注入 Skills 知识库的微表情词汇，让人物镜头的 action 写出"有戏"的细节而非抽象词
            from skills.resolver import emotion_micro_block
            _emo = emotion_micro_block()
            if _emo:
                prompt += (
                    f"\n\n人物镜头的微表情可选词（写到 action 里，别整句照抄，选贴合的）：{_emo}。"
                    f"记住：好戏来自『克制』，一个细小动作往往胜过一整句'他很伤心'。"
                )

            raw = await self.llm.generate(
                prompt=prompt,
                system_prompt=STORYBOARD_SYSTEM_PROMPT,
                # 不指定 model，让各 Provider 用自己的默认模型
                # json_mode=True 强制 Ollama 输出合法 JSON 数组
                json_mode=True,
            )

            raw = raw.strip()
            # 用自愈解析器处理 LLM 输出（截断/杂质/尾逗号/围栏都能救回）。
            # 只有自愈也救不回时才抛异常 → retry_async 捕获后自动重试。
            from providers.json_repair import repair_json
            parsed = repair_json(raw)
            # 兼容 LLM 输出为 `{"shots":[...]}` 的包裹对象：取里面的 shots 数组
            if isinstance(parsed, dict):
                inner = (parsed.get("shots")
                         or parsed.get("data")
                         or parsed.get("storyboard"))
                if isinstance(inner, list):
                    shots = inner
                else:
                    shots = parsed
            else:
                shots = parsed
            if not isinstance(shots, list):
                logger.error(f"[Storyboard] LLM输出不是JSON数组，前200字符: {raw[:200]}")
                raise ValueError("LLM返回格式错误，不是合法JSON数组")

            # 防御：qwen3 偶发把分镜数组再包一层（[[{...}]]）或含非 dict 元素，
            # 扁平化并只保留 dict，避免后续 shot.get(...) 抛 'list' has no attribute 'get'。
            flat_shots = []
            for s in shots:
                if isinstance(s, dict):
                    flat_shots.append(s)
                elif isinstance(s, list):
                    for sub in s:
                        if isinstance(sub, dict):
                            flat_shots.append(sub)
            shots = flat_shots

            # 分镜数量限制：默认最多18个，可用 STORYBOARD_MAX_SHOTS 环境变量
            # 调小（如=4）用于快速验证管线，正式使用不设置即可
            max_shots = int(os.environ.get("STORYBOARD_MAX_SHOTS", "18"))
            if len(shots) > max_shots:
                logger.info(
                    f"[Storyboard] 分镜数 {len(shots)} 超过限制 {max_shots}，已截断"
                )
                shots = shots[:max_shots]

            # 标准化（修正shot_type等），不再硬校验阻止
            self.validator.normalize_shots(shots)
            warnings = self.validator.validate(shots)
            if warnings:
                logger.warning(f"[校验] 分镜有{warnings}处需修正，已自动处理")

            # 自动补全prompt
            for shot in shots:
                self.validator.fix_prompt(shot)

            all_episodes.append({
                "episode_number": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "shots": shots,
            })

        result = AgentResult(
            success=True,
            data={"episodes": all_episodes},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_shots": sum(
                    len(ep["shots"]) for ep in all_episodes
                ),
            },
        )

        logger.info(
            f"[StoryboardAgent] 完成: "
            f"{result.metadata['total_shots']}个分镜"
        )
        return result
