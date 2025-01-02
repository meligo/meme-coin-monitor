import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar('T')  # Type variable for return types

class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded and retry attempts fail"""
    pass

@dataclass
class RPCMetrics:
    """Track RPC call metrics"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rate_limited_calls: int = 0
    total_retry_delay: float = 0.0
    retries: int = 0
    last_error: Optional[str] = None
    last_reset: datetime = field(default_factory=datetime.utcnow)
    
    def record_success(self):
        self.total_calls += 1
        self.successful_calls += 1
        
    def record_failure(self, error: str, retry_delay: float = 0.0):
        self.total_calls += 1
        self.failed_calls += 1
        self.total_retry_delay += retry_delay
        self.last_error = error
        
    def record_retry(self):
        self.retries += 1
        
    def record_rate_limit(self):
        self.rate_limited_calls += 1
        
    def reset(self):
        self.total_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.rate_limited_calls = 0
        self.total_retry_delay = 0.0
        self.retries = 0
        self.last_error = None
        self.last_reset = datetime.utcnow()
        
    @property
    def success_rate(self) -> float:
        return (self.successful_calls / self.total_calls * 100) if self.total_calls > 0 else 0.0

class GlobalRateLimiter:
    """
    Enhanced global rate limiter with metrics, retries, batching, and precise timing control.
    Maintains a strict RPS limit across all components.
    """
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalRateLimiter, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        max_rps: int = 250,
        max_concurrent: int = 250,
        min_delay: float = 0.001,
        max_retries: int = 10,
        base_delay: float = 0.1,
        metric_reset_interval: int = 3600
    ):
        if self._initialized:
            return
            
        self._initialized = True
        
        # Rate limiting parameters
        self.max_rps = max_rps
        self.current_second = int(time.time())
        self.requests_this_second = 0
        self.min_delay = min_delay
        
        # Concurrency control
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.call_timestamps: List[float] = []
        
        # Retry configuration
        self.retry_config = {
            'max_retries': max_retries,
            'base_delay': base_delay,
            'max_delay': 10.0
        }
        
        # Metrics tracking
        self.metrics = RPCMetrics()
        self.metric_reset_interval = metric_reset_interval
        self.method_metrics: Dict[str, RPCMetrics] = {}
        
        logger.info(
            f"Rate limiter initialized with: max_rps={max_rps}, "
            f"max_concurrent={max_concurrent}, max_retries={max_retries}"
        )

    async def _wait_for_next_second(self):
        """Wait until the start of the next second with precise timing"""
        current = time.time()
        next_second = int(current) + 1
        wait_time = next_second - current
        if wait_time > 0:
            await asyncio.sleep(wait_time)

    def _reset_counter_if_needed(self):
        """Reset counter if we've moved to a new second"""
        current_second = int(time.time())
        if current_second > self.current_second:
            self.requests_this_second = 0
            self.current_second = current_second

    async def acquire(self) -> bool:
        """Try to acquire permission for an RPC call"""
        async with self._lock:
            self._reset_counter_if_needed()
            
            if self.requests_this_second < self.max_rps:
                self.requests_this_second += 1
                return True
                
            self.metrics.record_rate_limit()
            return False

    def rate_limited(self) -> Callable:
        """
        Decorator for rate-limiting async functions
        
        Usage:
            @rate_limiter.rate_limited()
            async def my_rpc_call():
                pass
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> T:
                return await self.call(func, *args, **kwargs)
            return wrapper
        return decorator

    async def call(
        self,
        func: Callable[..., T],
        *args,
        method_name: Optional[str] = None,
        retry_count: Optional[int] = None,
        **kwargs
    ) -> T:
        """
        Execute a rate-limited call with automatic retries and metrics tracking
        
        Args:
            func: The async function to call
            *args: Positional arguments
            method_name: Optional name for tracking method-specific metrics
            retry_count: Override default retry count
            **kwargs: Keyword arguments
            
        Returns:
            Result from the function call
            
        Raises:
            RateLimitExceeded: When rate limit is exceeded and retries fail
            Exception: Other exceptions from the called function
        """
        method_name = method_name or func.__name__
        
        # Check and reset metrics if needed
        self._check_metric_reset()
        
        # Initialize method metrics if not exists
        if method_name not in self.method_metrics:
            self.method_metrics[method_name] = RPCMetrics()
            
        max_retries = retry_count if retry_count is not None else self.retry_config['max_retries']
        attempt = 0
        
        async with self.semaphore:
            while True:
                try:
                    if await self.acquire():
                        try:
                            # Apply rate limiting
                            await self._apply_rate_limit()
                            
                            # Execute the call
                            start_time = time.perf_counter()
                            result = await func(*args, **kwargs)
                            duration = time.perf_counter() - start_time
                            
                            # Record success
                            self.metrics.record_success()
                            self.method_metrics[method_name].record_success()
                            
                            if attempt > 0:
                                logger.info(
                                    f"Successful retry for {method_name} "
                                    f"(attempt {attempt + 1}/{max_retries})"
                                )
                            
                            # Log performance for slow calls
                            if duration > 1.0:
                                logger.warning(
                                    f"Slow RPC call: {method_name} took {duration:.2f}s"
                                )
                                
                            return result
                            
                        except Exception as e:
                            if attempt >= max_retries:
                                logger.error(f"Max retries ({max_retries}) exceeded: {str(e)}")
                                raise
                            
                            retry_delay = self._calculate_retry_delay(attempt)
                            error_msg = str(e)
                            
                            # Record failure and retry
                            self.metrics.record_failure(error_msg, retry_delay)
                            self.method_metrics[method_name].record_failure(error_msg, retry_delay)
                            self.metrics.record_retry()
                            self.method_metrics[method_name].record_retry()
                            
                            attempt += 1
                            logger.warning(
                                f"Attempt {attempt}/{max_retries} failed for "
                                f"{method_name}, retrying in {retry_delay:.1f}s: {error_msg}"
                            )
                            await asyncio.sleep(retry_delay)
                    else:
                        await self._wait_for_next_second()
                        
                except asyncio.CancelledError:
                    logger.warning("Rate limited call cancelled")
                    raise

    async def execute_batch(
    self,
    requests: List[Dict[str, Any]],
    batch_size: int = 50,  # Increased from 10 to 50
    raise_exceptions: bool = False
) -> List[Any]:
        """
        Execute a batch of RPC requests with rate limiting and concurrent execution
        """
        results = []
        batches = [requests[i:i + batch_size] for i in range(0, len(requests), batch_size)]
        
        for batch_idx, batch in enumerate(batches):
            batch_tasks = []
            for req in batch:
                func = req['func']
                args = req.get('args', [])
                kwargs = req.get('kwargs', {})
                method_name = req.get('method_name', func.__name__)
                
                task = asyncio.create_task(
                    self.call(func, *args, method_name=method_name, **kwargs)
                )
                batch_tasks.append(task)
            
            # Execute batch
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=not raise_exceptions)
            results.extend(batch_results)
            
            # Very small delay between batches if not the last batch
            if batch_idx < len(batches) - 1:
                await asyncio.sleep(0.001)  # Reduced from higher values
            
            success_count = sum(1 for r in batch_results if not isinstance(r, Exception))
            logger.info(
                f"Completed batch {batch_idx + 1}/{len(batches)} "
                f"({success_count}/{len(batch)} successful)"
            )
        
        return results

    async def execute_with_budget(
        self,
        calls: List[Dict[str, Any]],
        time_budget: float,
        batch_size: int = 10
    ) -> List[Any]:
        """
        Execute as many calls as possible within a time budget
        
        Args:
            calls: List of call specifications
            time_budget: Maximum time in seconds
            batch_size: Size of concurrent batches
            
        Returns:
            List of results for completed calls
        """
        results = []
        start_time = time.time()
        
        for i in range(0, len(calls), batch_size):
            if time.time() - start_time >= time_budget:
                logger.warning(f"Time budget ({time_budget}s) exceeded after {len(results)} calls")
                break
                
            batch = calls[i:i + batch_size]
            batch_results = await self.execute_batch(batch, batch_size)
            results.extend(batch_results)
            
        return results

    async def _apply_rate_limit(self):
        """Apply rate limiting based on calls per second"""
        current_time = time.time()
        
        # Remove timestamps older than 1 second
        self.call_timestamps = [
            ts for ts in self.call_timestamps 
            if current_time - ts <= 1.0
        ]
        
        # If at rate limit, wait minimal time
        if len(self.call_timestamps) >= self.max_rps:
            await asyncio.sleep(0.001)  # Minimal delay
        
        self.call_timestamps.append(current_time)

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter"""
        delay = self.retry_config['base_delay'] * (2 ** attempt)
        # Add jitter of ±25%
        jitter = delay * 0.25 * (2 * time.time() % 1 - 0.5)
        return max(self.min_delay, min(delay + jitter, self.retry_config['max_delay']))

    def _check_metric_reset(self):
        """Reset metrics if reset interval has elapsed"""
        if (datetime.utcnow() - self.metrics.last_reset).total_seconds() >= self.metric_reset_interval:
            self.metrics.reset()
            for metrics in self.method_metrics.values():
                metrics.reset()

    def get_metrics(self) -> Dict[str, Any]:
        """Get comprehensive rate limiter metrics"""
        return {
            "global": {
                "total_calls": self.metrics.total_calls,
                "success_rate": self.metrics.success_rate,
                "failed_calls": self.metrics.failed_calls,
                "rate_limited_calls": self.metrics.rate_limited_calls,
                "retries": self.metrics.retries,
                "total_retry_delay": self.metrics.total_retry_delay,
                "last_error": self.metrics.last_error,
                "last_reset": self.metrics.last_reset.isoformat(),
                "current_rps": self.get_current_rps(),
                "remaining_capacity": self.get_remaining_capacity()
            },
            "methods": {
                name: {
                    "total_calls": metrics.total_calls,
                    "success_rate": metrics.success_rate,
                    "failed_calls": metrics.failed_calls,
                    "rate_limited_calls": metrics.rate_limited_calls,
                    "retries": metrics.retries,
                    "total_retry_delay": metrics.total_retry_delay,
                    "last_error": metrics.last_error
                }
                for name, metrics in self.method_metrics.items()
            }
        }

    def get_current_rps(self) -> int:
        """Get current requests this second"""
        self._reset_counter_if_needed()
        return self.requests_this_second

    def get_remaining_capacity(self) -> int:
        """Get remaining requests available this second"""
        self._reset_counter_if_needed()
        return max(0, self.max_rps - self.requests_this_second)
    
    async def wait_for_capacity(self, required_capacity: int, timeout: float = 5.0):
        """
        Wait until specified capacity is available
        
        Args:
            required_capacity: Number of requests needed
            timeout: Maximum time to wait in seconds
            
        Raises:
            TimeoutError: If capacity isn't available within timeout
            ValueError: If required capacity exceeds max RPS
        """
        if required_capacity > self.max_rps:
            raise ValueError(f"Required capacity {required_capacity} exceeds max RPS {self.max_rps}")
            
        start_time = time.time()
        while self.get_remaining_capacity() < required_capacity:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Timeout waiting for {required_capacity} capacity "
                    f"(current: {self.get_current_rps()}, "
                    f"remaining: {self.get_remaining_capacity()})"
                )
            await self._wait_for_next_second()

    async def ensure_capacity(self, required_capacity: int, timeout: float = 5.0) -> bool:
        """
        Check and wait for capacity if necessary
        
        Args:
            required_capacity: Number of requests needed
            timeout: Maximum time to wait
            
        Returns:
            bool: True if capacity is available, False if timeout occurred
        """
        try:
            await self.wait_for_capacity(required_capacity, timeout)
            return True
        except TimeoutError:
            return False
        except ValueError:
            return False

    async def __aenter__(self):
        """Async context manager entry"""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        pass  # Rate limit is automatically managed by time

    def reset_metrics(self):
        """Manually reset all metrics"""
        self.metrics.reset()
        for metrics in self.method_metrics.values():
            metrics.reset()
        logger.info("Rate limiter metrics manually reset")

    def update_limits(self, new_max_rps: int):
        """
        Update rate limiting parameters
        
        Args:
            new_max_rps: New maximum requests per second
        """
        if new_max_rps <= 0:
            raise ValueError("Max RPS must be positive")
            
        self.max_rps = new_max_rps
        logger.info(f"Rate limiter updated: max_rps={new_max_rps}")

    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status of the rate limiter
        
        Returns:
            Dict containing current status and configuration
        """
        return {
            "config": {
                "max_rps": self.max_rps,
                "retry_config": self.retry_config,
                "metric_reset_interval": self.metric_reset_interval
            },
            "current_state": {
                "current_rps": self.get_current_rps(),
                "remaining_capacity": self.get_remaining_capacity(),
                "current_second": self.current_second
            },
            "metrics": self.get_metrics()
        }

    def estimate_completion_time(self, num_requests: int) -> float:
        """
        Estimate time needed to complete a number of requests
        
        Args:
            num_requests: Number of requests to estimate for
            
        Returns:
            Estimated completion time in seconds
        """
        current_capacity = self.get_remaining_capacity()
        if current_capacity >= num_requests:
            return self.min_delay * num_requests
            
        # Calculate full seconds needed
        remaining_requests = num_requests - current_capacity
        full_seconds = (remaining_requests + self.max_rps - 1) // self.max_rps
        
        # Add current second remainder
        return full_seconds + (1 - (time.time() % 1))