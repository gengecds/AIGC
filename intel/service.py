"""intel 服务层 — 把情报站的分析能力桥接到 AIGC（零破坏，默认关闭）。

职责：
- 依据 `INTEL_ENABLED` 开关（config.yaml `intel.enabled`，默认 False）决定是否生效；
- 开启时从 `storage/intel/cases/` 读取「作品元数据 + 转写文本」的情报案例，
  用 BenchmarkEngine 拆解成结构化参考素材，注入 research_agent 的 prompt，
  让 reference_cases 不再是 LLM 脑补；
- 关闭时（默认）所有函数返回空值/原样，对现有管线零影响。

输入约定：每个情报案例是一个 .json 文件，形如：
{
  "video_meta": {"title": "…", "creator_name": "…", "platform": "…", "duration_seconds": 120},
  "transcript": "转写逐字稿…"
}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .benchmark import BenchmarkEngine

logger = logging.getLogger(__name__)

_DEFAULT_CASES_DIR = Path(__file__).parent.parent / "storage" / "intel" / "cases"


def intel_enabled() -> bool:
    """是否开启情报能力（默认关闭）。读取 config.settings.intel.enabled。"""
    try:
        from config.settings import settings

        val = getattr(settings, "intel", None)
        if val is None:
            return False
        return bool(getattr(val, "enabled", False))
    except Exception:
        return False


def cases_dir() -> Path:
    """情报案例目录（默认 storage/intel/cases/，可用 config 覆盖）。"""
    try:
        from config.settings import settings

        val = getattr(settings, "intel", None)
        d = getattr(val, "cases_dir", None) if val is not None else None
        if d:
            return Path(d)
    except Exception:
        pass
    return _DEFAULT_CASES_DIR


def build_reference_case(video_meta: dict[str, Any], transcript_text: str) -> dict[str, Any]:
    """用 BenchmarkEngine 拆解单条视频，映射为 reference_cases 的一项。

    映射规则（见方案文档 §4）：
      title / summary / highlights / audience_feedback / transcript
    """
    analysis = BenchmarkEngine.analyze(video_meta or {}, transcript_text or "")
    key_takeaways = analysis.get("handoff", {}).get("keyTakeaways", []) or []
    return {
        "title": analysis.get("title", ""),
        "summary": analysis.get("hook", {}).get("promise", "") or analysis.get("title", ""),
        "highlights": key_takeaways[:3],
        "audience_feedback": "",
        "transcript": transcript_text or "",
        "_analysis": analysis,
    }


def iter_cases(limit: int = 3) -> list[dict[str, Any]]:
    """读取情报案例 JSON（作品元数据 + 转写），返回解析后的 dict 列表。"""
    root = cases_dir()
    if not root.exists():
        return []
    cases = []
    for p in sorted(root.glob("*.json"))[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cases.append(data)
        except Exception as e:
            logger.warning(f"intel 案例解析失败 {p}: {e}")
    return cases


def load_reference_context(user_input: str | None = None, limit: int = 3) -> str:
    """开启时读取情报案例并拆解，返回可注入 research prompt 的参考素材文本。

    关闭时或目录为空时返回空字符串（与现有 collect_materials 的"无素材返回空串"一致）。
    """
    if not intel_enabled():
        return ""
    cases = iter_cases(limit=limit)
    if not cases:
        return ""

    blocks = []
    for i, case in enumerate(cases, 1):
        meta = case.get("video_meta", {}) or {}
        transcript = case.get("transcript", "") or case.get("summary", "")
        analysis = BenchmarkEngine.analyze(meta, transcript)
        title = analysis.get("title", "未命名")
        creator = analysis.get("creator", "")
        platform = analysis.get("platform", "")
        hook = analysis.get("hook", {})
        takeaways = analysis.get("handoff", {}).get("keyTakeaways", []) or []

        lines = [
            f"【情报案例 {i}】{title}（来源:{platform} · {creator}）",
            f"  亮点/可借鉴: {hook.get('type', '')} — {hook.get('promise', '')}",
        ]
        for tk in takeaways[:3]:
            lines.append(f"  - 可复用点: {tk}")
        lines.append(f"  完整逐字稿参考: {transcript[:200]}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)
