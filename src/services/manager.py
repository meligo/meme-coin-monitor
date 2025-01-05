import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Type

from src.config.settings import settings
from src.core.models.tier_models import TierAlert
from src.services.analysis.rug_detector import RugDetector
from src.services.backtesting.scanner import BacktestScanner
from src.services.blockchain.token_metrics import TokenMetrics
from src.services.holder_analysis.distribution_calculator import DistributionCalculator
from src.services.holder_analysis.metrics_updater import HolderMetricsUpdater
from src.services.monitoring.performance import measure_performance
from src.services.monitoring.token_monitor import TokenMonitor
from src.services.pump_fun.scanner import PumpFunScanner
from src.services.tier_management.tier_manager import TierManager
from src.services.tier_management.utils.metrics import TokenMetricsProcessor
from src.services.wallet_analysis.analyzer import WalletPatternAnalyzer
from src.utils.rate_limiter import GlobalRateLimiter
from src.utils.rpc_manager import RPCManager
from src.utils.task_manager import TaskManager

logger = logging.getLogger(__name__)

class ServicePriority(Enum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3

@dataclass
class ServiceDependency:
    service_name: str
    priority: ServicePriority
    required_services: Set[str]

class ServiceManager:
    """
    Manages initialization and lifecycle of all services.
    Handles dependency management, batch operations, and error handling.
    """
    
    def __init__(self):
        """Initialize service manager with all required components"""
        # Core state tracking
        self.is_running = False
        self._cleanup_in_progress = False
        self._health_check_interval = 60  # seconds
        self._service_status = {}
        
        # Core managers
        self.task_manager = TaskManager()
        self.rate_limiter = GlobalRateLimiter()
        self.rpc_manager = RPCManager()
        
        # Service tracking
        self.services = {}
        self._dependencies = {}
        self._batch_operations = {}
        self._error_handlers = {}
        self._service_hooks = {}
        
        # Configuration
        self.config = settings
        
        # Initialize dependencies
        self._init_service_dependencies()
        logger.info("Service manager initialized")

    def _init_service_dependencies(self):
        """Initialize service dependencies and priorities"""
        self._dependencies = {
            'rpc_manager': ServiceDependency(
                'rpc_manager', 
                ServicePriority.CRITICAL,
                set()
            ),
            'token_metrics': ServiceDependency(
                'token_metrics',
                ServicePriority.HIGH,
                {'rpc_manager'}
            ),
            'metrics_processor': ServiceDependency(
                'metrics_processor',
                ServicePriority.HIGH,
                {'rpc_manager', 'token_metrics'}
            ),
            'tier_manager': ServiceDependency(
                'tier_manager',
                ServicePriority.HIGH,
                {'metrics_processor'}
            ),
            'token_monitor': ServiceDependency(
                'token_monitor',
                ServicePriority.HIGH,
                {'rpc_manager', 'token_metrics', 'tier_manager'}
            ),
            'pump_scanner': ServiceDependency(
                'pump_scanner',
                ServicePriority.MEDIUM,
                {'rpc_manager', 'token_metrics'}
            ),
            'backtest_scanner': ServiceDependency(
                'backtest_scanner',
                ServicePriority.LOW,
                {'rpc_manager', 'token_metrics'}
            ),
            'wallet_analyzer': ServiceDependency(
                'wallet_analyzer',
                ServicePriority.MEDIUM,
                {'rpc_manager'}
            ),
            'rug_detector': ServiceDependency(
                'rug_detector',
                ServicePriority.MEDIUM,
                {'token_metrics', 'wallet_analyzer'}
            ),
            'distribution_calculator': ServiceDependency(
                'distribution_calculator',
                ServicePriority.MEDIUM,
                {'token_metrics'}
            ),
            'holder_metrics_updater': ServiceDependency(
                'holder_metrics_updater',
                ServicePriority.MEDIUM,
                {'token_metrics', 'distribution_calculator'}
            )
        }

    @measure_performance('service_initialization')
    async def initialize(self) -> None:
        """Initialize all services with dependency tracking"""
        if self.is_running:
            return
            
        try:
            logger.info("Initializing services...")
            
            # Initialize RPC manager first
            await self.rpc_manager.initialize()
            
            # Initialize core services
            self.services = {
                'token_metrics': TokenMetrics(self.rpc_manager),
                'metrics_processor': TokenMetricsProcessor(self.rpc_manager),
                'tier_manager': TierManager(),
                'token_monitor': TokenMonitor(),
                'pump_scanner': PumpFunScanner(),
                'backtest_scanner': BacktestScanner(),
                'wallet_analyzer': WalletPatternAnalyzer(self.rpc_manager),
                'rug_detector': RugDetector(),
                'distribution_calculator': DistributionCalculator(),
                'holder_metrics_updater': HolderMetricsUpdater(),
                'rpc_manager': self.rpc_manager
            }
            
            # Sort services by dependency order
            service_order = self._get_service_init_order()
            
            # Initialize services in order
            for service_name in service_order:
                try:
                    service = self.services[service_name]
                    if hasattr(service, 'initialize'):
                        await service.initialize()
                    self._service_status[service_name] = True
                    logger.info(f"Initialized {service_name}")
                except Exception as e:
                    self._service_status[service_name] = False
                    logger.error(f"Failed to initialize {service_name}: {e}")
                    raise

            # Share dependencies
            await self._share_dependencies()
            
            # Start services
            await self.start_services()
            
            self.is_running = True
            logger.info("Services initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing services: {e}")
            self.is_running = False
            await self.cleanup()
            raise

    def _get_service_init_order(self) -> List[str]:
        """Get services sorted by dependency order"""
        visited = set()
        order = []
        
        def visit(service_name: str):
            if service_name in visited:
                return
            visited.add(service_name)
            
            # Visit dependencies first
            dep_info = self._dependencies.get(service_name)
            if dep_info:
                for dep in dep_info.required_services:
                    visit(dep)
            
            order.append(service_name)
            
        for service_name in sorted(
            self._dependencies.keys(),
            key=lambda x: self._dependencies[x].priority.value
        ):
            visit(service_name)
            
        return order

    async def start_services(self):
        """Start all services in proper order"""
        logger.info("Starting services...")
        
        # Start services in priority order
        for priority in ServicePriority:
            priority_services = [
                name for name, dep in self._dependencies.items()
                if dep.priority == priority and name in self.services
            ]
            
            for service_name in priority_services:
                await self.start_service(service_name)
                
        # Start monitoring without passing interval
        self.task_manager.start_task(
            "health_monitor",
            self._run_health_checks
        )
        
        # Start RPC monitoring with no interval param
        self.task_manager.start_task(
            "rpc_monitor",
            self._monitor_rpc_health,10
        )
        
        logger.info("All services started successfully")

    @measure_performance('service_start')
    async def start_service(self, service_name: str):
        """Start a single service with dependency checks"""
        try:
            if service_name not in self.services:
                raise ValueError(f"Unknown service: {service_name}")
                
            # Check dependencies
            dep_info = self._dependencies.get(service_name)
            if dep_info:
                for dep in dep_info.required_services:
                    if not self._check_service_running(self.services.get(dep)):
                        raise RuntimeError(f"Required dependency {dep} not running for {service_name}")

            service = self.services[service_name]
            if hasattr(service, 'start'):
                await service.start()
            elif hasattr(service, 'start_monitoring'):
                await service.start_monitoring()

            # Notify hooks
            await self._notify_service_hooks(service_name, 'started')
            
        except Exception as e:
            await self._handle_error(e)
            raise

    async def _run_health_checks(self):
        """Run periodic health checks on all services with fixed interval"""
        while self.is_running:
            try:
                unhealthy_services = []
                for service_name, service in self.services.items():
                    service_status = self._check_service_running(service)
                    if not service_status:
                        unhealthy_services.append(service_name)
                        logger.error(f"Service {service_name} is not running")
                
                # Check RPC health
                rpc_status = self.rpc_manager.get_status()
                if rpc_status['healthy_endpoints'] == 0:
                    logger.error("No healthy RPC endpoints available")
                    
                # Check task health
                task_metrics = self.task_manager.get_task_metrics()
                if task_metrics['completion_rate'] < 0.9:  # Below 90%
                    logger.warning(f"Task completion rate below threshold: {task_metrics['completion_rate']:.2f}")
                
                if unhealthy_services:
                    await self._handle_unhealthy_services(unhealthy_services)

                await asyncio.sleep(self._health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health check: {e}")
                await asyncio.sleep(self._health_check_interval)

    async def _handle_unhealthy_services(self, service_names: List[str]):
        """Handle unhealthy services"""
        for service_name in service_names:
            try:
                service = self.services[service_name]
                dep_info = self._dependencies.get(service_name)
                
                if dep_info and dep_info.priority in {ServicePriority.CRITICAL, ServicePriority.HIGH}:
                    logger.error(f"Critical service {service_name} is unhealthy. Attempting restart...")
                    await self.start_service(service_name)
                    
            except Exception as e:
                logger.error(f"Error handling unhealthy service {service_name}: {e}")

    async def _monitor_rpc_health(self, interval: int) -> None:
        """Monitor RPC health and handle failover"""
        while self.is_running:
            try:
                status = self.rpc_manager.get_status()
                healthy_count = status['healthy_endpoints']
                total_count = len(status['endpoints'])
                
                if healthy_count < total_count:
                    logger.warning(
                        f"Only {healthy_count}/{total_count} RPC endpoints are healthy. "
                        f"Active endpoint: {status['active_endpoint']}"
                    )
                    
                for endpoint in status['endpoints']:
                    if not endpoint['healthy']:
                        logger.warning(
                            f"Unhealthy RPC endpoint {endpoint['url']}: "
                            f"Error count: {endpoint['error_count']}"
                        )
                        
            except Exception as e:
                logger.error(f"Error monitoring RPC health: {e}")
                
            await asyncio.sleep(interval)

    async def _share_dependencies(self) -> None:
        """Share common dependencies between services"""
        try:
            # Share dependencies for PumpFunScanner
            pump_scanner = self.services.get('pump_scanner')
            if pump_scanner:
                pump_scanner.rpc_manager = self.rpc_manager
                pump_scanner.token_metrics = self.services['token_metrics']
                pump_scanner.wallet_analyzer = self.services['wallet_analyzer']
                pump_scanner.tier_manager = self.services['tier_manager']
                pump_scanner.rate_limiter = self.rate_limiter
                
            # Share dependencies for BacktestScanner
            backtest_scanner = self.services.get('backtest_scanner')
            if backtest_scanner:
                backtest_scanner.rpc_manager = self.rpc_manager
                backtest_scanner.token_metrics = self.services['token_metrics']
                backtest_scanner.rate_limiter = self.rate_limiter
                
            # Share dependencies for TokenMonitor
            token_monitor = self.services.get('token_monitor')
            if token_monitor:
                token_monitor.rpc_manager = self.rpc_manager
                token_monitor.token_metrics = self.services['token_metrics']
                token_monitor.metrics_processor = self.services['metrics_processor']
                token_monitor.wallet_analyzer = self.services['wallet_analyzer']
                token_monitor.tier_manager = self.services['tier_manager']
                token_monitor.rate_limiter = self.rate_limiter

            # Share rate limiter with WalletAnalyzer
            wallet_analyzer = self.services.get('wallet_analyzer')
            if wallet_analyzer:
                wallet_analyzer.rate_limiter = self.rate_limiter

            logger.info("Dependencies shared between services")

        except Exception as e:
            logger.error(f"Error sharing dependencies: {e}")
            raise

    def _check_service_running(self, service) -> bool:
        """Check if a service is running"""
        try:
            if hasattr(service, 'is_running'):
                return service.is_running
            if hasattr(service, 'processing'):
                return service.processing
            return True
        except Exception:
            return False

    @asynccontextmanager
    async def batch_operation(self, name: str):
        """Context manager for batch operations"""
        if name not in self._batch_operations:
            self._batch_operations[name] = asyncio.Queue()
            
        try:
            yield self._batch_operations[name]
        finally:
            # Process any remaining items
            while not self._batch_operations[name].empty():
                try:
                    item = await self._batch_operations[name].get()
                    await self._process_batch_item(name, item)
                except Exception as e:
                    await self._handle_error(e)

    async def _process_batch_item(self, operation: str, item: Any):
        """Process a single batch operation item"""
        try:
            if operation == 'token_updates':
                await self._process_token_update(item)
            elif operation == 'metrics_updates':
                await self._process_metrics_update(item)
            elif operation == 'tier_updates':
                await self._process_tier_update(item)
        except Exception as e:
            await self._handle_error(e)

    async def _process_token_update(self, update: Dict):
        """Process token update in batch"""
        token_monitor = self.services.get('token_monitor')
        if token_monitor and hasattr(token_monitor, 'update_token'):
            await token_monitor.update_token(update)

    async def _process_metrics_update(self, update: Dict):
        """Process metrics update in batch"""
        metrics_processor = self.services.get('metrics_processor')
        if metrics_processor and hasattr(metrics_processor, 'process_metrics'):
            await metrics_processor.process_metrics(update)

    async def _process_tier_update(self, alert: TierAlert):
        """Process tier update in batch"""
        tier_manager = self.services.get('tier_manager')
        if tier_manager and hasattr(tier_manager, 'process_tier_alert'):
            await tier_manager.process_tier_alert(alert)

    async def _handle_error(self, error: Exception):
        """Centralized error handling"""
        error_type = type(error)
        if error_type in self._error_handlers:
            await self._error_handlers[error_type](error)
        else:
            logger.error(f"Unhandled error: {error}")
            # Determine if error is critical
            if error_type in {RuntimeError, SystemError}:
                await self.cleanup()
                raise error

    async def _notify_service_hooks(self, service_name: str, event: str):
        """Notify all hooks for a service event"""
        if service_name in self._service_hooks:
            for hook in self._service_hooks[service_name]:
                try:
                    await hook(event)
                except Exception as e:
                    logger.error(f"Error in service hook: {e}")

    def register_error_handler(self, error_type: Type[Exception], handler: callable):
        """Register an error handler for a specific exception type"""
        self._error_handlers[error_type] = handler

    def register_service_hook(self, service_name: str, hook: callable):
        """Register a hook for service events"""
        if service_name not in self._service_hooks:
            self._service_hooks[service_name] = []
        self._service_hooks[service_name].append(hook)

    async def process_tier_alert(self, alert: TierAlert):
        """Process tier change alerts"""
        try:
            async with self.batch_operation('tier_updates') as queue:
                await queue.put(alert)
                
            # Notify relevant services
            services_to_notify = ['token_monitor', 'metrics_processor']
            for service_name in services_to_notify:
                if service := self.services.get(service_name):
                    if hasattr(service, 'handle_tier_alert'):
                        await service.handle_tier_alert(alert)
                        
        except Exception as e:
            await self._handle_error(e)

    def configure_service(self, service_name: str, **config):
        """Configure a specific service"""
        if service := self.services.get(service_name):
            if hasattr(service, 'configure'):
                service.configure(**config)
            else:
                logger.warning(f"Service {service_name} does not support configuration")

    async def get_service_metrics(self) -> Dict[str, Any]:
        """Get performance metrics from all services"""
        metrics = {
            'rate_limiter': self.rate_limiter.get_metrics(),
            'task_manager': self.task_manager.get_task_metrics(),
            'rpc_manager': self.rpc_manager.get_status()
        }

        # Add service-specific metrics
        for service_name, service in self.services.items():
            if hasattr(service, 'get_metrics'):
                metrics[service_name] = await service.get_metrics()
            elif hasattr(service, 'get_status'):
                metrics[service_name] = service.get_status()

        return metrics

    async def verify_services(self) -> Dict[str, bool]:
        """Verify that all services are properly initialized and running"""
        verification = {}
        
        # Verify service instances
        for service_name, service in self.services.items():
            verification[service_name] = self._check_service_running(service)

        # Verify task health
        task_metrics = self.task_manager.get_task_metrics()
        verification.update({
            'tasks_healthy': task_metrics['completion_rate'] >= 0.9,
            'critical_tasks_running': all(
                self.task_manager.is_task_running(task)
                for task in ['token_monitor', 'pump_scanner', 'health_monitor']
            )
        })
        
        # Verify RPC health
        rpc_status = self.rpc_manager.get_status()
        verification['rpc_healthy'] = rpc_status['healthy_endpoints'] > 0
        
        # Check memory usage
        if hasattr(self.config, 'max_memory_usage'):
            import psutil
            process = psutil.Process()
            memory_use = process.memory_info().rss / (1024 * 1024)  # MB
            verification['memory_healthy'] = memory_use < self.config.max_memory_usage
        
        return verification

    async def cleanup(self):
        """Cleanup all services and resources"""
        if self._cleanup_in_progress:
            return
            
        try:
            self._cleanup_in_progress = True
            self.is_running = False
            logger.info("Starting service cleanup...")
            
            # Cancel all tasks first
            await self.task_manager.cancel_all_tasks()
            
            # Stop all services in reverse dependency order
            service_order = list(reversed(self._get_service_init_order()))
            
            for service_name in service_order:
                try:
                    service = self.services.get(service_name)
                    if not service:
                        continue
                        
                    if hasattr(service, 'stop'):
                        await service.stop()
                    elif hasattr(service, 'cleanup'):
                        await service.cleanup()
                except Exception as e:
                    logger.error(f"Error stopping {service_name}: {e}")

            # Clear all queues
            for queue in self._batch_operations.values():
                while not queue.empty():
                    try:
                        _ = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

            # Clear all handlers and hooks
            self._service_hooks.clear()
            self._error_handlers.clear()
            
            # Clear service tracking
            self.services.clear()
            self._service_status.clear()
            
            logger.info("Service cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise
        finally:
            self._cleanup_in_progress = False

    async def __aenter__(self):
        """Async context manager support"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager cleanup"""
        await self.cleanup()