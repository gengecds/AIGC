"""MockProvider - 开发阶段占位实现

不依赖任何外部 API/GPU，返回假数据验证 Pipeline 数据流。
"""

import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)


class MockImageProvider:
    """Mock 出图 — 返回假路径，不调任何 API"""

    def __init__(self, output_dir: str = "storage/output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[MockImageProvider] 已初始化，0 API 费用")

    async def generate(self, prompt: str, ref_image: str | None = None,
                       seed: int = -1) -> str:
        """返回一个假的占位图片路径"""
        fake_name = f"mock_img_{abs(hash(prompt)) % 10000}_{seed}.png"
        fake_path = self.output_dir / fake_name
        # 创建一个1x1的假PNG占位
        if not fake_path.exists():
            fake_path.write_bytes(
                b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
            )
        logger.info(f"[Mock] 出图: {fake_path}")
        return str(fake_path)

    async def batch_generate(self, shots: list[dict]) -> dict[str, str]:
        """批量出图"""
        results = {}
        for shot in shots:
            path = await self.generate(
                prompt=shot.get("sd_prompt", ""),
                ref_image=shot.get("ref_image"),
                seed=shot.get("seed", random.randint(0, 2**31)),
            )
            results[shot["shot_id"]] = path
        return results


class MockVideoProvider:
    """Mock 图生视频 — 返回假路径"""

    def __init__(self, output_dir: str = "storage/output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[MockVideoProvider] 已初始化，0 API 费用")

    async def generate(self, input_image: str, prompt: str = "",
                       duration: int = 5) -> str:
        """返回一个假的占位视频路径"""
        fake_name = f"mock_vid_{abs(hash(input_image)) % 10000}_{duration}s.mp4"
        fake_path = self.output_dir / fake_name
        if not fake_path.exists():
            fake_path.write_text("mock video placeholder")
        logger.info(f"[Mock] 视频: {fake_path}")
        return str(fake_path)

    async def batch_generate(self, images: list[dict]) -> dict[str, str]:
        """批量图生视频"""
        results = {}
        for img in images:
            path = await self.generate(
                input_image=img["image_path"],
                prompt=img.get("motion", ""),
                duration=img.get("duration", 5),
            )
            results[img["shot_id"]] = path
        return results
