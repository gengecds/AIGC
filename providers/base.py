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


class VideoProvider(ABC):
    """视频生成"""

    @abstractmethod
    async def generate(self, input_image: str, prompt: str,
                       duration: int = 5, **kwargs) -> str:
        """返回视频路径或URL"""
        ...
