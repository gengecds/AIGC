"""LLM Provider - DeepSeek API / Ollama 本地"""

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


# 自动加载 .env 文件（如果存在）
def _load_dotenv():
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
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
    return None


class DeepSeekProvider:
    """DeepSeek API 封装"""

    def __init__(self):
        self.api_key = _load_dotenv() or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = "https://api.deepseek.com"
        if not self.api_key:
            logger.warning("DEEPSEEK_API_KEY 未设置，请写入 .env 文件")

    async def generate(self, system: str, user: str,
                       model: str = "deepseek-chat") -> str:
        """调用 DeepSeek API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
        }

        print(f"[DeepSeek] 调用中... model={model}, prompt_len={len(user)}")
        async with httpx.AsyncClient(timeout=120) as client:
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


class OllamaProvider:
    """Ollama 本地模型备用"""

    def __init__(self, host: str = "127.0.0.1", port: int = 11434,
                 model: str = "qwen3:14b-q8_0"):
        self.base_url = f"http://{host}:{port}"
        self.model = model

    async def generate(self, system: str, user: str,
                       model: str | None = None) -> str:
        use_model = model or self.model
        payload = {
            "model": use_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]
