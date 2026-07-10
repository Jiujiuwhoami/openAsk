"""API 调用重试机制（指数退避）。

支持同步和异步函数：
- 同步函数：使用 time.sleep()
- 异步函数：使用 asyncio.sleep()，不阻塞事件循环
"""

import asyncio
import functools
import time
from typing import Callable, Type, Tuple, TypeVar, Any

T = TypeVar("T")

MAX_RETRY_ATTEMPTS = 3
INITIAL_RETRY_DELAY = 1  # 秒


def retry_with_backoff(
    max_retries: int = MAX_RETRY_ATTEMPTS,
    initial_delay: float = INITIAL_RETRY_DELAY,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """指数退避重试装饰器。

    重试间隔：initial_delay * 2^(attempt - 1)
    例如 initial_delay=1: 1s → 2s → 4s

    自动检测被装饰函数是同步还是异步：
    - 异步函数：使用 asyncio.sleep()
    - 同步函数：使用 time.sleep()
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt >= max_retries:
                        raise
                    delay = initial_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
            raise last_error  # type: ignore[misc]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt >= max_retries:
                        raise
                    delay = initial_delay * (2 ** attempt)
                    time.sleep(delay)
            raise last_error  # type: ignore[misc]

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
