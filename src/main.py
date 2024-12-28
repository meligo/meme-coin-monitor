import asyncio
import logging
import os
import sys

# Add parent directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from services.pump_fun.scanner import PumpFunScanner
from config.settings import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Starting Pump.fun Token Scanner...")
    logger.info(f"Connecting to RPC endpoint: {settings.RPC_ENDPOINT}")
    
    scanner = PumpFunScanner()
    
    try:
        logger.info("Initializing scanner...")
        # Start monitoring both new and existing tokens
        await scanner.start_monitoring()
    except KeyboardInterrupt:
        logger.info("Shutting down scanner...")
    except Exception as e:
        logger.error(f"Error in main loop: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScanner stopped by user")
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        raise