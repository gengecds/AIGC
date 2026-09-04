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

        # 记录本次管线所选风格（可多选，第一个为主风格）→ 相关 Provider 据此选 model
        from config.style_resolver import set_active_styles
        set_active_styles(_to_styles(req.style))

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

        await _sse_broadcast("pipeline_start", {
            "pipeline_id": pipeline_id, "story_id": _sid,
            "input": text[:80],
        }, pipeline_id)

        import asyncio
        asyncio.create_task(_execute(pipeline_id, _sid, text, req.resume, req.style))

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

        - LLM: 调 Ollama /api/tags 拿本地已 pull 的模型
        - 出图: 调 ComfyUI /object_info 的 CheckpointLoaderSimple 拿 checkpoints
        任一服务不可用则返回空列表，前端会显示提示，不影响其它功能。
        """
        cfg_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

        # 1. Ollama LLM 模型
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

        # 2. ComfyUI 出图 checkpoint + 视频扩散模型（按类别区分，避免选错）
        ckpt_models: list[str] = []
        video_models: list[str] = []
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
            ckpt_models = await comfy_client.list_models("checkpoints")
            # 视频扩散模型区：优先 diffusion_models，其次 checkpoints 里含 ltx/wan 等视频模型名
            for m in await comfy_client.list_models("diffusion_models"):
                if m not in video_models:
                    video_models.append(m)
            for m in ckpt_models:
                mn = m.lower()
                if mn.startswith(("ltx", "wan", "svd", "hunyuan", "mochi", "cogvideo")) and m not in video_models:
                    video_models.append(m)
            await comfy_client.close()
        except Exception as e:
            logger.warning(f"ComfyUI list_models 获取失败: {e}")

        from config.style_resolver import current_style, current_styles
        return {
            "llm_models": llm_models,
            "image_ckpts": ckpt_models,
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

    @app.get("/api/v1/pipeline/snapshot/{pipeline_id}")
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
        await _sse_broadcast("pipeline_cancelled", {"pipeline_id": pipeline_id}, pipeline_id)
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

        async def on_review(agent_name: str, reason: str, data: dict):
            _active[pipeline_id].update(
                status="review", current_agent=agent_name, review_reason=reason,
            )
            await _sse_broadcast("agent_blocked", {
                "agent": agent_name, "reason": reason, "pipeline_id": pipeline_id,
            }, pipeline_id)

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
            IntelligenceAgent(),  # 情报前置（P2，默认关闭，开关控制是否执行）
            ResearchAgent(llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name),
            ScriptAgent(llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name),
            StoryboardAgent(llm_provider=mock_llm if llm_provider_name == "mock" else llm_provider_name),
            CharacterDesignAgent(use_comfyui=use_comfyui, comfy_client=comfy_client),
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

    except Exception as e:
        logger.error(f"[{pipeline_id}] 管线异常: {e}")
        _active[pipeline_id]["status"] = "failed"
        _active[pipeline_id]["error"] = str(e)
        await _sse_broadcast("pipeline_fail", {"error": str(e)}, pipeline_id)
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
