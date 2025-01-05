from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.core.models.base import BaseModel


class TaskPerformance(BaseModel):
    """Performance metrics for monitoring tasks"""

    task_name = Column(String(255), nullable=False, index=True)
    execution_time = Column(Float, nullable=False)  # in seconds
    success = Column(Boolean, nullable=False)
    error_message = Column(String)
    memory_usage = Column(Float)  # in MB
    cpu_usage = Column(Float)  # percentage
    extra_data = Column(JSONB)  # Changed from metadata to extra_data

    def __repr__(self):
        return (
            f"<TaskPerformance(task={self.task_name}, "
            f"time={self.execution_time:.2f}s, success={self.success})>"
        )


class RPCPerformance(BaseModel):
    """Performance metrics for RPC endpoints"""

    endpoint = Column(String(255), nullable=False, index=True)
    request_type = Column(String(50), nullable=False)
    response_time = Column(Float, nullable=False)  # in seconds
    success = Column(Boolean, nullable=False)
    error_type = Column(String(255))
    error_message = Column(String)
    extra_data = Column(JSONB)  # Changed from metadata to extra_data

    def __repr__(self):
        return (
            f"<RPCPerformance(endpoint={self.endpoint}, "
            f"type={self.request_type}, time={self.response_time:.3f}s)>"
        )


class DatabasePerformance(BaseModel):
    """Performance metrics for database operations"""

    operation_type = Column(String(50), nullable=False, index=True)
    table_name = Column(String(255), nullable=False)
    execution_time = Column(Float, nullable=False)  # in seconds
    rows_affected = Column(Integer)
    query_plan = Column(JSONB)
    extra_data = Column(JSONB)  # Changed from metadata to extra_data

    def __repr__(self):
        return (
            f"<DatabasePerformance(op={self.operation_type}, "
            f"table={self.table_name}, time={self.execution_time:.3f}s)>"
        )