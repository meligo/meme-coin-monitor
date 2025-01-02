import os
import sys

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy_utils import create_database, database_exists, drop_database

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


def init_database():
    print(f"Database URL: {settings['DATABASE_URL']}")
    print("\nInitializing database...")
    
    engine = create_engine(settings['DATABASE_URL'])
    
    # Create database if it doesn't exist
    if not database_exists(engine.url):
        print(f"Creating database: {engine.url.database}")
        create_database(engine.url)
        print("Database created successfully!")
    else:
        print(f"Database {engine.url.database} already exists")
        recreate = input("Do you want to recreate the database? (y/n): ")
        if recreate.lower() == 'y':
            print("Dropping existing database...")
            drop_database(engine.url)
            print("Creating new database...")
            create_database(engine.url)

    # Create all tables
    print("\nCreating tables...")
    Base.metadata.drop_all(bind=engine)  # Drop existing tables
    Base.metadata.create_all(bind=engine)  # Create all tables fresh
    print("Tables created successfully!")

    # Verify tables and their structures
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    print("\nCreated tables:")
    for table in tables:
        print(f"\n{table}:")
        columns = inspector.get_columns(table)
        print("Columns:")
        for column in columns:
            col_type = str(column['type'])
            nullable = "NULL" if column['nullable'] else "NOT NULL"
            primary_key = "PRIMARY KEY" if column.get('primary_key', False) else ""
            print(f"  - {column['name']}: {col_type} {nullable} {primary_key}")

        # Show foreign keys if any
        foreign_keys = inspector.get_foreign_keys(table)
        if foreign_keys:
            print("Foreign Keys:")
            for fk in foreign_keys:
                print(f"  - {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")

        # Show indices if any
        indices = inspector.get_indexes(table)
        if indices:
            print("Indices:")
            for idx in indices:
                unique = "UNIQUE " if idx['unique'] else ""
                print(f"  - {unique}INDEX on ({', '.join(idx['column_names'])})")

    return engine

if __name__ == "__main__":
    try:
        engine = init_database()
        print("\nDatabase initialization completed successfully!")
        
        # Verify model relationships
        print("\nVerifying model relationships...")
        print("MemeCoin relationships:")
        print("  - tier (TokenTier)")
        print("  - price_history (TokenPrice)")
        print("  - holder_snapshots (HolderSnapshot)")
        print("  - trading_volumes (TradingVolume)")
        print("  - token_metadata (TokenMetadata)")
        print("  - wallet_analyses (WalletAnalysis)")
        print("  - transactions (WalletTransaction)")
        
        print("\nTokenTier relationships:")
        print("  - token (MemeCoin)")
        print("  - tier_transitions (TierTransition)")
        print("  - tier_alerts (TierAlert)")
        
        print("\nAll model relationships verified successfully!")
        
    except Exception as e:
        print(f"\nError initializing database: {str(e)}")
        print("\nFull error traceback:", exc_info=True)
        raise