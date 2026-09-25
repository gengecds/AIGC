"""Intel API 路由 — 情报看板（P3，新增路由入口，零破坏）。

提供接口，从 AIGC 自有 `storage/intel/cases/` 读取 P2 IntelligenceAgent 落盘的情报案例，
用 BenchmarkEngine 拆解出「选题库」，供前端 /intelligence 看板展示；
已打通「自动采集链路」：前端可触发一键全平台采集 → 落盘 source → 重跑拆解 → 刷新看板。
「一键推入管线」由前端复用现有 `/api/v1/pipeline/run` 完成（本模块不新增执行逻辑）。

说明：
- 本模块是**新增**，不修改任何现有路由/业务逻辑，关闭开关或删除它即可完整还原；
- 情报数据来自 AIGC 自有 storage/intel/cases/，与外部情报站项目**零耦合**；
- 采集依赖本地环境（Chrome CDP 9222 / yt-dlp / FunASR），未就绪时脚本失败会回传 stderr，
  不影响本路由的只读接口（cases/topics）。
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from intel.benchmark import BenchmarkEngine
from intel.service import cases_dir as _cases_dir

# 服务端关闭信号：SSE 长连接据此主动收尾、并杀掉采集子进程（见 api/shutdown.py）
from api.shutdown import INTERRUPTED, wait_or_shutdown

logger = logging.getLogger(__name__)

# 编译期兜底（正常以 config 覆盖为准，见 intel.service.cases_dir）
_DEFAULT_CASES_DIR = Path(__file__).parent.parent.parent / "storage" / "intel" / "cases"

# 采集脚本目录（scripts/intel/）与项目根，供 subprocess 调用
_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts" / "intel"
_PROJECT_ROOT = _SCRIPTS_DIR.parent.parent

# ── 自动采集链路（触发式，前端通过 POST /api/v1/intel/collect 调用）──

class CollectRequest(BaseModel):
    """一键全平台采集的参数（与 download_all_platform_latest.py 对齐）。"""
    keyword: str = "美食"
    limit: int = 8
    with_audio: bool = False
    platforms: str = "douyin,xiaohongshu,youtube"
    # 是否在采集后跑 ASR 回填 transcript（需已下载音频 + FunASR 模型，默认关闭）
    transcribe: bool = False


def _run_script(script_name: str, args: list[str]) -> dict[str, Any]:
    """同步运行 scripts/intel/{script_name} 子进程，返回结构化结果。

    阻塞当前线程，调用方需用 asyncio.to_thread 包裹避免卡住事件循环。
    """
    script = _SCRIPTS_DIR / script_name
    if not script.exists():
        return {"ok": False, "error": f"脚本不存在: {script_name}"}
    cmd = [sys.executable, str(script), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_PROJECT_ROOT))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": (r.stdout or "")[-3000:],
        "stderr": (r.stderr or "")[-3000:],
    }


def _ensure_cdp_chrome() -> dict[str, Any]:
    """确保专用持久浏览器在线：调用 scripts/intel/cdp_chrome.py。

    这是「登录一次、后续免登录」的关键——它用固定的持久 profile（storage/intel/chrome-profile）
    拉起 Chrome(CDP 9222)，登录态存进该 profile 目录，之后采集脚本自动复用，不再重复登录。

    已在线（含复用历史会话）返回 ok=True；拉起失败返回 ok=False 并把原因回传，不阻断整条链路。
    """
    try:
        r = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "cdp_chrome.py")],
            capture_output=True, text=True, cwd=str(_PROJECT_ROOT), timeout=60,
        )
        ok = r.returncode == 0
        return {"ok": ok, "stdout": (r.stdout or "")[-1500:], "stderr": (r.stderr or "")[-1500:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _clear_dir(directory: Path, pattern: str = "*.json") -> int:
    """清空目录中匹配的文件，保留目录本身。返回删除数（用于每次采集前清空旧数据）。"""
    if not directory.exists():
        return 0
    n = 0
    for p in directory.glob(pattern):
        try:
            p.unlink()
            n += 1
        except Exception as e:
            logger.warning(f"清理旧文件失败 {p}: {e}")
    return n


def _intel_source_dir() -> Path:
    """采集脚本落盘的素材目录 storage/intel/source。"""
    return _PROJECT_ROOT / "storage" / "intel" / "source"


def _intel_cases_dir() -> Path:
    """情报案例目录：优先 config 覆盖，否则默认（与 _iter_case_files 保持一致）。"""
    try:
        root = _cases_dir()
    except Exception:
        root = _DEFAULT_CASES_DIR
    return Path(root) if root else _DEFAULT_CASES_DIR


async def _stream_script(script_name: str, args: list[str]):
    """异步逐行运行 scripts/intel/{script_name} 子进程，产出事件 dict（供 SSE 展示实时进度）。

    关键点：以 `-u` + `PYTHONUNBUFFERED=1` 开启无缓冲，子进程（download_douyin 等）也继承该环境，
    这样各采集脚本 print 的「正在抓取xx平台/命中xx条」能逐行推到前端，不再是一屏「采集中…」。
    产出形如：{"type": "log", "msg": "..."} 与 {"type": "exit", "code": int}。
    """
    script = _SCRIPTS_DIR / script_name
    if not script.exists():
        yield {"type": "log", "msg": f"[错误] 脚本不存在: {script_name}"}
        yield {"type": "exit", "code": 1}
        return
    cmd = [sys.executable, "-u", str(script), *args]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(_PROJECT_ROOT),
        env=env,
    )
    assert proc.stdout is not None
    try:
        while True:
            raw = await wait_or_shutdown(proc.stdout.readline())
            if raw is INTERRUPTED:
                # 服务端关闭：交给 finally 杀掉子进程，收尾后结束本次流
                yield {"type": "shutdown"}
                return
            if not raw:  # EOF：子进程输出结束
                break
            for seg in raw.decode("utf-8", "replace").splitlines():
                if seg.strip():
                    yield {"type": "log", "msg": seg.strip()}
        code = await proc.wait()
    finally:
        # 非正常结束（服务端关闭 / 外层硬 cancel）：收掉子进程，避免留孤儿
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    yield {"type": "exit", "code": code}


def _collect_dl_args(kw: str, limit: int, with_audio: bool, platforms: str) -> list[str]:
    """组 download_all_platform_latest.py 的 CLI 参数。"""
    args = [kw, "--limit", str(limit)]
    if with_audio:
        args.append("--with-audio")
    if platforms:
        args += ["--platforms", platforms]
    return args


async def _collect_stream_events(req: CollectRequest):
    """采集的 SSE 事件流：确保浏览器 → 逐行采集 → (可选 ASR) → 重跑拆解 → done。"""
    def evt(obj):
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    # 0) 确保专用持久浏览器在线（登录态复用：已登录过则免登录直接用）
    yield evt({"type": "status", "msg": "正在确保专用持久浏览器在线…"})
    chrome = await asyncio.to_thread(_ensure_cdp_chrome)
    yield evt({"type": "chrome", "ok": bool(chrome.get("ok"))})

    # 0.5) 每次采集前清空旧数据，确保看板只展示最新一批（用户选定「先清理」方式）
    cleared = await asyncio.to_thread(_clear_dir, _intel_source_dir())
    yield evt({"type": "status", "msg": f"已清空旧数据（{cleared} 条） · 开始最新一批采集…"})

    # 1) 采集（逐行推进度）
    yield evt({"type": "status", "msg": "开始采集（逐平台抓取素材）…"})
    dl_code = 0
    async for item in _stream_script("download_all_platform_latest.py", _collect_dl_args(req.keyword, req.limit, req.with_audio, req.platforms)):
        if item.get("type") == "shutdown":
            yield evt({"type": "done", "ok": False, "msg": "服务端关闭，采集已中断"})
            return
        if item.get("type") == "exit":
            dl_code = int(item.get("code", 0))
        yield evt(item)

    # 2) ASR 回填 transcript（可选，需已下载音频 + FunASR）
    if req.transcribe:
        yield evt({"type": "status", "msg": "正在做 ASR 转写回填 transcript（较慢，请耐心）…"})
        async for item in _stream_script("postprocess_platform_videos.py", ["--limit", str(req.limit)]):
            if item.get("type") == "shutdown":
                yield evt({"type": "done", "ok": False, "msg": "服务端关闭，采集已中断"})
                return
            yield evt(item)

    # 3) 重跑拆解，把最新 source 落盘为 cases
    yield evt({"type": "status", "msg": "正在重跑拆解、刷新选题库…"})
    step = await rebuild_cases()
    ok = bool(step.get("ok"))
    yield evt({
        "type": "done",
        "ok": ok,
        "count": int(step.get("count", 0)),
        "download_code": dl_code,
        "cases": list_cases(),
        "topics": list_topics(),
    })


async def rebuild_cases() -> dict[str, Any]:
    """重跑 IntelligenceAgent，把 storage/intel/source 重新拆解、落盘 cases。

    关键：把 cases 目录当作「当前 source 的全量拆解结果」，每次重建前先清空旧 case 文件，
    这样看板卡片恒等于当前素材库（最新一批）的全量拆解，不再混入历次采集的旧卡片。
    不依赖浏览器/模型，纯标准库逻辑；情报源为空时返回 count=0（不阻断）。
    """
    cases_root = _intel_cases_dir()
    try:
        cases_root.mkdir(parents=True, exist_ok=True)
        _clear_dir(cases_root)
    except Exception as e:
        logger.warning(f"[intel] 清空 cases 失败: {e}")

    from agents.intel_agent import IntelligenceAgent

    agent = IntelligenceAgent()
    result = await agent.run("")
    data = result.data or {}
    return {"ok": bool(result.success), "count": int(data.get("count", 0)),
            "note": str(data.get("note", ""))}


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
    # 标题用真实爆款标题（看板展示），template 作为「叙事框架」标签与推入管线时的副参考。
    real_title = a.get("title", "") or template
    topic = {
        "id": _safe_slug(real_title),
        "title": real_title,
        "mode": mode,
        "template": template,
        "contradiction": contradiction,
        "promise": promise,
        "takeaways": highlights,
        "platform": a.get("platform", ""),
        "source_title": real_title,
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
    """把选题预生成为「一键推入管线」的创作输入。

    以真实爆款标题为创作主体，template 作为叙事框架，再补上矛盾/承诺/可复用机制。
    """
    title = topic.get("source_title") or topic.get("title") or "爆款"
    parts = [f"创作一个类似《{title}》的爆款漫剧视频"]
    tpl = topic.get("template")
    if tpl:
        parts.append(f"叙事框架：{tpl}")
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

    @app.post("/api/v1/intel/rebuild")
    async def intel_rebuild():
        """仅重跑拆解：把 storage/intel/source 重新拆解、刷新 cases 与选题库（不采集、不依赖浏览器）。

        适用于：已有素材但看板数据过期，或采集脚本在外部已跑完、只差落盘拆解。
        """
        step = await rebuild_cases()
        return {
            "ok": step["ok"],
            "steps": {"rebuild_cases": step},
            "cases": list_cases(),
            "topics": list_topics(),
        }

    @app.get("/api/v1/intel/collect/stream")
    async def intel_collect_stream(
        keyword: str = "美食",
        limit: int = 8,
        with_audio: bool = False,
        transcribe: bool = False,
        platforms: str = "douyin,xiaohongshu,youtube",
    ):
        """一键全平台采集（实时进度流，SSE）。

        与 POST /collect 等价，但通过 Server-Sent Events 逐行把采集脚本输出推给前端，
        让看板显示「正在采集xx平台 / 命中xx条」等实时进度，而不是一屏「采集中…」。
        事件类型：status(log)/chrome/done。
        """
        req = CollectRequest(keyword=keyword, limit=limit, with_audio=with_audio,
                             transcribe=transcribe, platforms=platforms)
        return StreamingResponse(
            _collect_stream_events(req),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/intel/collect")
    async def intel_collect(req: CollectRequest):
        """一键全平台采集：下载 → （可选 ASR 回填 transcript）→ 重跑拆解 → 返回最新看板。

        采集依赖本地 Chrome CDP(9222)、yt-dlp 等，环境未就绪时对应步骤会失败并回传 stderr，
        不会影响后续拆解与只读看板。单步失败用 ok 标记，整体 ok 以「拆解成功」为准。
        """
        steps: dict[str, Any] = {}

        # 0) 确保专用持久浏览器在线（登录态复用：已登录过则免登录直接用）
        steps["chrome"] = await asyncio.to_thread(_ensure_cdp_chrome)

        # 0.5) 每次采集前清空旧数据，确保看板只展示最新一批
        steps["clear"] = {"ok": True, "removed": await asyncio.to_thread(_clear_dir, _intel_source_dir())}

        # 1) 采集（subprocess 包线程，避免阻塞事件循环）
        dl_args = [req.keyword, "--limit", str(req.limit)]
        if req.with_audio:
            dl_args.append("--with-audio")
        if req.platforms:
            dl_args += ["--platforms", req.platforms]
        steps["download"] = await asyncio.to_thread(_run_script, "download_all_platform_latest.py", dl_args)

        # 2) ASR 回填 transcript（可选，需已下载音频 + FunASR）
        if req.transcribe:
            steps["transcribe"] = await asyncio.to_thread(
                _run_script, "postprocess_platform_videos.py", ["--limit", str(req.limit)]
            )

        # 3) 重跑拆解，把最新 source 落盘为 cases（情报源已更新）
        steps["rebuild_cases"] = await rebuild_cases()

        ok = bool(steps.get("rebuild_cases", {}).get("ok", False))
        return {
            "ok": ok,
            "steps": steps,
            "cases": list_cases(),
            "topics": list_topics(),
        }
