"""Agent 5 - 图生视频（Phase 2: ComfyUI+HunyuanVideo）

传入图片列表 → ComfyUI+HunyuanVideo 批量图生视频 → 输出视频分段
开发阶段使用 MockVideoProvider 占位，生产切换 ComfyHunyuanVideoProvider
"""

import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.mock_provider import MockVideoProvider

logger = logging.getLogger(__name__)


class VideoGenAgent(Agent):
    """Agent 5：批量图生视频（ComfyUI + HunyuanVideo）"""

    name = "video_agent"

    def __init__(self, use_comfyui: bool = False):
        super().__init__(name="video_agent")
        if use_comfyui:
            from providers.comfyui_provider import ComfyHunyuanVideoProvider
            self.video_provider = ComfyHunyuanVideoProvider()
        else:
            self.video_provider = MockVideoProvider()

    async def run(self, images_result,
                  storyboard: dict | None = None) -> AgentResult:
        logger.info("[VideoGenAgent] 开始图生视频")

        # 兼容 AgentResult 对象和字典
        if hasattr(images_result, 'data'):
            images_data = images_result.data or {}
        else:
            images_data = images_result.get("data", {})

        all_results = {}
        for ep_key, images in (images_data.get("images", {}) or {}).items():
            shots = list(images.items())  # [(shot_id, image_path), ...]
            video_data = [
                {"shot_id": sid, "image_path": path}
                for sid, path in shots
            ]
            videos = await self.video_provider.batch_generate(video_data)
            all_results[ep_key] = videos

        result = AgentResult(
            success=True,
            data={"videos": all_results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_videos": sum(len(v) for v in all_results.values()),
            },
        )
        logger.info(f"[VideoGenAgent] 完成: {result.metadata['total_videos']}段视频")
        return result
