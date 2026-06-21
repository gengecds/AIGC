"""Agent 4 - 出图（骨架，Phase 2 实现）

传入分镜列表 → ComfyUI+SD 批量出图（ControlNet锁角色）→ 输出图片列表
开发阶段使用 MockImageProvider 占位
"""

import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.base import ImageProvider

logger = logging.getLogger(__name__)


class ImageGenAgent(Agent):
    """Agent 4：批量出图（ComfyUI + SD）"""

    name = "image_agent"

    def __init__(self, use_comfyui: bool = False, comfy_client=None,
                 image_provider: ImageProvider | None = None):
        super().__init__(name="image_agent")
        if image_provider is not None:
            self.image_provider = image_provider
        elif use_comfyui:
            from providers.comfyui_provider import ComfySDImageProvider
            self.image_provider = ComfySDImageProvider(client=comfy_client)
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
                # 取第一个角色名作为 ref_image 查询 key
                char_list = shot.get("characters") or []
                first_char = char_list[0] if isinstance(char_list, list) and char_list else None
                ref_path = None
                if character_assets and first_char:
                    asset = character_assets.get(first_char, {})
                    ref_path = asset.get("controlnet_ref_path")
                shot_data.append({
                    "shot_id": str(shot["shot_id"]),
                    "sd_prompt": shot.get("sd_prompt", ""),
                    "sd_negative": shot.get("sd_negative", ""),
                    "seed": shot.get("seed", -1),
                    "ref_image": ref_path,
                    "controlnet_type": "control_v11p_sd15_canny",
                    "controlnet_image": ref_path,
                    "controlnet_strength": 0.65,
                })

            # batch_generate 返回 list[dict]，转为 {shot_id: file_info} 格式
            image_list = await self.image_provider.batch_generate(shot_data)
            # image_list: [{'filename':...,'subfolder':...,'type':...,'prompt_id':...}]
            # 映射为 shot_id → file_info
            ep_images = {}
            for i, img_info in enumerate(image_list):
                sid = shot_data[i].get("shot_id", str(i))
                ep_images[sid] = img_info
            all_results[f"ep_{ep_num}"] = ep_images

        total = sum(len(v) for v in all_results.values())
        result = AgentResult(
            success=True,
            data={"images": all_results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_images": total,
            },
        )
        logger.info(f"[ImageGenAgent] 完成: {total}张图")
        return result
