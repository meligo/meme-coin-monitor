import logging
from typing import Any, Dict, Optional

from solana.rpc.async_api import AsyncClient

from src.services.analysis.rug_detector import RugDetector
from src.services.backtesting.scanner import BacktestScanner
from src.services.blockchain.token_metrics import TokenMetrics
from src.services.holder_analysis.distribution_calculator import DistributionCalculator
from src.services.holder_analysis.metrics_updater import HolderMetricsUpdater
from src.services.monitoring.token_monitor import TokenMonitor
from src.services.pump_fun.scanner import PumpFunScanner
from src.services.tier_management.tier_manager import TierManager
from src.services.tier_management.utils.metrics import TokenMetricsProcessor
from src.services.wallet_analysis.analyzer import WalletPatternAnalyzer
from src.utils.rate_limiter import GlobalRateLimiter

logger = logging.getLogger(__name__)

class ServiceManager:
    """
    Manages initialization and lifecycle of all services.
    Ensures proper sharing of dependencies and rate limiting.
    """
    
    def __init__(self):
        self.rpc_client: Optional[AsyncClient] = None
        self.rate_limiter = GlobalRateLimiter()
        
        # Core services
        self.token_metrics: Optional[TokenMetrics] = None
        self.metrics_processor: Optional[TokenMetricsProcessor] = None
        self.tier_manager: Optional[TierManager] = None
        
        # Monitoring services
        self.token_monitor: Optional[TokenMonitor] = None
        self.pump_scanner: Optional[PumpFunScanner] = None
        self.backtest_scanner: Optional[BacktestScanner] = None
        
        # Analysis services
        self.wallet_analyzer: Optional[WalletPatternAnalyzer] = None
        self.rug_detector: Optional[RugDetector] = None
        self.distribution_calculator: Optional[DistributionCalculator] = None
        self.holder_metrics_updater: Optional[HolderMetricsUpdater] = None
        
        logger.info("ServiceManager initialized")

    async def initialize_with_rpc(self, rpc_client: AsyncClient) -> None:
        """
        Initialize all services with shared RPC client
        
        Args:
            rpc_client: Solana RPC client instance
        """
        try:
            logger.info("Initializing services with RPC client...")
            self.rpc_client = rpc_client
            
            # Initialize core services first
            self.token_metrics = TokenMetrics(self.rpc_client)
            self.metrics_processor = TokenMetricsProcessor(self.rpc_client)
            self.tier_manager = TierManager()
            
            # Initialize monitoring services
            self.token_monitor = TokenMonitor()
            self.pump_scanner = PumpFunScanner()
            self.backtest_scanner = BacktestScanner()
            
            # Initialize analysis services
            self.wallet_analyzer = WalletPatternAnalyzer(self.rpc_client)
            self.rug_detector = RugDetector()
            self.distribution_calculator = DistributionCalculator()
            self.holder_metrics_updater = HolderMetricsUpdater()
            
            # Share common dependencies
            await self._share_dependencies()
            
            logger.info("Services initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing services: {e}")
            await self.cleanup()
            raise

    async def _share_dependencies(self) -> None:
        """Share common dependencies between services"""
        try:
            # Share TokenMetrics
            self.pump_scanner.token_metrics = self.token_metrics
            self.backtest_scanner.token_metrics = self.token_metrics
            self.token_monitor.token_metrics = self.token_metrics
            
            # Share TokenMetricsProcessor
            self.tier_manager.metrics_processor = self.metrics_processor
            self.token_monitor.metrics_processor = self.metrics_processor
            
            # Share RateLimiter
            self.pump_scanner.rate_limiter = self.rate_limiter
            self.backtest_scanner.rate_limiter = self.rate_limiter
            self.token_monitor.rate_limiter = self.rate_limiter
            self.wallet_analyzer.rate_limiter = self.rate_limiter
            
            # Share WalletAnalyzer
            self.pump_scanner.wallet_analyzer = self.wallet_analyzer
            self.token_monitor.wallet_analyzer = self.wallet_analyzer
            
            # Share TierManager
            self.token_monitor.tier_manager = self.tier_manager
            self.pump_scanner.tier_manager = self.tier_manager
            
            logger.info("Dependencies shared between services")
            
        except Exception as e:
            logger.error(f"Error sharing dependencies: {e}")
            raise

    async def cleanup(self) -> None:
        """Cleanup and shutdown all services"""
        try:
            logger.info("Starting service cleanup...")
            
            # Cleanup monitoring services
            if self.token_monitor:
                await self.token_monitor.stop_monitoring()
                
            if self.pump_scanner:
                await self.pump_scanner.stop()
                
            if self.backtest_scanner:
                await self.backtest_scanner.stop()
            
            # Close RPC client
            if self.rpc_client:
                await self.rpc_client.close()
                
            logger.info("Service cleanup completed")
            
        except Exception as e:
            logger.error(f"Error during service cleanup: {e}")
            raise

    async def verify_services(self) -> Dict[str, bool]:
        """
        Verify that all services are properly initialized
        
        Returns:
            Dict[str, bool]: Service status checks
        """
        return {
            'token_metrics': self.token_metrics is not None,
            'metrics_processor': self.metrics_processor is not None,
            'tier_manager': self.tier_manager is not None,
            'token_monitor': self.token_monitor is not None,
            'pump_scanner': self.pump_scanner is not None,
            'backtest_scanner': self.backtest_scanner is not None,
            'wallet_analyzer': self.wallet_analyzer is not None,
            'rug_detector': self.rug_detector is not None,
            'distribution_calculator': self.distribution_calculator is not None,
            'holder_metrics_updater': self.holder_metrics_updater is not None
        }

    def get_service_status(self) -> Dict[str, str]:
        """
        Get current status of all services
        
        Returns:
            Dict[str, str]: Service status information
        """
        statuses = {}
        
        for service_name, service in [
            ('token_metrics', self.token_metrics),
            ('metrics_processor', self.metrics_processor),
            ('tier_manager', self.tier_manager),
            ('token_monitor', self.token_monitor),
            ('pump_scanner', self.pump_scanner),
            ('backtest_scanner', self.backtest_scanner),
            ('wallet_analyzer', self.wallet_analyzer),
            ('rug_detector', self.rug_detector),
            ('distribution_calculator', self.distribution_calculator),
            ('holder_metrics_updater', self.holder_metrics_updater)
        ]:
            if service is None:
                statuses[service_name] = 'not_initialized'
            else:
                statuses[service_name] = 'running'
                
        return statuses
        
    async def get_service_metrics(self) -> Dict[str, Any]:
        """
        Get performance metrics from all services
        
        Returns:
            Dict[str, Any]: Service performance metrics
        """
        metrics = {
            'rate_limiter': self.rate_limiter.get_metrics()
        }
        
        # Add other service-specific metrics
        if self.token_monitor:
            metrics['token_monitor'] = await self.token_monitor.get_monitoring_stats()
            
        if self.pump_scanner:
            metrics['pump_scanner'] = self.pump_scanner.get_scanner_stats()
            
        if self.backtest_scanner:
            metrics['backtest_scanner'] = self.backtest_scanner.get_scanner_stats()
            
        return metrics