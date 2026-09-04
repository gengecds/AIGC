"""Intel API 路由 — 情报看板（P3，新增路由入口，零破坏）。

提供只读接口，从 AIGC 自有 `storage/intel/cases/` 读取 P2 IntelligenceAgent 落盘的情报案例，
用 BenchmarkEngine 拆解出「选题库」，供前端 /intelligence 看板展示；
「一键推入管线」由前端复用现有 `/api/v1/pipeline/run` 完成（本模块不新增执行逻辑）。

说明：
- 本模块是**新增**，不修改任何现有路由/业务逻辑，关闭开关或删除它即可完整还原；
- 情报数据来自 AIGC 自有 storage/intel/cases/，与外部情报站项目**零耦合**；
- 无数据时返回空列表，不影响现有功能。
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from intel.benchmark import BenchmarkEngine
from intel.service import cases_dir as _cases_dir

logger = logging.getLogger(__name__)

# 编译期兜底（正常以 config 覆盖为准，见 intel.service.cases_dir）
_DEFAULT_CASES_DIR = Path(__file__).parent.parent.parent / "storage" / "intel" / "cases"


# ── 数据读取（只读）─────────────────────────────

def _safe_slug(text: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", text).strip("_") or "case"
    return base


def _iter_case_files() -> list[Path]:
    """遍历情报案例 JSON。优先取 config 覆盖目录，否则取默认。"""
    try:
        root = _cases_dir()
    except Exception:
        root = _DEFAULT_CASES_DIR
    if not root or not Path(root).exists():
        return []
    return sorted(Path(root).glob("*.json"))


def _load_case(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception as e:
        logger.warning(f"intel case 解析失败 {path}: {e}")
        return None


# ── 拆解 & 选题生成 ──────────────────────────────

def _short(text: Any, limit: int = 90) -> str:
    """截断超长文本并省略号收尾，避免看板/创作输入被整段逐字稿撑爆。"""
    if not isinstance(text, str):
        return ""
    t = text.strip()
    if not t:
        return ""
    return t if len(t) <= limit else f"{t[:limit]}…"


def _case_to_view(data: dict[str, Any]) -> dict[str, Any]:
    """把一条情报案例拆解成看板可展示的结构 + 可延展选题。"""
    meta = data.get("video_meta", {}) or {}
    transcript = data.get("transcript", "") or data.get("summary", "") or ""
    a = BenchmarkEngine.analyze(meta, transcript)

    hook = a.get("hook", {}) or {}
    handoff = a.get("handoff", {}) or {}
    struct = a.get("structureProgression", {}) or {}
    dos = a.get("dosAndDonts", {}) or {}

    takeaways = handoff.get("keyTakeaways", []) or []
    borrow = dos.get("borrow", []) or []
    template = handoff.get("recommendedTemplate", "") or "教程型收藏闭环"
    mode = handoff.get("recommendedMode", "") or "SHORT"
    # 引擎对单行转录会把整段当一句塞进 contradiction/promise，这里收敛为短摘要
    contradiction = _short(handoff.get("coreContradiction", ""), 60)
    promise = _short(hook.get("promise", "") or a.get("title", ""), 90)
    # 亮点兜底：可复用机制不足时，退回「必抄袭资产」清单
    highlights = takeaways[:3] or borrow[:3]

    # 生成「选题库」条目（每个案例生成一个主选题）
    topic = {
        "id": _safe_slug(a.get("title", "")),
        "title": template,
        "mode": mode,
        "template": template,
        "contradiction": contradiction,
        "promise": promise,
        "takeaways": highlights,
        "platform": a.get("platform", ""),
        "source_title": a.get("title", ""),
        "hook_type": hook.get("type", ""),
    }
    topic["input"] = _build_topic_input(topic)

    return {
        "title": a.get("title", ""),
        "creator": a.get("creator", ""),
        "platform": a.get("platform", ""),
        "summary": promise,
        "highlights": highlights,
        "hook_type": hook.get("type", ""),
        "mode": mode,
        "template": template,
        "contradiction": contradiction,
        "beats": struct.get("beats", []) or [],
        "borrow": borrow[:3] or [],
        "avoid": dos.get("avoid", [])[:2] or [],
        "topics": [topic],
    }


def _build_topic_input(topic: dict[str, Any]) -> str:
    """把选题预生成为「一键推入管线」的创作输入。"""
    parts = [f"创作一个「{topic.get('template') or '爆款'}」主题的漫剧视频"]
    if topic.get("contradiction"):
        parts.append(f"核心矛盾：{topic['contradiction']}")
    if topic.get("promise"):
        parts.append(f"开场钩子承诺：{topic['promise']}")
    tips = topic.get("takeaways") or []
    if tips:
        parts.append("可复用机制：" + "；".join(tips))
    return "。".join(parts)


def list_cases() -> list[dict[str, Any]]:
    views = []
    for p in _iter_case_files():
        data = _load_case(p)
        if data is None:
            continue
        views.append(_case_to_view(data))
    return views


def list_topics() -> list[dict[str, Any]]:
    topics = []
    for v in list_cases():
        topics.extend(v.get("topics", []))
    return topics


# ── 路由注册 ───────────────────────────────────

def register_intel_routes(app):
    @app.get("/api/v1/intel/cases")
    async def get_intel_cases():
        """情报案例看板列表（只读）。无数据返回空数组。"""
        return {"cases": list_cases(), "count": len(list_cases())}

    @app.get("/api/v1/intel/topics")
    async def get_intel_topics():
        """选题库列表（只读）。每项含预生成的创作输入 input，可直接推入管线。"""
        topics = list_topics()
        return {"topics": topics, "count": len(topics)}
