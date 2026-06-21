"""重试工具 — Agent 自动重试机制"""

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Coroutine, Optional, TypeVar

from config.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_async(
    func: Callable[..., Coroutine[Any, Any, T]],
    *args,
    max_retries: Optional[int] = None,
    retry_delay: Optional[float] = None,
    retryable_exceptions: tuple = (Exception,),
    **kwargs,
) -> T:
    """异步重试包装器

    用法:
        result = await retry_async(my_agent.run, input_data, max_retries=3)
    """
    max_retries = max_retries or settings.pipeline.max_retries
    retry_delay = retry_delay or settings.pipeline.retry_delay

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    f"[Retry] {func.__name__} 第{attempt}/{max_retries}次失败: {e} "
                    f"({retry_delay}s后重试)"
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    f"[Retry] {func.__name__} 全部{max_retries}次重试失败: {e}"
                )

    raise last_error  # type: ignore
