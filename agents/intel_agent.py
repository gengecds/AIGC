"""IntelligenceAgent — 情报采集 + 爆款拆解前置节点（是否执行由 INTEL_ENABLED/config.intel.enabled 决定，零破坏）。

作用：作为管线第一个节点，把「情报站」能力前置到 AIGC。
- 从情报源读取作品素材（storage/intel/source/ 或 AIGC 自有的 storage/materials/）；
- 用 BenchmarkEngine 做爆款逆向拆解 → 生成结构化 reference_cases；
- 落盘到 storage/intel/cases/（P1 的 research_agent 已通过 load_reference_context 自动读取）。

零破坏红线（见方案文档 §7.1）：
- 仅当 intel_enabled() 为 True 时，调度器才会保留并执行本节点；关闭时按开关跳过，管线走原始路径；
- 无情报源时返回 success(count=0)，绝不阻断后续节点；
- 纯标准库逻辑，不依赖 LLM/GPU/外部采集脚本，mock 与真实模式均可运行。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from agents.base import Agent, AgentResult
from intel.benchmark import BenchmarkEngine

logger = logging.getLogger(__name__)

_STORAGE = Path(__file__).parent.parent / "storage"


class IntelligenceAgent(Agent):
    """情报前置 Agent：读取素材 → 爆款拆解 → 落盘 reference_cases。"""

    def __init__(
        self,
        name: str = "intel_agent",
        model_provider: str | None = None,
        source_dir: str | None = None,
        cases_dir: str | None = None,
    ):
        super().__init__(name=name, model_provider=model_provider)
        self._source_dir = Path(source_dir) if source_dir else None
        self._cases_dir = Path(cases_dir) if cases_dir else None

    # ── 路径解析（支持 config 覆盖，默认落 storage/intel/）─
    def _resolve_source_dir(self) -> Path:
        if self._source_dir:
            return self._source_dir
        try:
            from config.settings import settings
            d = getattr(getattr(settings, "intel", None), "source_dir", None)
            if d:
                return Path(d)
        except Exception:
            pass
        return _STORAGE / "intel" / "source"

    def _resolve_cases_dir(self) -> Path:
        if self._cases_dir:
            return self._cases_dir
        try:
            from config.settings import settings
            d = getattr(getattr(settings, "intel", None), "cases_dir", None)
            if d:
                return Path(d)
        except Exception:
            pass
        return _STORAGE / "intel" / "cases"

    # ── 情报源收集 ────────────────────────────
    def _iter_source(self) -> list[dict]:
        """从「专用情报源 + AIGC 自有素材」收集原始作品素材。"""
        items: list[dict] = []

        # 1. 专用情报源 storage/intel/source/*.json（供未来采集脚本写入）
        src = self._resolve_source_dir()
        if src.exists():
            for p in sorted(src.glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(d, dict):
                        d["_src"] = str(p)
                        items.append(d)
                except Exception as e:
                    logger.warning(f"[IntelAgent] source 解析失败 {p}: {e}")

        # 2. AIGC 自有素材 storage/materials/{keyword}/summary.json
        mat_dir = _STORAGE / "materials"
        if mat_dir.exists():
            for p in sorted(mat_dir.glob("*/summary.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(d, dict):
                        d.setdefault("video_meta", {})
                        d["_src"] = str(p)
                        items.append(d)
                except Exception as e:
                    logger.warning(f"[IntelAgent] materials 解析失败 {p}: {e}")
        return items

    @staticmethod
    def _to_video_meta(raw: dict) -> dict:
        meta = dict(raw.get("video_meta") or {})
        meta.setdefault("title", raw.get("title") or raw.get("video_title") or "")
        meta.setdefault("creator_name", raw.get("creator_name") or raw.get("creator") or "")
        meta.setdefault("platform", raw.get("platform") or "未知")
        return meta

    @staticmethod
    def _to_transcript(raw: dict) -> str:
        return (
            raw.get("transcript")
            or raw.get("summary")
            or raw.get("原文")
            or raw.get("content")
            or ""
        )

    def _safe_slug(self, text: str, idx: int) -> str:
        base = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", text).strip("_") or "case"
        return f"{base}_{idx}"

    async def run(self, user_input: str, *args: Any, **kwargs: Any) -> AgentResult:
        items = self._iter_source()
        if not items:
            logger.info("[IntelAgent] 无情报源（storage/intel/source 与 storage/materials 均为空），节点空跑")
            return AgentResult(
                success=True,
                data={"cases": [], "count": 0, "note": "无情报源"},
            )

        cases: list[dict] = []
        cases_dir = self._resolve_cases_dir()
        try:
            cases_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[IntelAgent] 无法创建 cases 目录 {cases_dir}: {e}")

        for idx, raw in enumerate(items, 1):
            meta = self._to_video_meta(raw)
            transcript = self._to_transcript(raw)
            if not meta.get("title") and not transcript:
                continue

            analysis = BenchmarkEngine.analyze(meta, transcript)
            cases.append({
                "title": analysis.get("title", ""),
                "summary": analysis.get("hook", {}).get("promise", ""),
                "highlights": analysis.get("handoff", {}).get("keyTakeaways", [])[:3],
                "audience_feedback": "",
                "transcript": transcript,
            })

            # 落盘为 P1 research_agent 的 load_reference_context 可读格式
            slug = self._safe_slug(analysis.get("title", ""), idx)
            out = cases_dir / f"{slug}.json"
            try:
                out.write_text(
                    json.dumps({"video_meta": meta, "transcript": transcript},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.warning(f"[IntelAgent] 落盘 {out} 失败: {e}")

        data = {"cases": cases, "count": len(cases)}
        if cases:
            data["note"] = f"已拆解 {len(cases)} 条情报并注入 reference_cases"
        logger.info(f"[IntelAgent] 前置情报完成: 拆解 {len(cases)} 条")
        return AgentResult(success=True, data=data)
