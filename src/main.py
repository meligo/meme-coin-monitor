import asyncio
import logging
import os

from src.config.database import Base, engine
from src.config.settings import RPC_ENDPOINT, settings
from src.services.backtesting.scanner import BacktestScanner
from src.services.pump_fun.scanner import PumpFunScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s'
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

async def run_live_scanner():
    """Run the live scanner for real-time monitoring"""
    logger.info("Starting Live Pump.fun Token Scanner...")
    scanner = PumpFunScanner()
    
    # Start the token processor
    await scanner.token_processor.start()
    
    # Create tasks for scanning existing tokens and listening for new ones
    scan_task = asyncio.create_task(scanner.scan_existing_tokens())
    listen_task = asyncio.create_task(scanner.listen_for_new_tokens())

    try:
        # Run both tasks concurrently
        await asyncio.gather(scan_task, listen_task)
    except Exception as e:
        logger.error(f"Error in live scanner: {e}", exc_info=True)
        raise

async def run_backtest_scanner():
    """Run full historical analysis of all pump.fun tokens"""
    logger.info("Starting Full Historical Analysis of Pump.fun Tokens...")
    
    # For debugging
    batch_size_env = os.getenv('BACKTEST_BATCH_SIZE', 20)
    logger.info(f"BACKTEST_BATCH_SIZE env value: -{batch_size_env}-")
    
    try:
        batch_size = int(batch_size_env.strip())  # Strip any whitespace
        scanner = BacktestScanner(batch_size=batch_size)
        await scanner.scan_historical_tokens()
        logger.info("Historical analysis completed")
    except Exception as e:
        logger.error(f"Error in historical scanner: {e}", exc_info=True)
        raise

async def main():
    logger.info(f"Connecting to RPC endpoint: {RPC_ENDPOINT}")

    # Create database tables
    Base.metadata.create_all(bind=engine)

    try:
        # Run both scanners concurrently
        await asyncio.gather(
            run_live_scanner(),
            run_backtest_scanner()
        )
    except KeyboardInterrupt:
        logger.info("Shutting down scanners...")
    except Exception as e:
        logger.error(f"Error in main loop: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())