import functools
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

class MeasureBlockPerformance:
    """Context manager for measuring performance of code blocks"""
    
    def __init__(self, operation: str):
        self.operation = operation
        self.start_time = None

    async def __aenter__(self):
        self.start_time = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        if exc_type:
            logger.warning(f"Performance: {self.operation} failed after {duration:.2f}s - Error: {str(exc_val)}")
        else:
            logger.info(f"Performance: {self.operation} completed in {duration:.2f}s")

def measure_performance(operation: str) -> Callable:
    """Decorator for measuring async function performance"""
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Remove db parameter if present to avoid conflicts
            kwargs.pop('db', None)
            
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"Performance: {func.__name__} completed in {duration:.2f}s [{operation}]")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.warning(f"Performance: {func.__name__} failed after {duration:.2f}s [{operation}] - Error: {str(e)}")
                raise
        return wrapper
    return decorator