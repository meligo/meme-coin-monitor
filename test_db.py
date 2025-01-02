from sqlalchemy import text
from src.config.database import engine, Base
from src.core.models import *  # This will import all models

def test_db_connection():
    try:
        # Test connection
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            print("Database connection successful!")
            
            # Create all tables
            print("Creating tables...")
            Base.metadata.drop_all(bind=engine)  # Drop all existing tables
            Base.metadata.create_all(bind=engine)  # Create all tables fresh
            print("Tables created successfully!")
            
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    test_db_connection()