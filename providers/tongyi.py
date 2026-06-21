"""通义万相 Provider — 开发占位用（免GPU）

使用通义万相 API 代替 ComfyUI，开发阶段无 GPU 也能跑完整管线。

注意：
- 无 ControlNet，角色一致性靠 prompt 硬调
- 免费额度有限，仅用于开发和调试
"""

import base64
import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from providers.base import ImageProvider

logger = logging.getLogger(__name__)

# 通义万相 API 端点
TONGYI_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"


class TongyiImageProvider(ImageProvider):
    """通义万相文生图（开发备用，免 GPU）"""

    def __init__(self, api_key: Optional[str] = None, output_dir: str = "storage"):
        self.api_key = api_key or ""
        if not self.api_key:
            logger.warning("[TongyiImageProvider] 未设置 API Key，会返回 mock 图片")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate(self, prompt: str, ref_image: Optional[str] = None,
                       seed: Optional[int] = None, **kwargs) -> str:
        """生成单张图片，返回本地路径"""
        if not self.api_key:
            logger.info(f"[TongyiImageProvider] Mock mode: {prompt[:40]}...")
            # 返回一个占位路径
            fname = f"tongyi_mock_{abs(hash(prompt)) % 10000:04d}.png"
            placeholder = self.output_dir / fname
            if not placeholder.exists():
                placeholder.write_text("")  # 空文件占位
            return str(placeholder)

        body = {
            "model": "wanx2.1-t2i-plus",
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "size": "1024*1024",
                "n": 1,
            },
        }
        if seed is not None:
            body["parameters"]["seed"] = seed

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(TONGYI_BASE_URL, json=body, headers=headers)
            data = resp.json()

        if resp.status_code != 200 or data.get("code"):
            logger.error(f"[TongyiImageProvider] API error: {data}")
            raise RuntimeError(f"通义万相 API 错误: {data}")

        # 下载图片
        image_url = data["output"]["results"][0]["url"]
        async with httpx.AsyncClient(timeout=30) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()

        fname = f"tongyi_{abs(hash(prompt)) % 100000:05d}.png"
        local_path = self.output_dir / fname
        local_path.write_bytes(img_resp.content)
        logger.info(f"[TongyiImageProvider] 保存: {local_path} ({len(img_resp.content)/1024:.0f}KB)")

        return str(local_path)
