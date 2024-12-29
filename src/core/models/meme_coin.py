from sqlalchemy import Column, String, Integer, Float, DateTime, JSON, Boolean
from datetime import datetime
from src.config.database import Base

class MemeCoin(Base):
    __tablename__ = "meme_coins"

    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, unique=True, index=True)
    name = Column(String)
    symbol = Column(String)
    total_supply = Column(Float)
    liquidity = Column(Float)
    contract_analysis = Column(JSON)
    bonding_curve_address = Column(String)
    bonding_curve_state = Column(JSON)
    is_curve_complete = Column(Boolean, default=False)
    launch_date = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)