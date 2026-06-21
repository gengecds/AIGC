"""FastAPI 应用入口"""

import os
import yaml
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db.database import get_engine, close_engine

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
    yield
    close_engine()
    logger.info("应用已关闭")


# ── 应用 ────────────────────────────

app = FastAPI(
    title="AI 漫剧创作平台",
    description="一句话/大纲/小说 → 完整漫剧视频",
    version="0.1.0",
    lifespan=lifespan,
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


# ── 导入路由 ─────────────────────────

from api.routes.pipeline import register_pipeline_routes
register_pipeline_routes(app)


if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "api.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=True,
    )
