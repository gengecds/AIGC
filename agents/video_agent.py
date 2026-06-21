"""Agent 5 - 图生视频（Phase 2: ComfyUI+HunyuanVideo）

传入图片列表 → ComfyUI+HunyuanVideo 批量图生视频 → 输出视频分段
"""

import logging
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class VideoGenAgent(Agent):
    """Agent 5：批量图生视频"""

    name = "video_agent"

    def __init__(self, use_comfyui: bool = False, comfy_client=None):
        super().__init__(name="video_agent")
        if use_comfyui and comfy_client:
            from providers.comfyui_provider import ComfyHunyuanVideoProvider
            self.video_provider = ComfyHunyuanVideoProvider(client=comfy_client)
        else:
            from providers.mock_provider import MockVideoProvider
            self.video_provider = MockVideoProvider()

    async def run(self, images_result, storyboard: dict | None = None) -> AgentResult:
        logger.info("[VideoGenAgent] 开始图生视频")

        # 兼容 AgentResult 对象和字典
        if hasattr(images_result, 'data'):
            images_data = images_result.data or {}
        else:
            images_data = images_result if isinstance(images_result, dict) else {}

        images = images_data.get("images", {}) or {}

        all_results = {}
        for ep_key, ep_images in images.items():
            # ep_images 是 {shot_id: {filename, subfolder, type, prompt_id}}
            video_data = []
            for sid, img_info in ep_images.items():
                video_data.append({
                    "shot_id": sid,
                    "image_path": img_info.get("filename", ""),
                    "subfolder": img_info.get("subfolder", ""),
                    "prompt_id": img_info.get("prompt_id", ""),
                })

            videos = await self.video_provider.batch_generate(video_data)
            # videos: list[dict]，映射回 shot_id
            ep_videos = {}
            for i, vid_info in enumerate(videos):
                sid = video_data[i]["shot_id"]
                ep_videos[sid] = vid_info
            all_results[ep_key] = ep_videos

        return AgentResult(
            success=True,
            data={"videos": all_results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_videos": sum(len(v) for v in all_results.values()),
            },
        )
