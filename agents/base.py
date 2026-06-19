"""Agent 基类 - 所有 Agent 的抽象接口"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


class AgentResult:
    """Agent 执行结果"""
    def __init__(self, success: bool, data: Any = None, error: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class BaseAgent(ABC):
    """所有 Agent 的基类"""

    def __init__(self, name: str, model_provider: Optional[str] = None):
        self.name = name
        self.model_provider = model_provider

    @abstractmethod
    def input_schema(self) -> Dict:
        """输入格式描述"""
        ...

    @abstractmethod
    def output_schema(self) -> Dict:
        """输出格式描述"""
        ...

    @abstractmethod
    async def run(self, input_data: Dict) -> AgentResult:
        """执行 Agent 逻辑"""
        ...

    async def run_with_retry(self, input_data: Dict, max_retries: int = 3) -> AgentResult:
        """带重试的执行"""
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[{self.name}] 第 {attempt} 次执行...")
                result = await self.run(input_data)
                if result.success:
                    return result
                last_error = result.error
            except Exception as e:
                last_error = str(e)
                logger.warning(f"[{self.name}] 第 {attempt} 次失败: {e}")
            if attempt < max_retries:
                import asyncio
                wait = attempt * 2  # 退避: 2s, 4s, 6s
                logger.info(f"[{self.name}] 等待 {wait}s 后重试...")
                await asyncio.sleep(wait)
        return AgentResult(success=False, error=f"重试 {max_retries} 次均失败: {last_error}")
