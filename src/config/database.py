import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from functools import wraps

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool
from asyncpg.exceptions import InterfaceError

from src.config.settings import settings

logger = logging.getLogger(__name__)

class DatabaseConnectionManager:
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseConnectionManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
            
        self._initialized = True
        self._engine: Optional[AsyncEngine] = None
        self._sessionmaker = None
        self.connection_retries = 3
        self.retry_delay = 1.0
        self.pool_recycle = 1800  # 30 minutes
        self.pool_pre_ping = True
        
    async def init(self):
        """Initialize database connection"""
        if self._engine is not None:
            return

        async with self._lock:
            if self._engine is not None:  # Double-check under lock
                return
                
            try:
                self._engine = create_async_engine(
                    self._get_async_db_url(),
                    poolclass=AsyncAdaptedQueuePool,
                    pool_pre_ping=self.pool_pre_ping,
                    pool_size=5,
                    max_overflow=10,
                    pool_timeout=30,
                    pool_recycle=self.pool_recycle,
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
                
                self._sessionmaker = async_sessionmaker(
                    self._engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autocommit=False,
                    autoflush=False
                )
                
                # Test connection
                async with self.get_session() as session:
                    await session.execute(text("SELECT 1"))
                    
                logger.info("Database connection initialized successfully")
                    
            except Exception as e:
                logger.error(f"Failed to initialize database: {str(e)}")
                await self.cleanup()
                raise

    def _get_async_db_url(self) -> str:
        """Get async database URL with correct driver"""
        db_url = settings.database_url
        
        if db_url.startswith('postgresql://'):
            return db_url.replace('postgresql://', 'postgresql+asyncpg://')
        elif db_url.startswith('postgres://'):
            return db_url.replace('postgres://', 'postgresql+asyncpg://')
        else:
            raise ValueError(
                "Unsupported database URL format. "
                "Only PostgreSQL is supported with asyncpg driver."
            )

    async def cleanup(self):
        """Cleanup database connections"""
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception as e:
                logger.error(f"Error during database cleanup: {str(e)}")
            finally:
                self._engine = None
                self._sessionmaker = None

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get database session with automatic retry on connection errors"""
        if self._sessionmaker is None:
            await self.init()

        session = None
        attempt = 0
        last_error = None

        while attempt < self.connection_retries:
            try:
                session = self._sessionmaker()
                # Test the connection
                await session.execute(text("SET timezone TO 'UTC'"))
                break
            except (SQLAlchemyError, InterfaceError) as e:
                last_error = e
                if session:
                    await session.close()
                attempt += 1
                if attempt < self.connection_retries:
                    logger.warning(
                        f"Database connection attempt {attempt} failed: {str(e)}. "
                        f"Retrying in {self.retry_delay * attempt} seconds..."
                    )
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    logger.error(f"All database connection attempts failed: {str(e)}")

        if session is None:
            raise last_error if last_error else RuntimeError("Failed to establish database connection")

        try:
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

    @property
    def engine(self) -> AsyncEngine:
        """Get database engine"""
        if self._engine is None:
            raise RuntimeError("Database not initialized")
        return self._engine

def ensure_active_event_loop(func):
    """Decorator to ensure there's an active event loop"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return await func(*args, **kwargs)
    return wrapper

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions"""
    async with db.get_session() as session:
        yield session

# Global instance
db = DatabaseConnectionManager()

# Backwards compatibility
get_async_session = get_db_session
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_db_session() as session:
        yield session