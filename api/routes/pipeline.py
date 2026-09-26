"""Pipeline API 路由 — 完整版

所有 route 直接挂载到 app，不通过 APIRouter（避免 fastapi include_router 路径问题）
"""

import json, os, time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent
from agents.image_agent import ImageGenAgent
from agents.video_agent import VideoGenAgent
from agents.subtitle_agent import SubtitleAgent
from pipeline.scheduler import Pipeline
from db.database import get_session
from db.models import Story, PipelineJob

# 向导事件总线：/pipeline/events?run_id=xxx 的发布端。
# 真实管线把「审核断点到达 / 整线结束」翻译成 5 步向导能渲染的事件后发布到这里。
from api.routes.pipeline_events_router import publish_pipeline_event

# 服务端关闭信号：SSE 长连接据此主动收尾（见 api/shutdown.py）
from api.shutdown import INTERRUPTED, shutdown_event, wait_or_shutdown

logger = logging.getLogger(__name__)

# ── 活跃 Pipeline 追踪 ────────────────────
_active: dict[str, dict] = {}
import asyncio
_ws_clients: list[WebSocket] = []
_pipeline_instances: dict[str, object] = {}  # pipeline_id → Pipeline instance (for approve/reject)
_pipeline_tasks: dict[str, asyncio.Task] = {}  # pipeline_id → 后台任务句柄（取消时真正中断执行）
_sse_clients: list[asyncio.Queue] = []  # SSE 客户端队列（模块级，供 _execute 等广播）

# ── Pydantic ──────────────────────────────

class RunRequest(BaseModel):
    input: str
    resume: bool = False
    style: str | list[str] = "写实风格"


def _to_styles(style) -> list[str]:
    """把前端传入的 style（字符串或字符串数组）归一化为列表。

    第一个元素即「主风格」，决定出图/视频底模等模型选择。
    """
    if isinstance(style, list):
        clean = [s.strip() for s in style if isinstance(s, str) and s.strip()]
    elif isinstance(style, str) and style.strip():
        clean = [style.strip()]
    else:
        clean = []
    return clean or ["写实风格"]

# 仍在执行的管线状态（终态 done/failed/cancelled 除外）
_LIVE_STATUS = ("queued", "running", "review")


def _find_live_pipeline(text: str, styles: list[str]) -> Optional[str]:
    """找出与本次请求「同输入 + 同风格」且仍在跑的管线 id。

    前端重复点击 / 多标签同时提交会并发起多条同输入管线，而本机 LTX 出片是
    串行跑的（24GB 内存，跑两条会互相挤爆 swap），所以同请求直接复用已有管线。
    """
    for pid, entry in _active.items():
        if (entry.get("status") in _LIVE_STATUS
                and entry.get("input") == text
                and entry.get("styles") == styles):
            return pid
    return None


def _set_job_status(job_id, status: str, error: str = ""):
    """把 DB 中的 PipelineJob 置为终态（取消等非正常结束路径收尾用）。"""
    if not job_id:
        return
    try:
        db = get_session()
        job = db.query(PipelineJob).filter_by(id=job_id).first()
        if job:
            job.status = status
            if error:
                job.error = error
            db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"[Pipeline] 更新 job={job_id} 状态失败: {e}")


class StatusResponse(BaseModel):
    pipeline_id: str
    status: str
    current_agent: str
    progress: int
    error: str = ""
    summary: dict = {}

# ── 直接挂载到 app ──────────────────────

def register_pipeline_routes(app):
    """直接添加路由到 app（不使用 APIRouter）"""

    # ── SSE 事件流（方案要求）────────────

    async def sse_event_sender(q: asyncio.Queue):
        try:
            while True:
                data = await wait_or_shutdown(q.get(), timeout=30)
                if data is INTERRUPTED:
                    if shutdown_event.is_set():
                        # 服务端关闭：推一条收尾事件让前端知道流已结束，再主动断开
                        yield 'event: shutdown\ndata: {"reason": "server_shutdown"}\n\n'
                        return
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {data['event']}\ndata: {json.dumps(data['data'], ensure_ascii=False)}\n\n"
        finally:
            # 客户端断开 / 服务端关闭都会取消本生成器，务必在这里摘掉队列：
            # 否则 _sse_broadcast 会永远往死队列塞事件，队列只增不减（内存泄漏）。
            if q in _sse_clients:
                _sse_clients.remove(q)
            logger.info(f"[SSE] 订阅结束 (剩余 {len(_sse_clients)} 个)")

    @app.get("/api/v1/pipeline/events/stream")
    async def sse_stream():
        q: asyncio.Queue = asyncio.Queue()
        _sse_clients.append(q)
        logger.info(f"[SSE] 新订阅 (共 {len(_sse_clients)} 个)")
        return StreamingResponse(sse_event_sender(q), media_type="text/event-stream")

    # ── WebSocket（兼容旧前端）────────────
    @app.websocket("/api/v1/pipeline/ws")
    async def ws_handler(ws: WebSocket):
        await ws.accept()
        _ws_clients.append(ws)
        logger.info(f"WS 连入 (共 {len(_ws_clients)} 个)")
        try:
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"[WS] 异常: {e}")
        finally:
            # 服务端关闭时任务被 cancel（CancelledError 不是 Exception）也要摘掉，
            # 否则 _broadcast 会一直往已关闭的连接上发
            if ws in _ws_clients:
                _ws_clients.remove(ws)
            logger.info(f"WS 断开 (剩余 {len(_ws_clients)} 个)")

    # ── 启动管线 ──────────────────────
    @app.post("/api/v1/pipeline/run")
    async def run_pipeline(req: RunRequest):
        text = req.input.strip()
        if not text or len(text) < 2:
            raise HTTPException(400, "输入至少2个字符")

        # 记录本次管线所选风格（可多选，第一个为主风格）→ 相关 Provider 据此选 model
        from config.style_resolver import set_active_styles
        styles = _to_styles(req.style)

        # 幂等去重：同输入同风格已在跑就直接复用，不再起第二条（避免并发抢内存）
        dup_id = _find_live_pipeline(text, styles)
        if dup_id:
            logger.warning(f"[Pipeline] 重复提交，复用进行中的管线 {dup_id}")
            return {
                "success": True, "pipeline_id": dup_id,
                "story_id": _active[dup_id].get("story_id"), "duplicated": True,
            }

        set_active_styles(styles)

        pipeline_id = f"pipe_{int(time.time())}_{os.urandom(4).hex()}"

        db = get_session()
        story = Story(original_input=text)
        db.add(story)
        db.flush()
        _sid = story.id  # int — 在 commit 前读出来
        job = PipelineJob(story_id=_sid, status="running", current_agent="queued")
        db.add(job)
        db.flush()
        _jid = job.id
        db.commit()
        db.close()

        _active[pipeline_id] = {
            "pipeline_id": pipeline_id, "status": "running",
            "story_id": _sid, "job_id": _jid,
            "input": text, "styles": styles,
            "current_agent": "queued", "progress": 0,
            "summary": {}, "error": "", "started_at": time.time(),
        }

        await _sse_broadcast("pipeline_start", {
            "pipeline_id": pipeline_id, "story_id": _sid,
            "input": text[:80],
        }, pipeline_id)

        # —— 向导桥：启动事件（无 payload，只更新提示、不点亮步骤）——
        _wiz_publish(pipeline_id, "progress", 0, "🚀 管线已启动…", None, 2)

        import asyncio
        task = asyncio.create_task(_execute(pipeline_id, _sid, text, req.resume, req.style))
        # 登记任务句柄：取消时要靠它真正中断后台执行，否则任务会继续跑完剩余节点
        _pipeline_tasks[pipeline_id] = task
        task.add_done_callback(lambda _t, _pid=pipeline_id: _pipeline_tasks.pop(_pid, None))

        return {"success": True, "pipeline_id": pipeline_id, "story_id": _sid}

    # ── 风格列表（供前端下拉展示 + 小白建议）──
    @app.get("/api/v1/pipeline/styles")
    async def get_styles():
        from config.style_resolver import list_styles
        return {"styles": list_styles()}

    # ── 本地模型清单（Ollama LLM + ComfyUI 出图 checkpoint）──
    @app.get("/api/v1/pipeline/local-models")
    async def get_local_models():
        """列出本机已安装的模型，供「模型库」页选择。

        - LLM: Ollama /api/tags 的本地模型 + 配置里的云端 DeepSeek 模型
        - 出图: ComfyUI checkpoints(SD1.5) + diffusion_models(FLUX GGUF UNET) 合并
        - 视频: 本地 LTX-2.3 MLX 模型（不走 ComfyUI）
        任一服务不可用则返回空列表，前端会显示提示，不影响其它功能。
        """
        cfg_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

        # 1. LLM 模型：Ollama 本地已 pull 的 + 配置里的云端 DeepSeek 模型
        llm_models: list[str] = []
        ollama_url = cfg.get("ollama", {}).get("server_url", "http://localhost:11434")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                resp.raise_for_status()
                tags = resp.json().get("models", [])
                llm_models = [m.get("name", "") for m in tags if m.get("name")]
        except Exception as e:
            logger.warning(f"Ollama /api/tags 获取失败: {e}")

        # 云端 DeepSeek 模型（engine.llm_provider=deepseek 时实际使用的模型）
        engine_cfg = cfg.get("engine", {}) or {}
        for key in ("llm_model_script", "llm_model_storyboard"):
            name = engine_cfg.get(key)
            if name and name not in llm_models:
                llm_models.append(name)

        # 2. ComfyUI 出图模型：checkpoints(SD1.5) + diffusion_models(FLUX GGUF UNET)
        #    两处都属「出图」底模，合并给前端，避免 FLUX 被误列到视频区。
        image_ckpts: list[str] = []
        comfy_addr = cfg.get("comfyui", {}).get("server_addr", "127.0.0.1")
        comfy_port = cfg.get("comfyui", {}).get("server_port", 8189)
        comfy_timeout = cfg.get("comfyui", {}).get("timeout", 30)
        try:
            from providers.comfyui.client import ComfyUIClient
            comfy_client = ComfyUIClient(
                server_addr=comfy_addr,
                server_port=int(comfy_port),
                timeout=int(comfy_timeout),
            )
            for m in await comfy_client.list_models("checkpoints"):
                if m not in image_ckpts:
                    image_ckpts.append(m)
            for m in await comfy_client.list_models("diffusion_models"):
                if m not in image_ckpts:
                    image_ckpts.append(m)
            await comfy_client.close()
        except Exception as e:
            logger.warning(f"ComfyUI list_models 获取失败: {e}")

        # 3. 视频模型：本地 LTX-2.3 MLX（不走 ComfyUI），取配置中各风格 video_model
        video_models: list[str] = []
        for entry in (cfg.get("styles", {}) or {}).values():
            vm = (entry or {}).get("video_model")
            if vm and vm not in video_models:
                video_models.append(vm)
        # 兜底：扫描本地模型目录里形如 ltx/wan/... 的视频模型目录
        models_root = Path(__file__).parent.parent.parent / "storage" / "models"
        if models_root.exists():
            for d in sorted(models_root.iterdir()):
                if d.is_dir() and d.name.lower().startswith(
                    ("ltx", "wan", "svd", "hunyuan", "mochi", "cogvideo")
                ) and d.name not in video_models:
                    video_models.append(d.name)

        from config.style_resolver import current_style, current_styles
        return {
            "llm_models": llm_models,
            "image_ckpts": image_ckpts,
            "video_models": video_models,
            "current_style": current_style(),
            "current_styles": current_styles(),
        }

    # ── 保存某个风格的模型配置（写回 config.yaml）──
    @app.put("/api/v1/pipeline/styles/{style}")
    async def update_style(style: str, data: dict):
        """把用户在本机「模型库」选择/配置的模型写回 config.yaml 并立即生效。"""
        from config.style_resolver import update_style_config, list_styles
        updated = update_style_config(style, data)
        if updated is None:
            raise HTTPException(404, f"风格不存在: {style}")
        # 通知前端/其它会话刷新
        await _sse_broadcast("styles_updated", {"style": style, "entry": updated})
        return {"success": True, "style": style, "entry": updated, "styles": list_styles()}

    # ── 状态查询 ──────────────────────
    @app.get("/api/v1/pipeline/status/{pipeline_id}")
    async def get_status(pipeline_id: str):
        entry = _active.get(pipeline_id)
        if not entry:
            raise HTTPException(404, "Pipeline 不存在或已过期")
        return StatusResponse(
            pipeline_id=pipeline_id, status=entry["status"],
            current_agent=entry["current_agent"], progress=entry["progress"],
            error=entry.get("error", ""), summary=entry.get("summary", {}),
        )

    # ── 当前活跃审核管线（用于前端刷新后恢复审核状态）──
    @app.get("/api/v1/pipeline/active")
    async def get_active_pipeline():
        """返回最接近当前、处于 review / running 的活跃管线。

        SSE 是实时无回放的，前端刷新后会丢失 state.pipelineId，
        此接口用于前端启动时主动恢复正在等待人工确认（review）的管线。
        """
        entries = [
            {
                "pipeline_id": pid,
                "status": entry.get("status"),
                "current_agent": entry.get("current_agent"),
                "progress": entry.get("progress"),
                "review_reason": entry.get("review_reason", ""),
                "started_at": entry.get("started_at"),
            }
            for pid, entry in _active.items()
            if entry.get("status") in ("review", "running", "queued")
        ]
        # 按启动时间倒序，最近的优先
        entries.sort(key=lambda e: e.get("started_at") or 0, reverse=True)
        return {"active": entries}

    # ── 快照（获取 checkpoint 的角色/分镜/视频数据）──
    @app.get("/api/v1/pipeline/snapshot/latest")
    async def get_latest_snapshot():
        cp_dir = Path("storage/checkpoints")
        result = {}
        prefixes = ["intel_agent", "research_agent", "script_agent", "storyboard_agent",
                    "character_agent", "image_agent", "video_agent",
                    "subtitle_agent", "compose_agent", "video_compose_agent",
                    "audio_agent", "publish_agent"]
        for pf in prefixes:
            fp = cp_dir / f"{pf}_checkpoint.json"
            if fp.exists():
                try:
                    raw = json.loads(fp.read_text())
                    data = raw.get("data", raw)
                    result[pf] = data
                except Exception:
                    pass
        return result

    @app.get("/api/v1/pipeline/wizard/latest")
    async def get_latest_wizard():
        """把最近一次落盘的 checkpoint 转成前端 5 步向导所需的 payload。

        为什么需要它：管线跑完（或后端重启）后该 run 已从 /active 移除，而事件总线
        是进程内存、重启即清空；用户此时刷新页面既找不到 run_id 也没历史可回放，
        界面就是空白。此端点让前端直接按 step 回填最近一次的角色卡与分镜出图等。
        同一 step 有多个 agent 时（如 step 2 的 character_agent / image_agent），
        按 _WIZ_STEP_OF 的声明顺序后者覆盖前者——image_agent 的 payload 更全
        （同时含 characters 与 images）。
        """
        out: dict = {}
        for agent in _WIZ_STEP_OF:
            try:
                payload = _wiz_payload(agent)
            except Exception as e:
                logger.warning(f"[Wizard] 回填 {agent} 失败，已跳过: {e}")
                continue
            if payload:
                out[str(_WIZ_STEP_OF[agent])] = payload
        return out

    @app.get("/api/v1/pipeline/snapshot/{pipeline_id}")
    async def get_snapshot(pipeline_id: str):
        cp_dir = Path("storage/checkpoints")
        result = {}
        prefixes = ["intel_agent", "research_agent", "script_agent", "storyboard_agent",
                    "character_agent", "image_agent", "video_agent",
                    "subtitle_agent", "compose_agent", "video_compose_agent",
                    "audio_agent", "publish_agent"]
        for pf in prefixes:
            fp = cp_dir / f"{pf}_checkpoint.json"
            if fp.exists():
                try:
                    raw = json.loads(fp.read_text())
                    data = raw.get("data", raw)
                    result[pf] = data
                except Exception:
                    pass
        return result

    # ── 历史 ──────────────────────────
    @app.get("/api/v1/pipeline/history")
    async def get_history(limit: int = 10):
        db = get_session()
        rows = (
            db.query(PipelineJob, Story)
            .join(Story, PipelineJob.story_id == Story.id)
            .order_by(PipelineJob.id.desc())
            .limit(limit)
            .all()
        )
        db.close()
        return {
            "history": [
                {
                    "id": j.id, "story_id": j.story_id,
                    "input": s.original_input[:100] if s.original_input else "",
                    "title": s.title, "status": j.status,
                    "current_agent": j.current_agent, "error": j.error,
                    "created_at": j.created_at.isoformat() if j.created_at else "",
                }
                for j, s in rows
            ]
        }

    # ── 审核确认 ──────────────────────
    @app.post("/api/v1/pipeline/approve/{pipeline_id}")
    async def approve(pipeline_id: str):
        pipe = _pipeline_instances.get(pipeline_id)
        if not pipe:
            raise HTTPException(404, "Pipeline 不存在")
        pipe.approve_review()
        await _sse_broadcast("review_approved", {"pipeline_id": pipeline_id}, pipeline_id)
        return {"success": True, "action": "approved"}

    @app.post("/api/v1/pipeline/reject/{pipeline_id}")
    async def reject(pipeline_id: str, data: dict = {}):
        pipe = _pipeline_instances.get(pipeline_id)
        if not pipe:
            raise HTTPException(404, "Pipeline 不存在")
        pipe.reject_review()
        await _sse_broadcast("review_rejected", {"pipeline_id": pipeline_id, "data": data}, pipeline_id)
        return {"success": True, "action": "rejected"}

    @app.post("/api/v1/pipeline/submit/{pipeline_id}")
    async def submit_edit(pipeline_id: str, data: dict = {}):
        """用户在前端修改了方案/剧本并提交，覆盖后续生成的剧本/方案"""
        pipe = _pipeline_instances.get(pipeline_id)
        if not pipe:
            raise HTTPException(404, "Pipeline 不存在")
        pipe.submit_edit(data)
        await _sse_broadcast("review_updated", {"pipeline_id": pipeline_id, "data": data}, pipeline_id)
        return {"success": True, "action": "submitted"}

    # ── 直接编辑某个已完成步骤的内容（无需管线暂停）──
    @app.put("/api/v1/pipeline/edit/{agent}")
    async def edit_step(agent: str, data: dict = {}):
        """把某个步骤的 checkpoint 内容直接覆盖为用户的修改稿。

        供「点开任意步骤 → 编辑 → 保存」使用，即使管线当前未暂停在该步。
        agent 为 checkpoint 名（如 research_agent / storyboard_agent），
        data 为该步骤的完整修改内容（写入 checkpoint 的 data 字段）。
        """
        from config.settings import settings
        cp_dir = Path(getattr(settings, "checkpoint_dir", "storage/checkpoints"))
        cp = cp_dir / f"{agent}_checkpoint.json"
        if not cp.exists():
            raise HTTPException(404, f"该步骤暂无数据可编辑: {agent}")
        try:
            raw = json.loads(cp.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {"success": True, "data": {}}
        raw["data"] = data if isinstance(data, dict) else {}
        raw["edited_at"] = datetime.utcnow().isoformat()
        cp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        await _sse_broadcast("review_updated", {"pipeline_id": "", "agent": agent, "data": data})
        return {"success": True, "agent": agent, "edited": True}

    # ── 取消管线 ──────────────────────
    @app.post("/api/v1/pipeline/cancel/{pipeline_id}")
    async def cancel(pipeline_id: str):
        entry = _active.get(pipeline_id)
        if not entry:
            raise HTTPException(404, "Pipeline 不存在")
        entry["status"] = "cancelled"

        # 真正中断后台任务：只摘引用（pop）而不 cancel 的话，任务会继续跑完剩余节点，
        # 包括串行的 LTX 出片，表现为「取消后仍在跑 / 管线看似结束却继续落盘」。
        task = _pipeline_tasks.pop(pipeline_id, None)
        if task and not task.done():
            task.cancel()

        # 卡在审核断点上的管线要唤醒，否则一直停在 _review_lock.wait()
        pipe = _pipeline_instances.get(pipeline_id)
        if pipe is not None:
            pipe.cancel()

        await _sse_broadcast("pipeline_cancelled", {"pipeline_id": pipeline_id}, pipeline_id)
        _set_job_status(entry.get("job_id"), "cancelled")
        # 清理
        _active.pop(pipeline_id, None)
        _pipeline_instances.pop(pipeline_id, None)
        return {"success": True, "action": "cancelled"}

# ── WebSocket 广播 ──────────────────────

async def _broadcast(event: str, data: dict, pipeline_id: str = ""):
    payload = {
        "type": event, "data": data,
        "pipeline_id": pipeline_id, "ts": datetime.utcnow().isoformat(),
    }
    dead: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            _ws_clients.remove(ws)
        except ValueError:
            pass


async def _sse_broadcast(event: str, data: dict, pipeline_id: str = ""):
    """SSE 广播（模块级）：写入所有 SSE 客户端队列，同时转发到 WebSocket"""
    dead = []
    for q in _sse_clients:
        try:
            await q.put({"event": event, "data": data})
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _sse_clients.remove(q)
        except ValueError:
            pass
    # 同时广播到 WS
    await _broadcast(event, data, pipeline_id)


# ── 后台执行 ──────────────────────────

# ════════════════════════════════════════════════════════════════
# 真实运行 → 5 步向导事件桥
# 前端 PipelineView 只订阅 /pipeline/events?run_id=xxx（pipeline_events_router
# 总线），而真实管线原先只向 /api/v1/pipeline/events/stream 广播 agent_* 事件，
# 导致向导只能看到 __demo__ 假流。这里把「审核断点到达 / 整线结束」翻译成向导
# 同构的 {type,step,step_name,percent,message,payload} 事件并发布到总线，让前端
# 直接渲染 5 步真实数据。演示模式已移除。
# ════════════════════════════════════════════════════════════════

# agent → 向导步骤（0-4，与 PipelineView.stepNodes 顺序一致）
_WIZ_STEP_OF = {
    "research_agent": 0, "script_agent": 0,          # 剧本审查
    "storyboard_agent": 1,                            # 分镜审查
    "character_agent": 2, "image_agent": 2,           # 角色/图片审查
    "video_agent": 3, "subtitle_agent": 3,            # 视频/字幕
    "video_compose_agent": 4, "compose_agent": 4,
    "audio_agent": 4, "publish_agent": 4,             # 成片/发布
}
_WIZ_STEP_NAMES = ["剧本审查", "分镜审查", "图片审查", "视频预览", "成片发布"]

# agent → 人类可读的业务名（用于断点提示语）
_WIZ_AGENT_LABEL = {
    "research_agent": "创作方案", "script_agent": "剧本", "storyboard_agent": "分镜",
    "character_agent": "角色定妆照", "image_agent": "分镜出图", "video_agent": "视频",
    "subtitle_agent": "字幕", "video_compose_agent": "成片合成", "compose_agent": "成片合成",
    "audio_agent": "配音音频", "publish_agent": "发布产物",
}


def _wiz_step_of(agent: str) -> int:
    """把 agent 名字映射到向导步骤 0-4（未知 agent 归到第 4 步收尾）。"""
    return _WIZ_STEP_OF.get(agent, 4)


def _cp_data(agent_name: str) -> dict:
    """读某 agent 落盘的 checkpoint 内层 data（无文件 / 异常一律返回 {}）。"""
    try:
        fp = Path("storage/checkpoints") / f"{agent_name}_checkpoint.json"
        if not fp.exists():
            return {}
        raw = json.loads(fp.read_text(encoding="utf-8"))
        data = raw.get("data", raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _pick(data: dict, *keys, default: str = "") -> str:
    """按顺序取第一个「非空字符串」的字段值，全部没有则返回 default。"""
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _first_media(data, depth: int = 0) -> str:
    """递归找第一个视频/图片文件路径（用于步骤 3/4 的成片 URL）。"""
    if depth > 3 or not isinstance(data, dict):
        return ""
    for k, v in data.items():
        if isinstance(v, str) and v.strip().lower().endswith(
            (".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg")
        ):
            return v.strip()
        if isinstance(v, dict):
            hit = _first_media(v, depth + 1)
            if hit:
                return hit
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    hit = _first_media(it, depth + 1)
                    if hit:
                        return hit
    return ""


# 出图产物可能落在两处：AIGC 本地图库（出图后复制过来的副本）与 ComfyUI 输出目录。
# _wiz_media_url 处理「裸文件名」时要探测它们，故集中定义（与 api/main.py 的挂载保持一致）。
_LOCAL_IMAGES_DIR = Path(__file__).parent.parent.parent / "storage" / "output" / "images"
_COMFY_OUTPUT_DIR = Path(os.environ.get(
    "COMFY_OUTPUT_DIR",
    "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/output",
))


def _wiz_media_url(path: str) -> str:
    """把磁盘路径转成浏览器可访问的 URL（对应 api/main.py 的静态挂载）。

    入参有三种历史形态（各 agent 落盘习惯不一，都要认）：
      ① 绝对路径（含 /ComfyUI/output/ 或 /storage/output/）—— 协议化即可；
      ② 相对路径（storage/output/images/xxx.png）—— 补前导斜杠；
      ③ 裸文件名（comfyui_output_00046_.png）—— character_agent 的 portrait_path
         就只存文件名，没有任何目录信息。这种靠「AIGC 本地图库 → ComfyUI 输出目录」
         逐个探测，落到哪个静态挂载下就用哪个 URL。
    """
    if not path:
        return ""
    s = str(path).replace("\\", "/")
    marker = "/ComfyUI/output/"
    idx = s.find(marker)
    if idx >= 0:
        return "/comfyui-output/" + s[idx + len(marker):]
    if "storage/output/" in s:
        return "/storage/output/" + s.split("storage/output/", 1)[1]
    if s.startswith(("http://", "https://")):
        return s
    # 裸文件名兜底：图片出图后会被复制一份到 AIGC 本地图库，优先用副本
    name = Path(s).name
    if not name:
        return ""
    if (_LOCAL_IMAGES_DIR / name).is_file():
        return f"/storage/output/images/{name}"
    if (_COMFY_OUTPUT_DIR / name).is_file():
        return f"/comfyui-output/{name}"
    return ""


def _fmt_script(d: dict) -> str:
    """把剧本 checkpoint 的『结构化分集/对白』拼成前端可读的纯文本剧本。"""
    direct = _pick(d, "script", "content", "script_text")
    if direct:
        return direct
    parts = []
    genre = _pick(d, "genre")
    summary = _pick(d, "summary")
    if genre:
        parts.append(f"类型：{genre}")
    if summary:
        parts.append(f"梗概：{summary}")
    for ep in d.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        ep_no = ep.get("episode_number", ep.get("episode", ""))
        ep_title = ep.get("title", "")
        parts.append(f"\n第 {ep_no} 集《{ep_title}》")
        if ep.get("plot"):
            parts.append(ep["plot"])
        for dl in ep.get("dialogues") or []:
            if not isinstance(dl, dict):
                continue
            scene = _pick(dl, "scene")
            who = _pick(dl, "character", "role")
            line = _pick(dl, "line", "content")
            seg = f"【{scene}】" if scene else ""
            parts.append(f"{seg}{who}：{line}" if who else f"{seg}{line}")
    return "\n".join(p for p in parts if p)


def _char_rows(d: dict) -> list:
    """把角色 checkpoint 的 characters 列表转成向导步骤 2 需要的行结构。"""
    chs = d.get("characters") or d.get("characters_design") or []
    rows = []
    for i, c in enumerate(chs or []):
        if not isinstance(c, dict):
            continue
        # 兼容两种结构：
        #   ① 直接是 asset（含 portrait_path）
        #   ② 是 {"name","status","asset":{...}} 的 AgentResult 结果
        asset = c.get("asset") if isinstance(c.get("asset"), dict) else c
        name = _pick(c, "name", "character", "suggested_name") or _pick(asset, "name")
        role = _pick(c, "role", "类型") or _pick(asset, "role", "类型")
        portrait = (
            _pick(c, "portrait_path", "image_path", "controlnet_ref_path")
            or _pick(asset, "portrait_path", "image_path", "controlnet_ref_path")
        )
        label = f"{name}（{role}）" if name and role else (name or f"角色{i + 1}")
        rows.append({
            # 真实环境没有 image_seed 就用名字/序号顶替；url 为磁盘定妆照转浏览器可访问地址
            "image_seed": _pick(c, "image_seed", "asset_id", default=name or f"char_{i}"),
            "url": _wiz_media_url(portrait),
            "suggested_name": label,
            "assigned_name": name or label,
        })
    return rows


def _wiz_payload(agent: str) -> dict:
    """按审核断点 agent 返回 demo 同构的 payload（尽量空值安全）。"""
    d = _cp_data(agent) or {}
    step = _wiz_step_of(agent)

    # —— 步骤 0：剧本 / 方案 ——
    if step == 0:
        if agent == "research_agent":
            plan = _pick(d, "plan", "scheme", "思路")
            if not plan:
                # 结构化方案 → 拼成一段人类可读文本（覆盖向导单 textarea 展示）
                seg = []
                for k in ("target_audience", "style_direction", "narrative_structure", "bgm_mood"):
                    v = _pick(d, k)
                    if v:
                        seg.append(v)
                for k, label in (("key_words", "核心词"), ("copy_points", "文案要点"),
                                 ("camera_words", "镜头语言"), ("scene_sounds", "场景声效")):
                    vs = d.get(k) or []
                    if vs:
                        seg.append(f"{label}：" + "；".join(str(x) for x in vs))
                plan = "\n".join(seg)
            cases = d.get("reference_cases") or []
            if cases:
                plan = plan + "\n\n参考案例：\n" + "\n".join(
                    f"- {x}" if isinstance(x, str) else f"- {x.get('title') or x.get('summary') or x}"
                    for x in cases[:5]
                )
            return {"title": _pick(d, "title", "topic", default="创作方案"), "script": plan or ""}
        return {
            "title": _pick(d, "title", default="未命名剧本"),
            "script": _fmt_script(d),
        }

    # —— 步骤 1：分镜 ——
    if agent == "storyboard_agent":
        raw_shots = (
            d.get("shots") or d.get("shot_list")
            or (d.get("storyboard") if isinstance(d.get("storyboard"), list) else [])
        )
        if not raw_shots:
            # 真实 checkpoint 是分集结构：{"episodes":[{"episode_number":1,"shots":[…]}]}，
            # 只找顶层 shots 会一行都取不到（分镜步骤因此空白）
            for ep in (d.get("episodes") or []):
                if isinstance(ep, dict) and isinstance(ep.get("shots"), list) and ep["shots"]:
                    raw_shots = ep["shots"]
                    break
        shots = []
        for s in raw_shots or []:
            if not isinstance(s, dict):
                continue
            shots.append({
                "shot_id": _pick(s, "shot_id", "id", default=f"S{len(shots) + 1:03d}"),
                "景别": _pick(s, "景别", "shot_type", "scene_type"),
                "对白": _pick(s, "对白", "dialogue"),
                "prompt": _pick(s, "prompt", "sd_prompt", "description"),
                "duration_sec": s.get("duration", s.get("duration_sec") or 5),
            })
        return {"shots": shots}

    # —— 步骤 2：角色/出图 ——
    if agent == "character_agent":
        return {"characters": _char_rows(d)}

    if agent == "image_agent":
        # checkpoint 的 images 是两层嵌套：{"ep_1": {"3": {filename, local_path, qc…}, …}}，
        # 这里要摊平成「一镜一行」——早前只遍历一层，会把整个 ep_1 当成一张图，
        # 前端因此只拿到 1 行且 url 为空，等于看不到任何分镜图。
        imgs = d.get("images") or {}
        rows: list = []

        def _add(shot_id, rec) -> None:
            if isinstance(rec, dict):
                # local_path（出图后复制到 AIGC 本地图库的副本）比裸 filename 更完整，优先取
                p = _pick(rec, "local_path", "image_path", "path") or _first_media(rec)
            else:
                p = str(rec) if rec else ""
            rows.append({
                "shot_id": str(shot_id),
                "url": _wiz_media_url(p),
                "image_path": p,
            })

        if isinstance(imgs, dict):
            for ep_key, ep_val in imgs.items():
                if isinstance(ep_val, dict):
                    for shot_id, rec in ep_val.items():
                        _add(shot_id, rec)
                else:
                    _add(ep_key, ep_val)   # 兼容旧的「一镜一行」结构
        elif isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, dict):
                    _add(_pick(it, "shot_id", default=""), it)
        # 按镜头号排序，前端展示顺序才与剧情一致（字符串排序会把 10 排到 2 前面）
        rows.sort(key=lambda r: int(r["shot_id"]) if r["shot_id"].isdigit() else 0)
        return {"characters": _char_rows(_cp_data("character_agent")), "images": rows}

    # —— 步骤 3：视频/字幕 ——
    if agent in ("video_agent", "subtitle_agent"):
        media = _first_media(d)
        if not media:
            # subtitle_agent 的 checkpoint 只存 .srt（不在 _first_media 认的媒体后缀里），
            # 而它与 video_agent 同属第 3 步且声明在后，会把该步 video_url 覆盖成空；
            # 这里回退去读 video_agent 的分镜视频，保证视频预览不为空。
            media = _first_media(_cp_data("video_agent"))
        return {
            "video_url": _wiz_media_url(media),
            "duration_sec": _pick(d, "duration_sec", "duration", default="—"),
            "resolution": _pick(d, "resolution", default="—"),
            "has_subtitle": bool(d.get("srt_files") or d.get("subtitles")),
            "agent": agent,
        }

    # —— 步骤 4：合成/配音/发布 ——
    media = _first_media(d)
    return {
        "video_url": _wiz_media_url(media),
        "agent": agent,
        "title": _pick(d, "title", default=""),
        "created_at": _pick(d, "created_at", default=time.strftime("%Y-%m-%d %H:%M:%S")),
    }


def _wiz_publish(pipeline_id: str, event_type: str, step: int,
                 message: str, payload=None, percent: int = 0) -> None:
    """统一封装：向向导总线发一条 5 步事件（只发整数 step，越界自动钳制）。"""
    step = max(0, min(4, int(step)))
    publish_pipeline_event(pipeline_id, {
        "type": event_type,
        "step": step,
        "step_name": _WIZ_STEP_NAMES[step],
        "percent": max(0, min(100, percent)),
        "message": message,
        "payload": payload,
    })


def _final_wizard_payload() -> dict:
    """管线收尾时，汇总「成片 URL / 标题 / 时间」等最终页展示字段。"""
    payload: dict = {}
    for agent in ("publish_agent", "audio_agent", "video_compose_agent", "compose_agent"):
        d = _cp_data(agent)
        if not d:
            continue
        media = _first_media(d)
        if not payload.get("video_url"):
            payload["video_url"] = _wiz_media_url(media)
        if not payload.get("title"):
            payload["title"] = _pick(d, "title")
        if not payload.get("output_size"):
            payload["output_size"] = _pick(d, "resolution", "output_size", "size")
        if not payload.get("file_size"):
            payload["file_size"] = _pick(d, "file_size", "filesize")
    if not payload.get("title"):
        payload["title"] = _pick(_cp_data("script_agent"), "title", default="未命名成片")
    payload["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return payload


async def _execute(pipeline_id: str, story_id: int, user_input: str, resume: bool, style: str | list = "写实风格"):
    try:
        # 确保管道内所有 Agent 用所选风格对应的模型 / 关键词 / 负向提示（多风格时前一个为主风格）
        from config.style_resolver import set_active_styles
        set_active_styles(_to_styles(style))

        pipe = Pipeline(pipeline_id=pipeline_id)
        _pipeline_instances[pipeline_id] = pipe

        # 设置 callbacks
        async def on_start(name: str, idx: int, total: int):
            prog = int((idx / total) * 100)
            _active[pipeline_id].update(current_agent=name, progress=prog, status="running")
            await _sse_broadcast("agent_start", {
                "agent": name, "step": idx + 1, "total": total, "progress": prog,
            }, pipeline_id)
            # —— 向导桥：无 payload 的进行中事件（前端据此不点亮步骤，仅更新提示）——
            _wiz_publish(
                pipeline_id, "progress", _wiz_step_of(name),
                f"⏳ {_WIZ_AGENT_LABEL.get(name, name)} 生成中…", None, prog,
            )

        async def on_complete(name: str, idx: int, total: int, meta: dict):
            prog = int(((idx + 1) / total) * 100)
            _active[pipeline_id]["current_agent"] = name
            _active[pipeline_id]["progress"] = prog
            _active[pipeline_id]["summary"][name] = meta
            await _sse_broadcast("agent_done", {
                "agent": name, "step": idx + 1, "total": total,
                "progress": prog, "meta": meta,
            }, pipeline_id)

        async def on_fail(name: str, idx: int, total: int, error: str):
            _active[pipeline_id].update(current_agent=name, status="failed", error=error)
            await _sse_broadcast("agent_fail", {
                "agent": name, "step": idx + 1, "total": total, "error": error,
            }, pipeline_id)
            # —— 向导桥：错误事件（前端转 error 态并展示原因）——
            _wiz_publish(
                pipeline_id, "error", _wiz_step_of(name),
                f"❌ {_WIZ_AGENT_LABEL.get(name, name)} 失败：{error}", None,
            )

        async def on_review(agent_name: str, reason: str, data: dict):
            _active[pipeline_id].update(
                status="review", current_agent=agent_name, review_reason=reason,
            )
            await _sse_broadcast("agent_blocked", {
                "agent": agent_name, "reason": reason, "pipeline_id": pipeline_id,
            }, pipeline_id)
            # —— 向导桥：审核断点到达 → 发布对应步骤的真实内容 payload ——
            step = _wiz_step_of(agent_name)
            label = _WIZ_AGENT_LABEL.get(agent_name, agent_name)
            _wiz_publish(
                pipeline_id, "progress", step,
                f"⏸️ 等待审核：{label}已生成，可查看/修改后继续",
                _wiz_payload(agent_name), min(100, (step + 1) * 20),
            )

        async def on_progress(name: str, stage: str, info: dict):
            """子进度（如配音预测量逐句 TTS）：转发 SSE + 刷新向导步骤提示文案。

            总进度条沿用当前 agent 的进度值（不做子进度插值），避免百分比回退。
            """
            if stage != "voice_plan":
                return
            await _sse_broadcast("voice_plan_progress", {
                "agent": name, "stage": stage, **info,
            }, pipeline_id)
            done, total = int(info.get("done") or 0), int(info.get("total") or 0)
            if total <= 0:
                return
            label = (f"🎙️ 配音预测量 {done}/{total}…" if done
                     else f"🎙️ 配音预测量开始：共 {total} 句（首次需加载模型）")
            _wiz_publish(
                pipeline_id, "progress", _wiz_step_of("storyboard_agent"),
                label, None, _active[pipeline_id].get("progress") or 0,
            )

        async def on_done(final: dict):
            _active[pipeline_id].update(current_agent="completed", progress=100, status="done")
            # 自动触发「生成创作笔记」：只写 Markdown，不沉淀 skills 词库
            if final.get("success"):
                try:
                    from pipeline.notes import generate_note_from_results
                    note_path = await generate_note_from_results(
                        final.get("results", {}),
                        user_input=user_input, styles=style,
                        story_id=story_id, pipeline_id=pipeline_id,
                    )
                    if note_path:
                        await _sse_broadcast("notes_generated", {
                            "path": str(note_path),
                        }, pipeline_id)
                        logger.info(f"[{pipeline_id}] 创作笔记已生成: {note_path}")
                except Exception as e:
                    logger.warning(f"[{pipeline_id}] 生成创作笔记失败: {e}")
            await _sse_broadcast("pipeline_done", {
                "summary": _active[pipeline_id].get("summary", {}),
                "success": final.get("success", False),
            }, pipeline_id)
            # —— 向导桥：先给第 4 步发「成片」内容，再发 done 结束流 ——
            _wiz_publish(
                pipeline_id, "progress", 4,
                "🎬 成片已合成完成，可预览或下载", _final_wizard_payload(), 100,
            )
            _wiz_publish(pipeline_id, "done", 4, "🎉 管线全部完成！", None, 100)

        pipe.set_callbacks(on_start, on_complete, on_fail, on_done, on_review,
                           on_agent_progress=on_progress)

        # 根据 config 判断用 mock 还是 comfyui
        config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        engine = cfg.get("engine", {})
        use_comfyui = engine.get("image_provider", "mock") == "comfyui"
        use_comfyui_video = engine.get("video_provider", "mock") == "comfyui"

        from providers.comfyui.client import ComfyUIClient
        comfy_client = None
        if use_comfyui or use_comfyui_video:
            comfy_host = cfg.get("comfyui", {}).get("server_addr", "127.0.0.1")
            comfy_port = cfg.get("comfyui", {}).get("server_port", 8189)
            comfy_timeout = cfg.get("comfyui", {}).get("timeout", 600)
            # 必须把 timeout 传给客户端：M4 出图/生视频可达 5-30 分钟，默认 300s 会误判超时
            comfy_client = ComfyUIClient(
                server_addr=comfy_host,
                server_port=comfy_port,
                timeout=comfy_timeout,
            )

        from agents.research_agent import ResearchAgent
        from agents.intel_agent import IntelligenceAgent
        from agents.video_compose_agent import VideoComposeAgent
        from agents.audio_agent import AudioAgent
        from agents.publish_agent import PublishAgent

        # 根据 config 选择 LLM provider（本地模式：ollama / mock）
        llm_provider_name = engine.get("llm_provider", "ollama")
        from providers.mock_provider import MockLLMProvider
        mock_llm = MockLLMProvider() if llm_provider_name == "mock" else None

        agents = [
            IntelligenceAgent(),  # 情报前置（是否执行由 intel_enabled/INTEL_ENABLED 决定）
            ResearchAgent(llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name),
            ScriptAgent(llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name),
            StoryboardAgent(llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name),
            CharacterDesignAgent(
                use_comfyui=use_comfyui, comfy_client=comfy_client,
                # 中文外貌设定对 SD1.5 无效，定妆照需 LLM 转英文人像 prompt
                llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name,
            ),
            ImageGenAgent(use_comfyui=use_comfyui, comfy_client=comfy_client),
            VideoGenAgent(use_comfyui=use_comfyui_video, comfy_client=comfy_client),
            SubtitleAgent(),
            VideoComposeAgent(),
            AudioAgent(),
            PublishAgent(),
        ]
        result = await pipe.run(agents, user_input, resume=resume,
                                enable_review=True)

        # 更新 DB
        db = get_session()
        job = db.query(PipelineJob).filter_by(id=_active[pipeline_id]["job_id"]).first()
        if job:
            job.status = "done" if result.get("success") else "failed"
            job.current_agent = "completed"
            job.result = result.get("results", {})
            if not result.get("success"):
                job.error = result.get("error", result.get("failed_at", ""))
            db.commit()
        db.close()

    except asyncio.CancelledError:
        # CancelledError 继承自 BaseException，不会被下面的 except Exception 捕获，
        # 必须单独收尾；否则任务静默消失、_active / _pipeline_instances 残留。
        # DB 与广播已由 cancel 路由处理，这里只做引用清理后原样抛出。
        logger.warning(f"[{pipeline_id}] 管线已取消，执行已中断")
        _active.pop(pipeline_id, None)
        _pipeline_instances.pop(pipeline_id, None)
        raise

    except Exception as e:
        logger.error(f"[{pipeline_id}] 管线异常: {e}")
        _active[pipeline_id]["status"] = "failed"
        _active[pipeline_id]["error"] = str(e)
        await _sse_broadcast("pipeline_fail", {"error": str(e)}, pipeline_id)
        _wiz_publish(
            pipeline_id, "error", _wiz_step_of(_active[pipeline_id].get("current_agent", "")),
            f"❌ 管线异常：{e}", None,
        )
        try:
            db = get_session()
            job = db.query(PipelineJob).filter_by(id=_active[pipeline_id]["job_id"]).first()
            if job:
                job.status = "failed"
                job.error = str(e)
                db.commit()
            db.close()
        except Exception:
            pass
