"""ComfyUI Provider - 通过 ComfyUI API 实现 SD 出图和视频生成"""

import logging
from datetime import datetime
from typing import Optional

from providers.base import ImageProvider, VideoProvider
from providers.comfyui.client import ComfyUIClient

logger = logging.getLogger(__name__)


class ComfySDImageProvider(ImageProvider):
    """ComfyUI + SD 图片生成"""

    def __init__(self, client: Optional[ComfyUIClient] = None):
        self.client = client or ComfyUIClient()
        self._models = []

    async def _ensure_models(self):
        if not self._models:
            self._models = await self.client.list_models()
            if not self._models:
                raise RuntimeError("ComfyUI 无可用 checkpoint 模型")
        return self._models

    async def generate(
        self,
        prompt: str,
        ref_image: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> str:
        """单张图片生成"""
        models = await self._ensure_models()
        ckpt = models[0]

        workflow = ComfyUIClient.build_txt2img_workflow(
            ckpt_name=ckpt,
            prompt=prompt,
            negative_prompt=kwargs.get("negative_prompt", ""),
            width=kwargs.get("width", 1024),
            height=kwargs.get("height", 1024),
            seed=seed or 42,
            steps=kwargs.get("steps", 20),
            cfg=kwargs.get("cfg", 7.5),
        )

        resp = await self.client.queue_prompt(workflow)
        prompt_id = resp.get("prompt_id", "")

        result = await self.client.wait_for_completion(prompt_id)
        if not result["success"]:
            raise RuntimeError(f"ComfyUI 生成失败: {result.get('error', '未知错误')}")

        return prompt_id

    async def batch_generate(
        self,
        shots: list[dict],
    ) -> list[str]:
        """批量出图"""
        results = []
        for shot in shots:
            path = await self.generate(
                prompt=shot.get("sd_prompt", ""),
                seed=shot.get("seed"),
                ref_image=shot.get("ref_image"),
            )
            results.append(path)
        return results


class ComfyHunyuanVideoProvider(VideoProvider):
    """ComfyUI + HunyuanVideo 视频生成"""

    def __init__(self, client: Optional[ComfyUIClient] = None):
        self.client = client or ComfyUIClient()

    async def generate(
        self,
        input_image: str,
        prompt: str = "",
        duration: int = 5,
        **kwargs,
    ) -> str:
        """图生视频（从 workflow json 构建）"""
        workflow = self._build_workflow(input_image, prompt, duration)
        resp = await self.client.queue_prompt(workflow)
        prompt_id = resp.get("prompt_id", "")
        result = await self.client.wait_for_completion(prompt_id)
        if not result["success"]:
            raise RuntimeError(f"HunyuanVideo 生成失败: {result.get('error')}")
        return prompt_id

    @staticmethod
    def _build_workflow(
        input_image: str,
        prompt: str = "",
        duration: int = 5,
    ) -> dict:
        """构建 HunyuanVideo 图生视频工作流

        节点类型参考 AutoDL 实例上安装的 ComfyUI-HunyuanVideoWrapper:
          - HyVideoModelLoader
          - HyVideoPromptEncode
          - HyVideoSampler
          - HyVideoDecode
        """
        # 帧数 = 24fps * 秒数
        frame_count = 24 * duration

        return {
            # 1. 模型加载
            "1": {
                "class_type": "HyVideoModelLoader",
                "inputs": {
                    "model_name": "HunyuanVideo/hunyuan_video_720_cfgdistill_fp8_e4m3fn.safetensors",
                    "model_dtype": "bf16",
                    "weight_dtype": "fp8_e4m3fn",
                    "device": "main_device",
                    "attn_mode": "flash_attn_varlen",
                },
            },
            # 2. 参考图加载
            "2": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": input_image,
                },
            },
            # 3. 图像重采样（适配模型分辨率）
            "3": {
                "class_type": "HyVideoImageToVideo",
                "inputs": {
                    "model": ["1", 0],
                    "images": ["2", 0],
                    "prompt": prompt,
                    "resolution": "848x480",
                    "video_length": frame_count,
                    "cfg": 6.0,
                    "seed": 42,
                },
            },
            # 4. 视频解码
            "4": {
                "class_type": "HyVideoDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["1", 1],
                },
            },
            # 5. 保存视频
            "5": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["4", 0],
                    "frame_rate": 24,
                    "filename_prefix": "hunyuan_output",
                },
            },
        }
