import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

from sqlalchemy import and_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import db, get_db_session
from src.config.settings import settings
from src.core.models import MemeCoin, TokenTier
from src.services.backtesting.scanner import BacktestScanner
from src.services.manager import ServiceManager
from src.services.monitoring.performance import measure_performance
from src.services.monitoring.token_monitor import TokenMonitor
from src.services.pump_fun.scanner import PumpFunScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f'meme_monitor_{datetime.now(timezone.utc).strftime("%Y%m%d")}.log')
    ]
)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

class MemeMonitor:
    def __init__(self):
        self.service_manager = ServiceManager()
        self.token_monitor = TokenMonitor()
        self.pump_scanner = PumpFunScanner()
        batch_size = int(os.getenv('BACKTEST_BATCH_SIZE', '20').strip())
        self.backtest_scanner = BacktestScanner(batch_size=batch_size)  # Initialize once
        self.is_running = False
        self._tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()
        self._backtest_lock = asyncio.Lock()  # Add lock for backtest synchronization


    def _handle_signals(self):
        """Setup signal handlers for graceful shutdown"""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                asyncio.get_event_loop().add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._shutdown(s))
                )
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda s, f: asyncio.create_task(self._shutdown(s)))

    def _register_task(self, task: asyncio.Task):
        """Register a task to be managed"""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._handle_task_exception)

    def _handle_task_exception(self, task: asyncio.Task):
        """Handle any exceptions from completed tasks"""
        try:
            exc = task.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                logger.error(f"Task {task.get_name()} failed with exception: {exc}")
                asyncio.create_task(self._shutdown(None))  # Trigger shutdown on task failure
        except asyncio.CancelledError:
            pass

    async def _shutdown(self, sig):
        """Handle graceful shutdown"""
        if sig:
            logger.info(f"Received exit signal {sig.name if hasattr(sig, 'name') else sig}...")
        
        if not self.is_running:
            return

        self.is_running = False
        self._shutdown_event.set()
        
        logger.info("Canceling pending tasks...")
        tasks = [t for t in self._tasks if not t.done()]
        
        if tasks:
            logger.info(f"Cancelling {len(tasks)} pending tasks...")
            for task in tasks:
                if not task.done():
                    task.cancel()
            
            await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("Stopping services...")
        await self.stop()

    async def _get_backlog_tokens(self, db: AsyncSession) -> List[TokenTier]:   
        """Get backlog tokens with proper timezone handling."""
        current_time = datetime.now(timezone.utc).replace(tzinfo=None)
        check_time = current_time - timedelta(minutes=30)
        check_time_naive = check_time.replace(tzinfo=None)

        result = await db.execute(
            select(TokenTier).where(
                and_(
                    TokenTier.is_active == true(),
                    TokenTier.current_tier != 'RUGGED',
                    TokenTier.last_checked < check_time_naive
                )
            ).order_by(TokenTier.processing_priority.desc())
        )
        return result.scalars().all()

    async def start_live_scanner(self):
        """Run the live scanner for real-time monitoring"""
        logger.info("Starting Live Pump.fun Token Scanner...")
        if not self.is_running:
            return

        await self.pump_scanner.token_processor.start()
        
        scan_task = asyncio.create_task(
            self.pump_scanner.scan_existing_tokens(),
            name="scan_existing_tokens"
        )
        listen_task = asyncio.create_task(
            self.pump_scanner.listen_for_new_tokens(),
            name="listen_for_new_tokens"
        )
        
        self._register_task(scan_task)
        self._register_task(listen_task)

        try:
            await asyncio.gather(scan_task, listen_task)
        except asyncio.CancelledError:
            logger.info("Live scanner tasks cancelled")
        except Exception as e:
            logger.error(f"Error in live scanner: {e}", exc_info=True)
            raise

    async def start_backtest_scanner(self):
        """Run full historical analysis of all pump.fun tokens"""
        if not self.is_running:
            return

        async with self._backtest_lock:  # Use lock to prevent concurrent scanning
            logger.info("Starting Full Historical Analysis of Pump.fun Tokens...")
            
            try:
                await self.backtest_scanner.token_processor.start()  # Start processor if not started
                await self.backtest_scanner.scan_historical_tokens()
            except asyncio.CancelledError:
                logger.info("Backtest scanner cancelled")
            except Exception as e:
                logger.error(f"Error in historical scanner: {e}", exc_info=True)
                raise

    async def resume_monitoring(self):
        """Resume monitoring for any paused or stuck tokens"""
        if not self.is_running:
            return

        async with get_db_session() as db:
            try:
                stuck_tokens = await self._get_backlog_tokens(db)
                current_time = datetime.now(timezone.utc).replace(tzinfo=None)
                
                for token in stuck_tokens:
                    logger.info(f"Resuming monitoring for token {token.token_address}")
                    token.is_monitoring_paused = False
                    token.next_check_at = current_time
                    
                await db.commit()
            except Exception as e:
                logger.error(f"Error resuming monitoring: {e}")
                await db.rollback()
                raise

    async def cleanup_orphaned_tokens(self):
        """Clean up any orphaned token entries"""
        async with get_db_session() as db:
            try:
                result = await db.execute(
                    select(TokenTier).outerjoin(
                        MemeCoin, TokenTier.token_address == MemeCoin.address
                    ).where(MemeCoin.id == None)
                )
                orphaned_tiers = result.scalars().all()
                
                for tier in orphaned_tiers:
                    logger.warning(f"Found orphaned tier entry for {tier.token_address}")
                    tier.is_active = False
                    
                await db.commit()
            except Exception as e:
                logger.error(f"Error cleaning orphaned tokens: {e}")
                await db.rollback()
                raise
            
    @measure_performance('initialization')
    async def start(self):
        """Start all monitoring systems with performance tracking"""
        logger.info("Starting Meme Coin Monitoring System...")
        self.is_running = True
        self._handle_signals()

        try:
            # Initialize database first
            await db.init()
            
            # Initialize and start all services
            await self.service_manager.initialize()
            await self.service_manager.start_services()
            
            # Initialize core components
            await self.token_monitor.start_monitoring()
            await self.pump_scanner.token_processor.start()
            
            # Create and register tasks
            tasks = []
            
            # Live scanner tasks
            scan_task = asyncio.create_task(
                self.pump_scanner.scan_existing_tokens(),
                name="pump_scanner"
            )
            listen_task = asyncio.create_task(
                self.pump_scanner.listen_for_new_tokens(),
                name="pump_scanner_listener"
            )
            tasks.extend([scan_task, listen_task])
            self._register_task(scan_task)
            self._register_task(listen_task)
            
            # Initialize backtest scanner before tasks
            batch_size = int(os.getenv('BACKTEST_BATCH_SIZE', '20').strip())
            self.backtest_scanner = BacktestScanner(batch_size=batch_size)
            
            # Backlog processing task
            backlog_task = asyncio.create_task(
                self.process_backlog(),
                name="process_backlog"
            )
            tasks.append(backlog_task)
            self._register_task(backlog_task)
            
            # Backtest scanner task
            if self.backtest_scanner:
                backtest_task = asyncio.create_task(
                    self.backtest_scanner.scan_historical_tokens(),
                    name="backtest_scanner"
                )
                tasks.append(backtest_task)
                self._register_task(backtest_task)
            
            # Setup health check task
            health_check = asyncio.create_task(
                self.service_manager._run_health_check(),
                name="health_check"
            )
            tasks.append(health_check)
            self._register_task(health_check)
            
            # Wait for either shutdown event or task completion
            while self.is_running and not self._shutdown_event.is_set():
                done, pending = await asyncio.wait(
                    tasks,
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Check for any errors in completed tasks
                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        logger.info(f"Task {task.get_name()} was cancelled")
                    except Exception as e:
                        logger.error(f"Task {task.get_name()} failed: {e}")
                        raise
                
                tasks = list(pending)  # Update task list
                
                if not tasks:
                    break
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await self.stop()
            raise
        
    async def process_backlog(self):
        """Process backlog using BacktestScanner with proper error handling"""
        if not self.is_running:
            return

        logger.info("Processing token backlog...")
        
        try:
            async with self._backtest_lock:  # Synchronize backtest scanner usage
                await self.backtest_scanner.token_processor.start()
        
                async with get_db_session() as db:
                    backlog_tokens = await self._get_backlog_tokens(db)
                    
                    if not backlog_tokens:
                        logger.info("No tokens in backlog")
                        return
                        
                    logger.info(f"Found {len(backlog_tokens)} tokens in backlog")
                    current_time = datetime.now(timezone.utc).replace(tzinfo=None)
                    
                    # Create a semaphore to limit concurrent token processing
                    semaphore = asyncio.Semaphore(5)
                    
                    async def process_with_semaphore(token):
                        if not self.is_running:
                            return

                        async with semaphore:
                            try:
                                # Mark token as being processed
                                token.is_monitoring_paused = True
                                await db.commit()
                                
                                # Process the token
                                success = await self.backtest_scanner.process_token(token.token_address)
                                
                                # Update token status after processing
                                token.is_monitoring_paused = False
                                token.last_checked = current_time
                                if success:
                                    token.next_check_at = current_time + timedelta(
                                        seconds=token.check_frequency
                                    )
                                await db.commit()
                                
                            except Exception as e:
                                logger.error(f"Error processing token {token.token_address}: {e}")
                                await db.rollback()
                                raise
                    
                    # Create and register tasks for each token
                    tasks = []
                    for token in backlog_tokens:
                        task = asyncio.create_task(
                            process_with_semaphore(token),
                            name=f"process_token_{token.token_address}"
                        )
                        self._register_task(task)
                        tasks.append(task)
                    
                    # Wait for all token processing tasks to complete
                    await asyncio.gather(*tasks, return_exceptions=True)
                    logger.info("Backlog processing completed")
                        
        except asyncio.CancelledError:
            logger.info("Backlog processing cancelled")
        except Exception as e:
            logger.error(f"Failed to process backlog: {e}")
            raise
        finally:
            if self.backtest_scanner and self.is_running:
                await self.backtest_scanner.token_processor.stop()
            
    async def stop(self):
        """Stop all monitoring systems"""
        if not self.is_running:
            return

        logger.info("Shutting down Meme Coin Monitoring System...")
        
        try:
            await self.service_manager.cleanup()
            await self.token_monitor.stop_monitoring()
            if self.pump_scanner:
                await self.pump_scanner.stop()
                
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise
        finally:
            await db.cleanup()
            logger.info("Monitoring system shutdown complete")

async def main():
    """Main entry point with proper event loop handling"""
    monitor = MemeMonitor()
    
    try:
        # Ensure we have an event loop
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        await monitor.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        raise
    finally:
        # Clean shutdown of event loop
        try:
            await monitor.stop()
        except Exception as e:
            logger.error(f"Error during final cleanup: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
    except Exception as e:
        logger.error(f"Application crashed: {e}", exc_info=True)
        raise