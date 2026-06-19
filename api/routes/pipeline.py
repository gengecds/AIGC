"""Pipeline API 路由"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent
from agents.image_agent import ImageGenAgent
from agents.video_agent import VideoGenAgent
from agents.subtitle_agent import SubtitleAgent
from agents.compose_agent import ComposeAgent
from pipeline.scheduler import Pipeline, PipelineState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["pipeline"])


@router.post("/pipeline/run")
async def run_pipeline(body: dict):
    """执行完整管线：输入文本 → 漫剧视频"""
    user_input = body.get("input", "")
    resume = body.get("resume", False)

    if not user_input:
        raise HTTPException(status_code=400, detail="input 不能为空")
    if len(user_input) < 2:
        raise HTTPException(status_code=400, detail="输入太短，至少2个字符")

    pipeline = Pipeline()

    # 按顺序创建 Agent（Mock 模式）
    agents = [
        ScriptAgent(),
        StoryboardAgent(),
        CharacterDesignAgent(),
        ImageGenAgent(),
        VideoGenAgent(),
        SubtitleAgent(),
        ComposeAgent(),
    ]

    result = await pipeline.run(agents, user_input, resume=resume)

    return {
        "success": result.get("success", False),
        "pipeline_id": result.get("pipeline_id", ""),
        "summary": _summarize(result),
        "results": result.get("results", {}),
    }


@router.get("/pipeline/status/{pipeline_id}")
async def pipeline_status(pipeline_id: str):
    """查询管线执行状态"""
    checkpoint_dir = Path("storage/checkpoints")
    if not checkpoint_dir.exists():
        return {"pipeline_id": pipeline_id, "status": "not_found"}

    completed = []
    for f in sorted(checkpoint_dir.glob("*_checkpoint.json")):
        agent_name = f.name.replace("_checkpoint.json", "")
        completed.append(agent_name)

    return {
        "pipeline_id": pipeline_id,
        "completed_agents": completed,
        "total": len(completed),
    }


def _summarize(result: dict) -> dict:
    """生成执行摘要"""
    results = result.get("results", {})
    summary = {}

    for name, r in results.items():
        meta = r.get("metadata", {})
        if name == "script_agent":
            summary["script"] = {
                "title": r.get("data", {}).get("title", ""),
                "characters": meta.get("characters", 0),
                "episodes": meta.get("episodes", 0),
            }
        elif name == "storyboard_agent":
            summary["storyboard"] = {
                "total_shots": meta.get("total_shots", 0),
            }
        elif name == "character_agent":
            summary["characters"] = {
                "total": meta.get("total", 0),
                "generated": meta.get("generated", 0),
                "skipped": meta.get("skipped", 0),
            }
        elif name == "image_agent":
            summary["images"] = {
                "total": meta.get("total_images", 0),
            }
        elif name == "video_agent":
            summary["videos"] = {
                "total": meta.get("total_videos", 0),
            }
        elif name == "subtitle_agent":
            summary["subtitles"] = {
                "files": meta.get("files", 0),
            }
        elif name == "compose_agent":
            summary["published"] = {
                "episodes": meta.get("episodes", 0),
            }

    summary["success"] = result.get("success", False)
    if not summary["success"]:
        summary["failed_at"] = result.get("failed_at", "unknown")

    return summary
