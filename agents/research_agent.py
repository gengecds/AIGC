"""Agent 0 - 需求研究（流程升级：先收集/分析，后写文案）

用户提出需求后，不直接写剧本，而是先产出「完整制作方案」：
- 分析需求 → 明确目标人群、风格方向、叙事结构
- 注入渲染引擎 / 光影强化关键词（影视级质感词库）
- 输出结构化方案 JSON，供后续 script/storyboard/image 消费

方案 JSON 示例：
{
  "title": "方案标题",
  "target_audience": "目标人群",
  "style_direction": "风格方向（写实/动漫/电影感…）",
  "narrative_structure": "叙事结构（悬念/温情/节奏…）",
  "key_words": ["关键词1", "关键词2"],
  "render_engine_words": ["UE5.3 Lumen 全局光照", "Nanite 虚拟几何体影视级资产"],
  "lighting_words": ["光线追踪反射", "路径追踪"],
  "camera_words": ["35mm, f/2.8, 浅景深"],
  "copy_points": ["文案要点1", "文案要点2"],
  "bgm_mood": "背景音乐情绪（温馨/激昂/悬疑…）",
  "scene_sounds": ["场景声效建议1", "煎锅滋滋声"]
}
"""

import json
import logging
import re
from datetime import datetime
from typing import Union

from agents.base import Agent, AgentResult
from providers.base import LLMProvider
from providers.llm import OllamaProvider

logger = logging.getLogger(__name__)

# ── 影视级质感词库（用户提供，全局注入）────────────────
# 渲染引擎 / 光影强化关键词：文案与出图 prompt 会按需组合引用。
# 词库从 config/style_library.yaml 加载（人可编辑），支持"自我进化"：
# 每次生成后 LLM 会建议候选新词，人工确认后追加到 YAML 即可，无需改代码。
# 保留模块级常量名（RENDER_ENGINE_WORDS 等），供 image_agent 等下游 import 兼容。
from providers.style_library import load_library

_style_lib = load_library()
RENDER_ENGINE_WORDS = _style_lib["render_engine_zh"]   # 渲染引擎中文词（方案展示/文案用）
LIGHTING_WORDS = _style_lib["lighting_zh"]             # 光影强化中文词
RENDER_ENGINE_EN = _style_lib["render_engine_en"]      # 渲染引擎英文词（出图 prompt 用）
LIGHTING_EN = _style_lib["lighting_en"]                # 光影强化英文词


RESEARCH_SYSTEM_PROMPT = """你是一个专业的影视广告策划。用户会提出一个创作需求（广告/短剧/动漫均可）。

你的任务：先做需求研究，输出一份完整的制作方案（JSON），不要直接写剧本。

输出格式（严格JSON）：
{
  "title": "方案标题",
  "target_audience": "目标人群分析",
  "style_direction": "风格方向（写实电影感/日漫/国漫/治愈/硬核等，一句话）",
  "narrative_structure": "叙事结构设计（如：悬念开场→冲突→反转→温暖收尾）",
  "key_words": ["3-6个核心关键词"],
  "render_engine_words": ["从给定词库挑选1-3个渲染引擎关键词"],
  "lighting_words": ["从给定词库挑选1-3个光影关键词"],
  "camera_words": ["镜头语言建议，如 35mm 浅景深、运动跟拍"],
  "copy_points": ["3-5条文案要点（要细、要具体、有画面感）"],
  "reference_cases": ["参考过的类似成品/素材（如收集阶段没有素材则为空数组；有素材时归纳其亮点与可借鉴处）"],
  "candidate_words": [{"zh": "建议新增的关键词", "en": "英文版（用于出图prompt）", "note": "适用场景"}],
  "bgm_mood": "背景音乐情绪（温馨/激昂/悬疑/清新/怀旧）",
  "scene_sounds": ["2-4个场景声效建议，如 煎锅滋滋声/雨声/车流声"]
}

要求：
1. 先分析需求适合什么人群、什么风格，再定叙事结构——方案要比用户原话更专业、更完整
2. render_engine_words 与 lighting_words 必须从用户消息中给定的词库选（不要自创）
3. copy_points 必须具体到"能直接指导分镜"的程度，避免空泛
4. 广告类需求：明确卖点、目标人群、转化引导
5. 如果用户消息里提供了"参考素材"（类似成品清单），必须阅读并归纳其亮点写入 reference_cases，
   并在方案中借鉴其优点；没有素材则 reference_cases 为空数组
6. candidate_words：如果现有词库无法完全覆盖需求的光影/质感表达，可建议 1-3 个新词
   （不能为空的数组；词库够用则为空数组）
7. 必须返回合法JSON，不要有其他文字"""


def _find_materials(user_input: str) -> str:
    """查找与本需求相关的已收集素材（storage/materials/），返回素材摘要文本。

    收集流程（由 scripts/collect_materials.py 生成）：
      需求提出后先用本地 Chrome 搜索类似成品，把竞品标题/简介/来源落盘
      storage/materials/{关键词}/summary.json。这里读取后注入 prompt，
      让方案真正"参考了现成类似成品"，而不是全靠 LLM 脑补。

    :param user_input: 用户需求原文
    :return: 素材摘要文本；无素材时返回空字符串
    """
    from pathlib import Path
    import re

    materials_root = Path(__file__).parent.parent / "storage" / "materials"
    if not materials_root.exists():
        return ""

    # 提取需求里的中文词片段，用于匹配素材目录名
    def _keywords(text: str) -> list:
        # 先取连续中文串（"给一家煎饺店做"），再滑动切成 2/3/4 字片段
        # （"煎饺店"这种 3 字词必须保留，否则匹配不到收集时的目录名）
        chunks = re.findall(r"[\u4e00-\u9fff]+", text)
        segs = set()
        for ch in chunks:
            if len(ch) < 2:
                continue
            segs.add(ch)  # 整串也算一个候选（如"煎饺店广告"完整词）
            for L in (2, 3, 4):
                for i in range(max(0, len(ch) - L + 1)):
                    segs.add(ch[i:i + L])
        return list(segs)[:16]  # 限制数量避免过拟合

    kw_list = _keywords(user_input)
    matched = []
    # 目录名包含任一关键词即视为相关（最多取 2 个目录，避免 prompt 过长）
    for d in sorted(materials_root.iterdir()):
        if not d.is_dir():
            continue
        if any(k in d.name for k in kw_list) or not kw_list:
            summary = d / "summary.json"
            if summary.exists():
                try:
                    import json
                    data = json.loads(summary.read_text(encoding="utf-8"))
                    lines = [
                        f"- {data.get('title','')}（来源:{data.get('source','')}）",
                        f"  简介: {data.get('desc','')}",
                    ]
                    matched.append(f"【素材目录: {d.name}】\n" + "\n".join(lines))
                except Exception:
                    pass
            if len(matched) >= 2:
                break
    if not matched:
        return ""
    return "\n\n".join(matched)


class ResearchAgent(Agent):
    """Agent 0：需求研究 → 制作方案"""

    name = "research_agent"

    def __init__(self, llm_provider: Union[str, LLMProvider] = "ollama"):
        super().__init__(name="research_agent")
        if isinstance(llm_provider, LLMProvider):
            self.llm = llm_provider
        elif str(llm_provider or "ollama").lower() in ("deepseek", "auto", "config", "ollama"):
            # 传的是字符串 provider 名（pipeline 里按 config.yaml 的 llm_provider=deepseek 传入）。
            # 走统一工厂：DeepSeek 无 Key/失败时会自动回退本地 Ollama，不会让研究步骤挂掉。
            from providers.llm import get_llm_provider
            self.llm = get_llm_provider(str(llm_provider))
        else:
            self.llm = OllamaProvider()

    async def run(self, user_input: str) -> AgentResult:
        logger.info(f"[ResearchAgent] 研究需求: {user_input[:50]}...")

        try:
            # ── 素材收集阶段：查找 storage/materials/ 里的类似成品素材 ──
            # 需求 ①：不是马上做，而是先参考现成类似成品。素材由
            # scripts/collect_materials.py（本地 Chrome CDP）提前收集落盘。
            materials_text = _find_materials(user_input)
            if materials_text:
                logger.info("[ResearchAgent] 发现参考素材，纳入方案研究")

            # ── 情报站增强（由 config.intel.enabled/INTEL_ENABLED 决定，零破坏）─────────
            # 开启时读取 storage/intel/cases/ 的情报案例，
            # 用 BenchmarkEngine 拆解后注入 reference_cases，让方案参考真实爆款。
            intel_context = ""
            try:
                from intel.service import load_reference_context
                intel_context = load_reference_context(user_input)
                if intel_context:
                    logger.info("[ResearchAgent] 读取情报站拆解结果，纳入方案研究")
            except Exception as _e:  # 情报增强失败不影响原路径
                logger.debug(f"[ResearchAgent] intel 增强不可用: {_e}")

            # 合并参考素材（collect_materials 文件 + 情报站拆解）成同一段参考输入
            ref_text = "\n\n".join(t for t in (materials_text, intel_context) if t)

            # 用户消息 = 原始需求 + 词库（供挑选，不注入 system prompt 避免被覆盖）
            library_text = (
                "词库（从中挑选 render_engine_words / lighting_words）\n"
                f"渲染引擎: {' / '.join(RENDER_ENGINE_WORDS)}\n"
                f"光影强化: {' / '.join(LIGHTING_WORDS)}\n"
            )
            prompt = (
                f"创作需求：{user_input}\n\n"
                f"{library_text}\n"
                + (f"参考素材（类似成品清单，请归纳亮点写入 reference_cases）：\n{ref_text}" if ref_text else "")
            )

            raw = await self.llm.generate(
                prompt=prompt,
                system_prompt=RESEARCH_SYSTEM_PROMPT,
                json_mode=True,
            )
            raw = raw.strip()
            # 用自愈解析器处理 LLM 输出：截断/杂质/尾逗号/围栏都能救回。
            # 若自愈也救不回会抛 ValueError，被下方 except 兜底成默认方案（不阻塞管线）。
            from providers.json_repair import repair_json
            plan = repair_json(raw)

            # 兜底补全必填字段（LLM 漏输出时）
            plan.setdefault("title", user_input[:20])
            plan.setdefault("target_audience", "")
            plan.setdefault("style_direction", "电影感写实")
            plan.setdefault("narrative_structure", "")
            plan.setdefault("key_words", [])
            plan.setdefault("render_engine_words", RENDER_ENGINE_WORDS[:2])
            plan.setdefault("lighting_words", LIGHTING_WORDS[:2])
            plan.setdefault("camera_words", [])
            plan.setdefault("copy_points", [])
            # 进化机制新增字段：参考成品 + 候选新词（词库够用时为 [])
            plan.setdefault("reference_cases", [])
            plan.setdefault("candidate_words", [])
            plan.setdefault("bgm_mood", "温馨")
            plan.setdefault("scene_sounds", [])
            # 记录是否有素材被消费（metadata，前端可展示"参考了 N 个成品"）
            meta_extra = {}
            if materials_text:
                meta_extra["materials_used"] = True
            if intel_context:
                meta_extra["intel_used"] = True

            result = AgentResult(
                success=True,
                data=plan,
                metadata={
                    "agent": self.name,
                    "timestamp": datetime.utcnow().isoformat(),
                    "title": plan.get("title", ""),
                    **meta_extra,
                },
            )
            logger.info(f"[ResearchAgent] 方案完成: {plan.get('title')}")
            return result

        except Exception as e:
            logger.error(f"[ResearchAgent] 错误: {e}")
            # 研究失败不阻塞管线：返回一个默认方案兜底
            return AgentResult(
                success=True,
                data={
                    "title": user_input[:20],
                    "target_audience": "",
                    "style_direction": "电影感写实",
                    "narrative_structure": "",
                    "key_words": [],
                    "render_engine_words": RENDER_ENGINE_WORDS[:2],
                    "lighting_words": LIGHTING_WORDS[:2],
                    "camera_words": [],
                    "copy_points": [],
                    "reference_cases": [],
                    "candidate_words": [],
                    "bgm_mood": "温馨",
                    "scene_sounds": [],
                },
                metadata={"agent": self.name, "timestamp": datetime.utcnow().isoformat()},
            )
