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
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── 生命周期 ────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.config = cfg
    # 初始化数据库
    db_url = cfg["database"]["url"]
    os.makedirs(Path(db_url.replace("sqlite:///", "")).parent, exist_ok=True)
    get_engine(db_url)
    logger.info("数据库已初始化")
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


# ── 导入路由（后续扩展） ─────────────

if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    uvicorn.run(
        "api.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=True,
    )
