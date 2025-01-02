import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import AsyncAdaptedQueuePool

from src.config.settings import settings
from src.core.models.base import Base

logger = logging.getLogger(__name__)

# Create declarative base class for models
Base = declarative_base()

def get_async_db_url() -> str:
    db_url = settings['DATABASE_URL']
    return db_url.replace('postgresql://', 'postgresql+asyncpg://') if db_url.startswith('postgresql://') else db_url

def get_sync_db_url() -> str:
    db_url = settings['DATABASE_URL']
    
    if db_url.startswith('postgresql://'):
        return db_url.replace('postgresql://', 'postgresql+asyncpg://')
    elif db_url.startswith('postgres://'):
        return db_url.replace('postgres://', 'postgresql+asyncpg://')
    else:
        raise ValueError(
            "Unsupported database URL format. "
            "Only PostgreSQL is supported with asyncpg driver."
        )

# Updated engine configuration with timezone settings
engine = create_async_engine(
    get_async_db_url(),
    poolclass=AsyncAdaptedQueuePool,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    echo=False,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "command_timeout": 60,
        "server_settings": {
            "timezone": "UTC",
            "application_name": "meme_coin_monitor"
        }
    }
)

def create_engine() -> AsyncEngine:
    try:
        return create_async_engine(
            get_async_db_url(),
            poolclass=AsyncAdaptedQueuePool,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
            echo=False,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "command_timeout": 60,
                "server_settings": {
                    "timezone": "UTC",
                    "application_name": "meme_coin_monitor"
                }
            }
        )
    except Exception as e:
        logger.error(f"Failed to create database engine: {str(e)}")
        raise

async def init_models():
    try:
        engine = create_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database models initialized successfully")
    except SQLAlchemyError as e:
        logger.error(f"Database initialization failed: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during database initialization: {str(e)}")
        raise

def create_session_maker() -> async_sessionmaker[AsyncSession]:
    try:
        return async_sessionmaker(
            bind=create_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False
        )
    except Exception as e:
        logger.error(f"Failed to create session maker: {str(e)}")
        raise

# Create global session maker
AsyncSessionMaker = create_session_maker()

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    session = AsyncSessionMaker()
    try:
        # Ensure all datetime operations use UTC
        await session.execute(text("SET timezone TO 'UTC'"))
        yield session
        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        logger.error(f"Database error occurred: {str(e)}")
        raise
    except Exception as e:
        await session.rollback()
        logger.error(f"Unexpected error occurred: {str(e)}")
        raise
    finally:
        await session.close()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_async_session() as session:
        yield session

class DatabaseSessionManager:
    def __init__(
        self,
        retry_attempts: int = 3,
        retry_delay: float = 1.0
    ):
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self._engine = create_engine()
        self._session_maker = create_session_maker()

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[AsyncSession, None]:
        attempt = 0
        last_error = None

        while attempt < self.retry_attempts:
            try:
                async with get_async_session() as session:
                    yield session
                    return
            except SQLAlchemyError as e:
                last_error = e
                attempt += 1
                if attempt < self.retry_attempts:
                    logger.warning(
                        f"Database connection attempt {attempt} failed: {str(e)}. "
                        f"Retrying in {self.retry_delay} seconds..."
                    )
                    await asyncio.sleep(self.retry_delay)
                else:
                    logger.error(
                        f"All database connection attempts failed: {str(e)}"
                    )

        raise last_error

    async def close(self):
        try:
            await self._engine.dispose()
        except Exception as e:
            logger.error(f"Error closing database connections: {str(e)}")
            raise

db_manager = DatabaseSessionManager()