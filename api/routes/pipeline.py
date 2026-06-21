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

logger = logging.getLogger(__name__)

# ── 活跃 Pipeline 追踪 ────────────────────
_active: dict[str, dict] = {}
import asyncio
_ws_clients: list[WebSocket] = []
_pipeline_instances: dict[str, object] = {}  # pipeline_id → Pipeline instance (for approve/reject)

# ── Pydantic ──────────────────────────────

class RunRequest(BaseModel):
    input: str
    resume: bool = False

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
    _sse_clients: list[asyncio.Queue] = []

    async def sse_event_sender(q: asyncio.Queue):
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=30)
                yield f"event: {data['event']}\ndata: {json.dumps(data['data'], ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    @app.get("/api/v1/pipeline/events/stream")
    async def sse_stream():
        q: asyncio.Queue = asyncio.Queue()
        _sse_clients.append(q)
        return StreamingResponse(sse_event_sender(q), media_type="text/event-stream")

    async def _sse_broadcast(event: str, data: dict, pipeline_id: str = ""):
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
            if ws in _ws_clients:
                _ws_clients.remove(ws)
        except Exception:
            if ws in _ws_clients:
                _ws_clients.remove(ws)

    # ── 启动管线 ──────────────────────
    @app.post("/api/v1/pipeline/run")
    async def run_pipeline(req: RunRequest):
        text = req.input.strip()
        if not text or len(text) < 2:
            raise HTTPException(400, "输入至少2个字符")

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
            "current_agent": "queued", "progress": 0,
            "summary": {}, "error": "", "started_at": time.time(),
        }

        await _broadcast("pipeline_start", {
            "pipeline_id": pipeline_id, "story_id": _sid,
            "input": text[:80],
        }, pipeline_id)

        import asyncio
        asyncio.create_task(_execute(pipeline_id, _sid, text, req.resume))

        return {"success": True, "pipeline_id": pipeline_id, "story_id": _sid}

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

    # ── 快照（获取 checkpoint 的角色/分镜/视频数据）──
    @app.get("/api/v1/pipeline/snapshot/latest")
    async def get_latest_snapshot():
        cp_dir = Path("storage/checkpoints")
        result = {}
        prefixes = ["script_agent", "storyboard_agent", "character_agent",
                    "image_agent", "video_agent", "subtitle_agent", "compose_agent"]
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

    @app.get("/api/v1/pipeline/snapshot/{pipeline_id}")
    @app.get("/api/v1/pipeline/snapshot/{pipeline_id}")
    async def get_snapshot(pipeline_id: str):
        cp_dir = Path("storage/checkpoints")
        result = {}
        prefixes = ["script_agent", "storyboard_agent", "character_agent",
                    "image_agent", "video_agent", "subtitle_agent", "compose_agent"]
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
        await _broadcast("review_approved", {"pipeline_id": pipeline_id}, pipeline_id)
        return {"success": True, "action": "approved"}

    @app.post("/api/v1/pipeline/reject/{pipeline_id}")
    async def reject(pipeline_id: str, data: dict = {}):
        pipe = _pipeline_instances.get(pipeline_id)
        if not pipe:
            raise HTTPException(404, "Pipeline 不存在")
        pipe.reject_review()
        await _broadcast("review_rejected", {"pipeline_id": pipeline_id, "data": data}, pipeline_id)
        return {"success": True, "action": "rejected"}

    # ── 取消管线 ──────────────────────
    @app.post("/api/v1/pipeline/cancel/{pipeline_id}")
    async def cancel(pipeline_id: str):
        entry = _active.get(pipeline_id)
        if not entry:
            raise HTTPException(404, "Pipeline 不存在")
        entry["status"] = "cancelled"
        await _broadcast("pipeline_cancelled", {"pipeline_id": pipeline_id}, pipeline_id)
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

# ── 后台执行 ──────────────────────────

async def _execute(pipeline_id: str, story_id: int, user_input: str, resume: bool):
    try:
        pipe = Pipeline(pipeline_id=pipeline_id)
        _pipeline_instances[pipeline_id] = pipe

        # 设置 callbacks
        async def on_start(name: str, idx: int, total: int):
            prog = int((idx / total) * 100)
            _active[pipeline_id].update(current_agent=name, progress=prog, status="running")
            await _broadcast("agent_start", {
                "agent": name, "step": idx + 1, "total": total, "progress": prog,
            }, pipeline_id)

        async def on_complete(name: str, idx: int, total: int, meta: dict):
            prog = int(((idx + 1) / total) * 100)
            _active[pipeline_id]["current_agent"] = name
            _active[pipeline_id]["progress"] = prog
            _active[pipeline_id]["summary"][name] = meta
            await _broadcast("agent_done", {
                "agent": name, "step": idx + 1, "total": total,
                "progress": prog, "meta": meta,
            }, pipeline_id)

        async def on_fail(name: str, idx: int, total: int, error: str):
            _active[pipeline_id].update(current_agent=name, status="failed", error=error)
            await _broadcast("agent_fail", {
                "agent": name, "step": idx + 1, "total": total, "error": error,
            }, pipeline_id)

        async def on_review(agent_name: str, reason: str, data: dict):
            _active[pipeline_id].update(status="review", current_agent=agent_name)
            await _broadcast("agent_blocked", {
                "agent": agent_name, "reason": reason,
            }, pipeline_id)

        async def on_done(final: dict):
            _active[pipeline_id].update(current_agent="completed", progress=100, status="done")
            await _broadcast("pipeline_done", {
                "summary": _active[pipeline_id].get("summary", {}),
                "success": final.get("success", False),
            }, pipeline_id)

        pipe.set_callbacks(on_start, on_complete, on_fail, on_done, on_review)

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
            comfy_host = cfg.get("comfyui", {}).get("host", "127.0.0.1")
            comfy_port = cfg.get("comfyui", {}).get("port", 8188)
            comfy_timeout = cfg.get("comfyui", {}).get("timeout", 600)
            comfy_client = ComfyUIClient(server_addr=comfy_host, server_port=comfy_port)

        from agents.video_compose_agent import VideoComposeAgent
        from agents.publish_agent import PublishAgent

        agents = [
            ScriptAgent(),
            StoryboardAgent(),
            CharacterDesignAgent(use_comfyui=use_comfyui, comfy_client=comfy_client),
            ImageGenAgent(use_comfyui=use_comfyui, comfy_client=comfy_client),
            VideoGenAgent(use_comfyui=use_comfyui_video, comfy_client=comfy_client),
            SubtitleAgent(),
            VideoComposeAgent(),
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

    except Exception as e:
        logger.error(f"[{pipeline_id}] 管线异常: {e}")
        _active[pipeline_id]["status"] = "failed"
        _active[pipeline_id]["error"] = str(e)
        await _broadcast("pipeline_fail", {"error": str(e)}, pipeline_id)
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
