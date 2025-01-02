
import asyncio
import functools
import logging
from typing import Any, Callable

from src.config.settings import RPC_CONFIG

logger = logging.getLogger(__name__)

def with_retry(max_retries: int = None, delay: float = None) -> Callable:
    """Decorator for RPC calls with retry logic"""
    max_retries = max_retries or RPC_CONFIG['retry_count']
    delay = delay or RPC_CONFIG['retry_delay']

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (attempt + 1)  # Exponential backoff
                        logger.warning(
                            f"Attempt {attempt + 1} failed: {str(e)}. "
                            f"Retrying in {wait_time}s..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"All {max_retries} attempts failed for {func.__name__}. "
                            f"Last error: {str(last_exception)}"
                        )
                        raise last_exception
            
            return None  # Type checker happiness
            
        return wrapper
    return decorator