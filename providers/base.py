"""Provider 抽象基类 - 统一 AI API 适配层"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class LLMProvider(ABC):
    """文本生成（剧本/分镜等）"""

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: Optional[str] = None,
                       model: Optional[str] = None, **kwargs) -> str:
        ...

    @abstractmethod
    async def chat(self, messages: list, model: Optional[str] = None, **kwargs) -> str:
        ...


class ImageProvider(ABC):
    """图片生成"""

    @abstractmethod
    async def generate(self, prompt: str, ref_image: Optional[str] = None,
                       seed: Optional[int] = None, **kwargs) -> str:
        """返回图片路径或URL"""
        ...

    async def batch_generate(self, items: list[dict], **kwargs) -> list[dict]:
        """批量生成（默认逐个调用 generate，Provider 可覆盖实现优化）"""
        results = []
        for item in items:
            path = await self.generate(
                prompt=item.get("sd_prompt", ""),
                ref_image=item.get("ref_image"),
                seed=item.get("seed"),
                shot_id=item.get("shot_id", ""),
                **kwargs,
            )
            results.append({"filename": path, "subfolder": "", "type": "png"})
        return results


class VideoProvider(ABC):
    """视频生成"""

    @abstractmethod
    async def generate(self, input_image: str, prompt: str,
                       duration: int = 5, **kwargs) -> str:
        """返回视频路径或URL"""
        ...

    async def batch_generate(self, items: list[dict], **kwargs) -> list[dict]:
        """批量生成（默认逐个调用 generate，Provider 可覆盖实现优化）"""
        results = []
        for item in items:
            path = await self.generate(
                input_image=item.get("input_image", ""),
                prompt=item.get("video_motion", ""),
                duration=item.get("duration", 5),
                shot_id=item.get("shot_id", ""),
                **kwargs,
            )
            results.append({"filename": path, "subfolder": "", "type": "mp4"})
        return results
