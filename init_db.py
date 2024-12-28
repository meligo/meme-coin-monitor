import os
import sys
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import text

# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

from src.config.settings import settings
from src.config.database import engine, Base
from src.core.models.meme_coin import MemeCoin

def init_database():
    print("Initializing database...")
    
    # Create database if it doesn't exist
    if not database_exists(engine.url):
        print(f"Creating database: {engine.url.database}")
        create_database(engine.url)
        print("Database created successfully!")
    else:
        print(f"Database {engine.url.database} already exists")

    # Create all tables
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")

    # Verify tables
    with engine.connect() as connection:
        result = connection.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """))
        tables = [row[0] for row in result]
        print("\nCreated tables:")
        for table in tables:
            print(f"- {table}")

        # Get columns for meme_coins table
        print("\nMeme Coins table structure:")
        result = connection.execute(text("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'meme_coins'
            ORDER BY ordinal_position
        """))
        
        for row in result:
            col_name, data_type, max_length = row
            if max_length:
                print(f"- {col_name}: {data_type}({max_length})")
            else:
                print(f"- {col_name}: {data_type}")

if __name__ == "__main__":
    try:
        init_database()
        print("\nDatabase initialization completed successfully!")
    except Exception as e:
        print(f"\nError initializing database: {str(e)}")
        raise