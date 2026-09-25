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
        scores: list[int] = []

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
            # 注入 Skills 质量块：摄影师实拍 + 人体结构，让定妆照"人像人"且稳定可复用
            from skills.resolver import photoreal_block, anatomy_block
            prompt = (
                f"Portrait of {name}, {appearance}, "
                f"{gender}, front view, upper body, "
                f"looking at camera, detailed face, "
                f"{photoreal_block()}, {anatomy_block()}, "
                f"masterpiece, best quality, highly detailed, photorealistic, 8k"
            )

            first_img, qc = await self._portrait_with_qc(
                name, prompt, seed=hash(name) % (2**31)
            )
            image_path = first_img.get("filename", f"storage/output/char_{name}.png")
            if qc.get("score") is not None:
                scores.append(qc["score"])
                logger.info(f"[CharacterAgent] 定妆照质检 {name}: {qc.get('reason')} "
                            f"tags={qc.get('tags')} defects={qc.get('defects')}")

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
                "qc_checked": len(scores),
                "qc_avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            },
        )

    async def _portrait_with_qc(self, name: str, prompt: str, seed: int):
        """生成定妆照 + 质检自评；不合格换 seed 自动重生成（最多 max_retries 次）。

        返回 (first_img, qc)；质检不可用时 qc 为 skipped（放行），绝不中断管线。
        """
        from providers.comfyui_provider import sync_image_to_local
        from providers.image_critic import ImageCritic, next_seed

        critic = ImageCritic()
        best_img: dict = {}
        best_qc: dict = {}
        cur_seed = seed
        for attempt in range(critic.max_retries + 1 if critic.enabled else 1):
            image_results = await self.image_provider.generate(prompt=prompt, seed=cur_seed)
            first = image_results[0] if isinstance(image_results, list) and image_results else {}
            if not first:
                continue
            best_img = first
            if not critic.enabled:
                break
            local = sync_image_to_local(first)
            qc = await critic.evaluate(local, expect=f"角色定妆照：{name}")
            best_qc = qc
            if qc["passed"]:
                break
            if attempt < critic.max_retries:
                logger.warning(f"[CharacterAgent] 定妆照 {name} 质检不达标（{qc.get('reason')}），重生成")
                cur_seed = next_seed(cur_seed)
        return best_img, best_qc