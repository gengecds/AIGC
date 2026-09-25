"""FastAPI 应用入口"""

import os
import yaml
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()  # 加载 .env 到环境变量

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from db.database import get_engine, close_engine

from api.shutdown import install_signal_hook, request_shutdown, reset_shutdown

# 日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ai-drama")


# ── 配置加载 ────────────────────────────

def load_config() -> dict:
    """加载配置（通过配置中心）"""
    from config.settings import settings
    return {
        "server": {"host": settings.frontend.host, "port": settings.frontend.port},
        "storage": {"checkpoint_dir": str(settings.storage.checkpoint_dir)},
        "pipeline": {"max_retries": int(settings.pipeline.max_retries)},
    }


# ── 生命周期 ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.config = cfg
    logger.info("应用启动中...")
    # 确保存储目录存在
    for d in ["storage/output", "storage/checkpoints"]:
        os.makedirs(d, exist_ok=True)
    logger.info("应用启动完毕")
    reset_shutdown()
    # 关闭信号必须在「uvicorn 等连接」之前触发，所以链到信号处理器（reload 走 SIGTERM、
    # Ctrl+C 走 SIGINT），而不是等到 lifespan 退出——那时连接早被硬 cancel 了。
    restore_signals = install_signal_hook()
    yield
    # 兜底：非信号路径退出（如 lifespan 被直接关闭）也要唤醒各 SSE 生成器收尾
    request_shutdown()
    logger.info("已通知 SSE 长连接收尾")
    restore_signals()
    close_engine()
    logger.info("应用已关闭")


# ── 应用 ────────────────────────────

app = FastAPI(
    title="AI 漫剧创作平台",
    description="一句话/大纲/小说 → 完整漫剧视频",
    version="0.1.0",
    lifespan=lifespan,
)

# 前端/本地工具跨源访问（Vite dev server、file:// 打开等场景）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 路由（占位） ─────────────────────

@app.get("/")
async def root():
    return {"message": "AI 漫剧创作平台 - API v0.1", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── 静态文件 ─────────────────────────

# 前端文件
frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/frontend", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

# storage/output 作为视频源
output_dir = Path(__file__).parent.parent / "storage" / "output"
os.makedirs(str(output_dir), exist_ok=True)
app.mount("/storage/output", StaticFiles(directory=str(output_dir)), name="output")

# ComfyUI 输出目录（SD/LTX 生成的图片视频直接落在这里）
comfyui_output_dir = Path(os.environ.get(
    "COMFY_OUTPUT_DIR",
    "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/output",
))
os.makedirs(str(comfyui_output_dir), exist_ok=True)
app.mount("/comfyui-output", StaticFiles(directory=str(comfyui_output_dir)), name="comfyui-output")


# ── 导入路由 ─────────────────────────

from api.routes.pipeline import register_pipeline_routes
register_pipeline_routes(app)

from api.routes.intel import register_intel_routes
register_intel_routes(app)

from api.routes.skills import register_skills_routes
register_skills_routes(app)

from api.routes.auth import router as auth_router
app.include_router(auth_router)

# 音频域：参考音频上传 / 音色克隆（域 B 独立）
from api.routes.audio_router import router as audio_router
app.include_router(audio_router)

# Pipeline SSE 进度事件流（域 D 独立，供前端 5 步向导式 Review UI 订阅）
# 端点：GET /pipeline/events?run_id=xxx（订阅真实管线 run_id，__demo__ 已移除）
from api.routes.pipeline_events_router import router as pipeline_events_router
app.include_router(pipeline_events_router)

# ComfyUI 工作流模板导出（前端下载后在 ComfyUI 里查看/复用）
from api.routes.comfyui_router import router as comfyui_router
app.include_router(comfyui_router)


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "api.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=True,
        # SSE 长连接（/pipeline/events 等）不会主动结束，热重载关旧进程时 uvicorn 会一直卡在
        # 「Waiting for connections to close」等连接释放（该参数默认 None = 无限等），
        # reloader 的 process.join() 随之永久阻塞。设超时后到点强制取消残留连接与任务。
        timeout_graceful_shutdown=3,
    )
