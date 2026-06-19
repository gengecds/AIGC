"""Agent 3 - 角色定妆照 + 双轨制资产

- 检查角色是否已入库（is_asset_library）
- 已入库 → 跳过，复用ControlNet参考图
- 未入库 → MockImageProvider生成定妆照 → 入库
"""

import json
import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.mock_provider import MockImageProvider

logger = logging.getLogger(__name__)


class CharacterDesignAgent(Agent):
    """Agent 3：角色定妆照 + 资产入库"""

    name = "character_agent"

    def __init__(self):
        super().__init__(name="character_agent")
        # Phase 2 替换为 ComfySDImageProvider
        self.image_provider = MockImageProvider()

    async def run(self, script: dict, db_assets: dict | None = None) -> AgentResult:
        """
        script: Agent 1 输出的剧本JSON（含characters）
        db_assets: 从数据库读取的已有资产库 {character_name: asset_info}
        """
        logger.info("[CharacterAgent] 开始处理角色定妆照")

        characters = script.get("characters", [])
        if not characters:
            return AgentResult(
                success=False,
                error="剧本中没有角色数据",
            )

        db_assets = db_assets or {}
        results = []

        for char in characters:
            name = char.get("name", "")
            existing = db_assets.get(name)

            if existing and existing.get("is_asset_library"):
                logger.info(f"[双轨制] 角色'{name}'已入库，跳过生成")
                results.append({
                    "name": name,
                    "status": "skipped",
                    "asset": existing,
                })
                continue

            # 生成定妆照
            prompt = f"Portrait of {name}, {char.get('appearance', '')}"
            image_path = await self.image_provider.generate(
                prompt=prompt,
                seed=hash(name) % (2**31),
            )

            asset = {
                "name": name,
                "gender": char.get("gender", ""),
                "appearance": char.get("appearance", ""),
                "personality": char.get("personality", ""),
                "role": char.get("role", ""),
                "portrait_path": image_path,
                "controlnet_ref_path": image_path,  # 同图作为ControlNet参考
                "is_asset_library": True,
                "generated_at": datetime.utcnow().isoformat(),
            }

            results.append({
                "name": name,
                "status": "generated",
                "asset": asset,
            })
            logger.info(f"[CharacterAgent] 生成定妆照: {name} -> {image_path}")

        result = AgentResult(
            success=True,
            data={"characters": results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total": len(results),
                "generated": sum(1 for r in results if r["status"] == "generated"),
                "skipped": sum(1 for r in results if r["status"] == "skipped"),
            },
        )

        logger.info(
            f"[CharacterAgent] 完成: "
            f"生成{result.metadata['generated']}, "
            f"跳过{result.metadata['skipped']}"
        )
        return result
