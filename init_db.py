import os
import sys
from sqlalchemy_utils import database_exists, create_database, drop_database
from sqlalchemy import text, create_engine, MetaData, inspect

# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

from src.config.settings import settings
from src.config.database import engine
from src.core.models import Base, MemeCoin  # Import all models

def init_database():
    print(f"Database URL: {settings.DATABASE_URL}")
    print("\nInitializing database...")
    
    # Create database if it doesn't exist
    if not database_exists(engine.url):
        print(f"Creating database: {engine.url.database}")
        create_database(engine.url)
        print("Database created successfully!")
    else:
        print(f"Database {engine.url.database} already exists")
        # Optionally recreate the database
        recreate = input("Do you want to recreate the database? (y/n): ")
        if recreate.lower() == 'y':
            print("Dropping existing database...")
            drop_database(engine.url)
            print("Creating new database...")
            create_database(engine.url)

    # Create all tables
    print("\nCreating tables...")
    Base.metadata.drop_all(bind=engine)  # Drop existing tables
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")

    # Verify tables
    with engine.connect() as connection:
        # Use inspect() instead of dialect.get_inspector()
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        print("\nCreated tables:")
        for table in tables:
            print(f"- {table}")
            columns = inspector.get_columns(table)
            print("\nColumns:")
            for column in columns:
                col_type = str(column['type'])
                nullable = "NULL" if column['nullable'] else "NOT NULL"
                print(f"  - {column['name']}: {col_type} {nullable}")

if __name__ == "__main__":
    try:
        init_database()
        print("\nDatabase initialization completed successfully!")
    except Exception as e:
        print(f"\nError initializing database: {str(e)}")
        raise