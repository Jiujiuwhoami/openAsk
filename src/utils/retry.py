"""API 调用重试机制（指数退避）。"""

import time
import functools
from typing import Callable, Type, Tuple, TypeVar

T = TypeVar("T")

MAX_RETRY_ATTEMPTS = 3
INITIAL_RETRY_DELAY = 1  # 秒


def retry_with_backoff(
    max_retries: int = MAX_RETRY_ATTEMPTS,
    initial_delay: float = INITIAL_RETRY_DELAY,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """指数退避重试装饰器。

    重试间隔：initial_delay * 2^(attempt - 1)
    例如 initial_delay=1: 1s → 2s → 4s
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error: Optional[Exception] = None
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

        return wrapper

    return decorator