from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.orm import declarative_base, declared_attr


class BaseClass:
    @declared_attr
    def __tablename__(cls) -> str:
        """Convert CamelCase class name to snake_case table name"""
        import re
        name = re.sub('([A-Z])', r'_\1', cls.__name__).lower().lstrip('_')
        return name

    # Primary key for all tables
    id = Column(Integer, primary_key=True, index=True)
    
    # Audit timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

# Create the declarative base
Base = declarative_base(cls=BaseClass)
BaseModel = Base  # For backward compatibility