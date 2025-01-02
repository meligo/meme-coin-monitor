import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from sqlalchemy import and_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_async_session
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
        self.rpc_client = AsyncClient(
            settings['RPC_ENDPOINT'], 
            commitment=Commitment("confirmed"), 
            timeout=30.0
        )
        self.token_monitor = TokenMonitor()
        self.service_manager = ServiceManager()
        self.pump_scanner = PumpFunScanner()
        self.backtest_scanner: Optional[BacktestScanner] = None
        self.is_running = False

    async def _get_backlog_tokens(self, db: AsyncSession) -> List[TokenTier]:
        """Get backlog tokens with proper timezone handling."""
        current_time = datetime.now(timezone.utc).replace(tzinfo=None)
        check_time = current_time - timedelta(minutes=30)

        # Remove timezone info for db comparison
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
        await self.pump_scanner.token_processor.start()
        
        scan_task = asyncio.create_task(self.pump_scanner.scan_existing_tokens())
        listen_task = asyncio.create_task(self.pump_scanner.listen_for_new_tokens())

        try:
            await asyncio.gather(scan_task, listen_task)
        except Exception as e:
            logger.error(f"Error in live scanner: {e}", exc_info=True)
            raise

    async def start_backtest_scanner(self):
        """Run full historical analysis of all pump.fun tokens"""
        logger.info("Starting Full Historical Analysis of Pump.fun Tokens...")
        logger.info(f"BACKTEST_BATCH_SIZE env value: -{os.getenv('BACKTEST_BATCH_SIZE', '100')}-")
        
        try:
            batch_size = int(os.getenv('BACKTEST_BATCH_SIZE', '20').strip())
            self.backtest_scanner = BacktestScanner(batch_size=batch_size)
            await self.backtest_scanner.scan_historical_tokens()
        except ValueError as e:
            logger.error(f"Invalid BACKTEST_BATCH_SIZE: {e}")
            raise
        except Exception as e:
            logger.error(f"Error in historical scanner: {e}", exc_info=True)
            raise

    async def resume_monitoring(self):
        """Resume monitoring for any paused or stuck tokens"""
        async with get_async_session() as db:
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
        async with get_async_session() as db:
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
        
        await self.service_manager.initialize_with_rpc(self.rpc_client)
        
        try:
            await self.token_monitor.start_monitoring()
            await self.process_backlog()
            
            await asyncio.gather(
                self.start_live_scanner(),
                self.start_backtest_scanner()
            )
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await self.stop()
            raise
        
    async def process_backlog(self):
        """Process backlog using BacktestScanner with proper error handling"""
        logger.info("Processing token backlog...")
        
        try:
            if not self.backtest_scanner:
                batch_size = int(os.getenv('BACKTEST_BATCH_SIZE', '20').strip())
                self.backtest_scanner = BacktestScanner(batch_size=batch_size)
                await self.backtest_scanner.token_processor.start()
        
            async with get_async_session() as db:
                backlog_tokens = await self._get_backlog_tokens(db)
                
                if not backlog_tokens:
                    logger.info("No tokens in backlog")
                    return
                    
                logger.info(f"Found {len(backlog_tokens)} tokens in backlog")
                current_time = datetime.now(timezone.utc).replace(tzinfo=None)
                
                semaphore = asyncio.Semaphore(5)
                
                async def process_with_semaphore(token):
                    async with semaphore:
                        try:
                            token.is_monitoring_paused = True
                            await db.commit()
                            
                            success = await self.backtest_scanner.process_token(token.token_address)
                            
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
                
                tasks = [
                    asyncio.create_task(process_with_semaphore(token))
                    for token in backlog_tokens
                ]
                
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("Backlog processing completed")
                    
        except Exception as e:
            logger.error(f"Failed to initialize backlog processing: {e}")
            raise
        finally:
            if self.backtest_scanner:
                await self.backtest_scanner.token_processor.stop()
            
    async def stop(self):
        """Stop all monitoring systems"""
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
            logger.info("Monitoring system shutdown complete")

async def main():
    monitor = MemeMonitor()
    try:
        await monitor.start()
    except KeyboardInterrupt:
        await monitor.stop()
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        await monitor.stop()
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
    except Exception as e:
        logger.error(f"Application crashed: {e}", exc_info=True)
        raise