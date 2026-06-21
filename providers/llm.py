"""LLM Provider - DeepSeek API / Ollama 本地（继承 LLMProvider 抽象基类）"""

import logging
import os
from pathlib import Path
from typing import Optional

import httpx

from providers.base import LLMProvider

logger = logging.getLogger(__name__)


def _load_env_key() -> str:
    """从 .env 文件或环境变量加载 DEEPSEEK_API_KEY"""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key == "DEEPSEEK_API_KEY":
                    os.environ[key] = val
                    return val
    return os.environ.get("DEEPSEEK_API_KEY", "")


class DeepSeekProvider(LLMProvider):
    """DeepSeek API 封装（主 LLM）"""

    def __init__(self):
        self.api_key = _load_env_key()
        self.base_url = "https://api.deepseek.com"
        if not self.api_key:
            logger.warning("DEEPSEEK_API_KEY 未设置，请写入 .env 文件")

    async def generate(self, prompt: str,
                       system_prompt: Optional[str] = None,
                       model: Optional[str] = None,
                       **kwargs) -> str:
        """使用 generate 接口（单 prompt 模式）"""
        return await self.chat(
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            model=model or "deepseek-chat",
            **kwargs,
        )

    async def chat(self, messages: list,
                   model: Optional[str] = None,
                   **kwargs) -> str:
        """chat 接口"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or "deepseek-chat",
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
        }

        total_len = sum(len(m.get("content", "")) for m in messages)
        print(f"[DeepSeek] 调用中... model={payload['model']}, " +
              f"messages={len(messages)}, total_chars={total_len}")
        async with httpx.AsyncClient(timeout=kwargs.get("timeout", 120)) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"[DeepSeek] 响应完成: {len(content)} chars")
            logger.info(f"[DeepSeek] 生成长度: {len(content)} chars")
            return content


class OllamaProvider(LLMProvider):
    """Ollama 本地模型（备用）"""

    def __init__(self, host: str = "127.0.0.1", port: int = 11434,
                 model: str = "qwen3:14b-q8_0"):
        self.base_url = f"http://{host}:{port}"
        self.default_model = model

    async def generate(self, prompt: str,
                       system_prompt: Optional[str] = None,
                       model: Optional[str] = None,
                       **kwargs) -> str:
        return await self.chat(
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            model=model or self.default_model,
            **kwargs,
        )

    async def chat(self, messages: list,
                   model: Optional[str] = None,
                   **kwargs) -> str:
        use_model = model or self.default_model
        payload = {
            "model": use_model,
            "messages": messages,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=kwargs.get("timeout", 300)) as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]
