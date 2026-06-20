"""Agent 4 - 出图（骨架，Phase 2 实现）

传入分镜列表 → ComfyUI+SD 批量出图（ControlNet锁角色）→ 输出图片列表
开发阶段使用 MockImageProvider 占位
"""

import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.mock_provider import MockImageProvider

logger = logging.getLogger(__name__)


class ImageGenAgent(Agent):
    """Agent 4：批量出图（ComfyUI + SD）"""

    name = "image_agent"

    def __init__(self, use_comfyui: bool = False):
        super().__init__(name="image_agent")
        if use_comfyui:
            from providers.comfyui_provider import ComfySDImageProvider
            self.image_provider = ComfySDImageProvider()
        else:
            from providers.mock_provider import MockImageProvider
            self.image_provider = MockImageProvider()

    async def run(self, storyboard: dict,
                  character_assets: dict | None = None) -> AgentResult:
        logger.info("[ImageGenAgent] 开始批量出图")

        episodes = storyboard.get("episodes", [])
        all_results = {}

        for ep in episodes:
            ep_num = ep.get("episode_number", 1)
            shots = ep.get("shots", [])

            shot_data = []
            for shot in shots:
                shot_data.append({
                    "shot_id": str(shot["shot_id"]),
                    "sd_prompt": shot.get("sd_prompt", ""),
                    "sd_negative": shot.get("sd_negative", ""),
                    "seed": shot.get("seed", -1),
                    "ref_image": (
                        character_assets.get(
                            shot.get("characters", [None])[0] if isinstance(shot.get("characters"), list) else shot.get("characters", None),
                            {}
                        ).get("controlnet_ref_path")
                    ) if character_assets else None,
                })

            images = await self.image_provider.batch_generate(shot_data)
            all_results[f"ep_{ep_num}"] = images

        result = AgentResult(
            success=True,
            data={"images": all_results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_images": sum(len(v) for v in all_results.values()),
            },
        )
        logger.info(f"[ImageGenAgent] 完成: {result.metadata['total_images']}张图")
        return result
