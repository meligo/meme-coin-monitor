import asyncio
import logging
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Coroutine, Any, Callable
from functools import partial
import weakref

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        self._tasks: Dict[str, weakref.ref[asyncio.Task]] = {}
        self._running_tasks: Set[str] = set()
        self._task_start_times: Dict[str, datetime] = {}
        self._shutdown_event = asyncio.Event()
        self._shutdown_handlers: List[Callable] = []
        self.is_shutting_down = False
        self._cleanup_in_progress = False

    def register_shutdown_handler(self, handler: Callable) -> None:
        """Register a function to be called during shutdown"""
        if not callable(handler):
            raise ValueError("Shutdown handler must be callable")
        if handler not in self._shutdown_handlers:
            self._shutdown_handlers.append(handler)
            logger.debug(f"Registered shutdown handler: {handler.__name__}")

    def start_task(self, name: str, coro: Coroutine, *args: Any, **kwargs: Any) -> asyncio.Task:
        """
        Start and register a new task with monitoring
        
        Args:
            name: Unique identifier for the task
            coro: Coroutine to run as task
            *args: Arguments to pass to the coroutine
            **kwargs: Keyword arguments to pass to the coroutine

        Returns:
            asyncio.Task: The created and monitored task
        
        Raises:
            RuntimeError: If attempting to start a task during shutdown
        """
        if self.is_shutting_down:
            logger.warning(f"Attempted to start task {name} during shutdown")
            raise RuntimeError("Cannot start new tasks during shutdown")

        # Check if task exists and is still running
        existing_task = self.get_task(name)
        if existing_task and not existing_task.done():
            logger.warning(f"Task {name} is already running")
            return existing_task

        # Create and setup new task
        task = asyncio.create_task(coro(*args, **kwargs), name=name)
        self._tasks[name] = weakref.ref(task)
        self._running_tasks.add(name)
        self._task_start_times[name] = datetime.utcnow()
        
        task.add_done_callback(partial(self._handle_task_completion, name))
        logger.debug(f"Started task: {name}")
        return task

    async def cancel_task(self, name: str, timeout: float = 5.0) -> bool:
        """
        Cancel a specific task by name with timeout
        
        Args:
            name: Name of the task to cancel
            timeout: Maximum time to wait for task cancellation

        Returns:
            bool: True if task was found and cancelled, False otherwise
        """
        task = self.get_task(name)
        if not task:
            return False
            
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.error(f"Task {name} failed to cancel within {timeout} seconds")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error while cancelling task {name}: {e}")

        self._cleanup_task(name)
        return True

    async def cancel_all_tasks(self) -> None:
        """Cancel all running tasks gracefully"""
        if self._cleanup_in_progress:
            return

        try:
            self._cleanup_in_progress = True
            self.is_shutting_down = True
            self._shutdown_event.set()
            
            # Call shutdown handlers first
            for handler in self._shutdown_handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler()
                    else:
                        handler()
                except Exception as e:
                    logger.error(f"Error in shutdown handler {handler.__name__}: {e}")

            # Cancel all running tasks
            tasks_to_cancel = [
                (name, self.get_task(name)) 
                for name in list(self._running_tasks)
                if self.get_task(name) and not self.get_task(name).done()
            ]
            
            if tasks_to_cancel:
                logger.info(f"Cancelling {len(tasks_to_cancel)} running tasks...")
                
                for name, task in tasks_to_cancel:
                    logger.debug(f"Cancelling task: {name}")
                    if task:
                        task.cancel()
                
                await asyncio.gather(
                    *[task for _, task in tasks_to_cancel if task],
                    return_exceptions=True
                )

        except Exception as e:
            logger.error(f"Error during task cancellation: {e}")
        finally:
            self._cleanup_in_progress = False

    def get_task(self, name: str) -> Optional[asyncio.Task]:
        """Get a task by name if it exists and is still valid"""
        if name in self._tasks:
            task_ref = self._tasks[name]
            task = task_ref() if task_ref else None
            if task is None:
                self._cleanup_task(name)
            return task
        return None

    def get_running_tasks(self) -> Dict[str, float]:
        """
        Get currently running tasks and their durations in seconds
        
        Returns:
            Dict[str, float]: Mapping of task names to their running duration
        """
        now = datetime.utcnow()
        running_tasks = {}
        
        for name in list(self._running_tasks):
            task = self.get_task(name)
            if task and not task.done():
                start_time = self._task_start_times.get(name)
                if start_time:
                    running_tasks[name] = (now - start_time).total_seconds()
        
        return running_tasks

    def is_task_running(self, name: str) -> bool:
        """Check if a specific task is currently running"""
        task = self.get_task(name)
        return bool(task and not task.done() and name in self._running_tasks)

    def get_task_metrics(self) -> Dict[str, Any]:
        """
        Get detailed metrics about all tasks
        
        Returns:
            Dict containing task statistics and health information
        """
        now = datetime.utcnow()
        valid_tasks = [(name, self.get_task(name)) for name in list(self._tasks.keys())]
        valid_tasks = [(name, task) for name, task in valid_tasks if task is not None]
        
        running_count = sum(1 for _, task in valid_tasks if not task.done())
        total_count = len(valid_tasks)
        
        # Calculate completion rate
        completed_tasks = sum(
            1 for _, task in valid_tasks
            if task.done() and not task.cancelled()
        )
        completion_rate = (completed_tasks / total_count) if total_count > 0 else 1.0
        
        # Get task durations
        task_durations = {
            name: (now - self._task_start_times[name]).total_seconds()
            for name in self._running_tasks
            if name in self._task_start_times
        }
        
        # Identify long-running tasks (over 1 hour)
        long_running = [
            name for name, duration in task_durations.items()
            if duration > 3600
        ]
        
        return {
            'total_tasks': total_count,
            'running_tasks': running_count,
            'completion_rate': completion_rate,
            'task_durations': task_durations,
            'long_running_tasks': long_running
        }

    def _cleanup_task(self, name: str) -> None:
        """Clean up task references"""
        self._tasks.pop(name, None)
        self._running_tasks.discard(name)
        self._task_start_times.pop(name, None)

    def _handle_task_completion(self, name: str, task: asyncio.Task) -> None:
        """
        Handle task completion, cleanup, and error logging
        
        Args:
            name: Name of the completed task
            task: The completed task object
        """
        try:
            exc = task.exception()
            if exc is None:
                duration = datetime.utcnow() - self._task_start_times.get(
                    name, datetime.utcnow()
                )
                logger.debug(f"Task {name} completed successfully after {duration}")
            elif not isinstance(exc, asyncio.CancelledError):
                logger.error(f"Task {name} failed with exception: {exc}")
                if not self.is_shutting_down:
                    asyncio.create_task(self.cancel_all_tasks())
        except asyncio.CancelledError:
            logger.info(f"Task {name} was cancelled")
        except Exception as e:
            logger.error(f"Error handling task completion for {name}: {e}")
        finally:
            self._cleanup_task(name)

    def setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown"""
        def handle_signal(sig: signal.Signals) -> None:
            logger.info(f"Received signal {sig.name}")
            if not self.is_shutting_down:
                asyncio.create_task(self.cancel_all_tasks())

        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig,
                    partial(handle_signal, sig)
                )
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda s, _: handle_signal(s))