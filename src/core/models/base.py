from sqlalchemy import Column, Integer, DateTime
from datetime import datetime
from sqlalchemy.orm import declarative_base

# Create the declarative base
Base = declarative_base()

class BaseModel(Base):
    """Base model class that other models will inherit from"""
    __abstract__ = True

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)