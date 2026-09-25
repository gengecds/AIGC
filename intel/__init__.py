"""AIGC intel 层 — 情报站核心能力封装（是否生效由 config.intel.enabled 决定，零破坏）。

将情报站的两个纯标准库引擎（爆款拆解 / 导演级二创脚本）复制为本地模块，
并提供轻量服务函数，供 research 等 agent 在 intel_enabled() 为真时调用，实现：
- 爆款视频逆向拆解 → 注入 research_agent 的 reference_cases（方案 §4 映射）
- 导演级二创脚本 → 供后续 agent 复用（P2/P3 扩展）

遵守「严格隔离 + 只增不改」红线：
- 不修改任何现有 agent/pipeline 逻辑；
- 关闭开关时 intel_enabled() == False，所有服务函数返回空/原样。
"""

from .benchmark import BenchmarkEngine
from .draft import DraftEngine
from .service import (
    intel_enabled,
    cases_dir,
    build_reference_case,
    iter_cases,
    load_reference_context,
)

__all__ = [
    "BenchmarkEngine",
    "DraftEngine",
    "intel_enabled",
    "cases_dir",
    "build_reference_case",
    "iter_cases",
    "load_reference_context",
]
