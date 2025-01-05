import asyncio
import logging
from sqlalchemy import text
from src.config.database import db, Base, ensure_active_event_loop
from src.core.models.base import Base
from src.core.models.meme_coin import (
    HolderSnapshot,
    MemeCoin,
    TokenMetadata,
    TokenPrice,
    TradingVolume,
)
from src.core.models.tier_models import TierAlert, TierTransition, TokenTier
from src.core.models.wallet_analysis import WalletAnalysis, WalletTransaction

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s'
)
logger = logging.getLogger(__name__)

@ensure_active_event_loop
async def test_db_connection():
    """Test database connection and table creation"""
    try:
        # Initialize database connection
        await db.init()
        logger.info("Database connection initialized")

        # Test basic query
        async with db.get_session() as session:
            result = await session.execute(text("SELECT 1"))
            await result.fetchone()
            logger.info("Basic query test successful")

        # Create all tables
        logger.info("Creating tables...")
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created successfully")

        # Verify each model can be accessed
        async with db.get_session() as session:
            for model in [MemeCoin, TokenMetadata, TokenPrice, HolderSnapshot,
                         TradingVolume, TokenTier, TierTransition, TierAlert,
                         WalletAnalysis, WalletTransaction]:
                try:
                    await session.execute(text(f'SELECT 1 FROM {model.__tablename__} LIMIT 1'))
                    logger.info(f"Table {model.__tablename__} verified")
                except Exception as e:
                    logger.error(f"Error verifying table {model.__tablename__}: {str(e)}")
                    raise

    except Exception as e:
        logger.error(f"Database test failed: {str(e)}")
        raise
    finally:
        # Cleanup
        await db.cleanup()
        logger.info("Database connection cleaned up")

if __name__ == "__main__":
    try:
        asyncio.run(test_db_connection())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {str(e)}")
        raise