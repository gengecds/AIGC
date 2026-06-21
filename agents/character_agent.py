"""Agent 3 - 角色定妆照 + 双轨制资产

- 检查角色是否已入库（is_asset_library）
- 已入库 → 跳过，复用ControlNet参考图
- 未入库 → ComfyUI/Mock生成定妆照 → 入库
"""

import json, logging, os
from datetime import datetime
from typing import Optional, Union

from agents.base import Agent, AgentResult
from providers.base import ImageProvider

logger = logging.getLogger(__name__)


class CharacterDesignAgent(Agent):
    """Agent 3：角色定妆照 + 资产入库"""

    name = "character_agent"

    def __init__(self, use_comfyui: bool = False,
                 comfy_client=None,
                 image_provider: Union[str, ImageProvider, None] = None):
        super().__init__(name="character_agent")
        self._comfy_client = comfy_client
        if isinstance(image_provider, ImageProvider):
            self.image_provider = image_provider
        elif use_comfyui and comfy_client:
            from providers.comfyui_provider import ComfySDImageProvider
            self.image_provider = ComfySDImageProvider(client=comfy_client)
        else:
            from providers.mock_provider import MockImageProvider
            self.image_provider = MockImageProvider()

    async def run(self, script: dict, db_assets: dict | None = None) -> AgentResult:
        logger.info("[CharacterAgent] 开始处理角色定妆照")

        characters = script.get("characters", [])
        if not characters:
            return AgentResult(success=False, error="剧本中没有角色数据")

        db_assets = db_assets or {}
        results = []

        for char in characters:
            name = char.get("name", "")
            existing = db_assets.get(name)

            if existing and existing.get("is_asset_library"):
                logger.info(f"[双轨制] 角色'{name}'已入库，跳过生成")
                results.append({"name": name, "status": "skipped", "asset": existing})
                continue

            # 生成定妆照 - 角色正面半身
            appearance = char.get("appearance", "")
            gender = char.get("gender", "男")
            prompt = (
                f"Portrait of {name}, {appearance}, "
                f"{gender}, front view, upper body, "
                f"looking at camera, detailed face, "
                f"masterpiece, best quality, highly detailed"
            )

            image_results = await self.image_provider.generate(
                prompt=prompt,
                seed=hash(name) % (2**31),
            )
            # image_results 是 list[dict]，取第一个
            first_img = image_results[0] if isinstance(image_results, list) else {}
            image_path = first_img.get("filename", f"storage/output/char_{name}.png")

            asset = {
                "name": name,
                "gender": gender,
                "appearance": appearance,
                "personality": char.get("personality", ""),
                "role": char.get("role", ""),
                "portrait_path": image_path,
                "controlnet_ref_path": image_path,
                "is_asset_library": True,
                "generated_at": datetime.utcnow().isoformat(),
            }
            results.append({"name": name, "status": "generated", "asset": asset})
            logger.info(f"[CharacterAgent] 生成定妆照: {name} -> {image_path}")

        return AgentResult(
            success=True,
            data={"characters": results},
            metadata={
                "agent": self.name, "timestamp": datetime.utcnow().isoformat(),
                "total": len(results),
                "generated": sum(1 for r in results if r["status"] == "generated"),
                "skipped": sum(1 for r in results if r["status"] == "skipped"),
            },
        )
