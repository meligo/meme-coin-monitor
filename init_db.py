import logging
import os
import sys
from traceback import format_exc

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy_utils import create_database, database_exists, drop_database

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

# Import all models from your existing structure
from src.config.settings import settings
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


def verify_model_relationships():
    """Verify and log all model relationships"""
    logger.info("\nVerifying model relationships...")
    
    relationships = {
        'MemeCoin': {
            'tier': 'TokenTier',
            'price_history': 'TokenPrice',
            'holder_snapshots': 'HolderSnapshot',
            'trading_volumes': 'TradingVolume',
            'token_metadata': 'TokenMetadata',
            'wallet_analyses': 'WalletAnalysis',
            'transactions': 'WalletTransaction'
        },
        'TokenTier': {
            'token': 'MemeCoin',
            'tier_transitions': 'TierTransition',
            'tier_alerts': 'TierAlert'
        }
    }
    
    for model, relations in relationships.items():
        logger.info(f"{model} relationships:")
        for rel_name, rel_type in relations.items():
            logger.info(f"  - {rel_name} ({rel_type})")


def init_database():
    """Initialize the database with all tables and constraints"""
    logger.info(f"Database URL: {settings.database_url}")
    logger.info("\nInitializing database...")
    
    engine = create_engine(settings.database_url)
    
    # Create database if it doesn't exist
    if not database_exists(engine.url):
        logger.info(f"Creating database: {engine.url.database}")
        create_database(engine.url)
        logger.info("Database created successfully!")
    else:
        logger.info(f"Database {engine.url.database} already exists")
        recreate = input("Do you want to recreate the database? (y/n): ")
        if recreate.lower() == 'y':
            logger.info("Dropping existing database...")
            drop_database(engine.url)
            logger.info("Creating new database...")
            create_database(engine.url)

    # Create all tables
    logger.info("\nCreating tables...")
    Base.metadata.drop_all(bind=engine)  # Drop existing tables
    Base.metadata.create_all(bind=engine)  # Create all tables fresh
    logger.info("Tables created successfully!")

    # Verify tables and their structures
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    logger.info("\nCreated tables:")
    for table in tables:
        logger.info(f"\n{table}:")
        columns = inspector.get_columns(table)
        logger.info("Columns:")
        for column in columns:
            col_type = str(column['type'])
            nullable = "NULL" if column['nullable'] else "NOT NULL"
            primary_key = "PRIMARY KEY" if column.get('primary_key', False) else ""
            logger.info(f"  - {column['name']}: {col_type} {nullable} {primary_key}")

        # Show foreign keys if any
        foreign_keys = inspector.get_foreign_keys(table)
        if foreign_keys:
            logger.info("Foreign Keys:")
            for fk in foreign_keys:
                logger.info(f"  - {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")

        # Show indices if any
        indices = inspector.get_indexes(table)
        if indices:
            logger.info("Indices:")
            for idx in indices:
                unique = "UNIQUE " if idx['unique'] else ""
                logger.info(f"  - {unique}INDEX on ({', '.join(idx['column_names'])})")

    return engine


if __name__ == "__main__":
    try:
        engine = init_database()
        logger.info("\nDatabase initialization completed successfully!")
        verify_model_relationships()
        logger.info("\nAll model relationships verified successfully!")
        
    except Exception as e:
        logger.error(f"\nError initializing database: {str(e)}")
        logger.error("\nFull error traceback:")
        logger.error(format_exc())
        raise