from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from src.core.models.base import Base


class PerformanceMetrics(Base):
    __tablename__ = 'performance_metrics'
    
    id = Column(Integer, primary_key=True)
    function_name = Column(String(255), nullable=False, index=True)
    execution_time = Column(Float, nullable=False)
    success = Column(Integer, nullable=False)
    error_message = Column(String(1000))
    timestamp = Column(DateTime, nullable=False)
    context = Column(String(255), index=True)
    module = Column(String(255), index=True)
    extra_data = Column(String(1000))

    __table_args__ = {'comment': 'Records performance metrics for system monitoring'}