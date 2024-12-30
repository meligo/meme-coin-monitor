from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.models.base import Base
from src.core.models.tier_models import TokenTier


class MemeCoin(Base):
    __tablename__ = 'meme_coins'

    # Core Information
    id = Column(Integer, primary_key=True)
    # Changed address length to 64 to accommodate longer addresses
    address = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255))
    symbol = Column(String(255))
    decimals = Column(Integer)
    total_supply = Column(BigInteger)
    
    # Contract Details
    creator_address = Column(String(255))  # Increased from 42
    creation_tx_hash = Column(Text)
    contract_verified = Column(Boolean, default=False)
    contract_code = Column(Text, nullable=True)
    contract_analysis = Column(JSONB)
    
    # Supply Information
    circulating_supply = Column(BigInteger)
    burned_supply = Column(BigInteger)
    
    # Bonding Curve Information
    bonding_curve_address = Column(String(255))  # Increased from 42
    bonding_curve_state = Column(JSONB)
    is_curve_complete = Column(Boolean)
    tokens_left = Column(BigInteger)
    bonding_curve_completion = Column(Float)
    
    # King of the Hill Information
    king_of_hill_progress = Column(Float)
    
    # Market Information
    price_usd = Column(Float)
    market_cap_usd = Column(Float)
    volume_24h_usd = Column(Float)
    liquidity = Column(Float)
    liquidity_pairs = Column(JSONB)  # Stores all trading pair information
    
    # Holder Information
    holder_count = Column(Integer)
    holder_distribution = Column(JSONB)  # Distribution statistics
    top_holders = Column(JSONB)  # Top holder addresses and balances
    
    # Risk Metrics
    risk_score = Column(Float)
    risk_factors = Column(JSONB)
    
    # Monitoring Metrics
    whale_holdings = Column(Float)
    smart_money_flow = Column(Float)
    holder_growth_rate = Column(Float)
    
    # Timestamps
    launch_date = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    tier = relationship("TokenTier", back_populates="token", uselist=False)
    price_history = relationship("TokenPrice", back_populates="token")
    holder_snapshots = relationship("HolderSnapshot", back_populates="token")
    trading_volumes = relationship("TradingVolume", back_populates="token")
    token_metadata = relationship("TokenMetadata", back_populates="token", uselist=False)

    __table_args__ = {'comment': 'Main table storing meme coin information and metrics'}


# Keep the rest of the models unchanged...
class TokenPrice(Base):
    __tablename__ = 'token_prices'
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    price_usd = Column(Float, nullable=False)
    volume_usd = Column(Float)
    market_cap_usd = Column(Float)
    liquidity_usd = Column(Float)
    
    token = relationship("MemeCoin", back_populates="price_history")
    
    __table_args__ = (
        {'comment': 'Historical price and market data for tokens'}
    )

class HolderSnapshot(Base):
    __tablename__ = 'holder_snapshots'
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    holder_count = Column(Integer)
    holder_distribution = Column(JSONB)  # Snapshot of holder distribution
    concentration_metrics = Column(JSONB)  # Gini coefficient, etc.
    
    token = relationship("MemeCoin", back_populates="holder_snapshots")
    
    __table_args__ = (
        {'comment': 'Historical holder data and distribution metrics'}
    )

class TradingVolume(Base):
    __tablename__ = 'trading_volumes'
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    volume_usd = Column(Float)
    trade_count = Column(Integer)
    buyer_count = Column(Integer)
    seller_count = Column(Integer)
    whale_volume = Column(Float)  # Volume from whale addresses
    smart_money_volume = Column(Float)  # Volume from known smart money addresses
    
    token = relationship("MemeCoin", back_populates="trading_volumes")
    
    __table_args__ = (
        {'comment': 'Detailed trading volume and participant metrics'}
    )

class TokenMetadata(Base):
    __tablename__ = 'token_metadata'
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'), unique=True, nullable=False)
    website = Column(String(255))
    telegram = Column(String(255))
    twitter = Column(String(255))
    discord = Column(String(255))
    description = Column(Text)
    tags = Column(JSONB)  # Array of descriptive tags
    custom_metadata = Column(JSONB)  # Any additional metadata
    
    token = relationship("MemeCoin", back_populates="token_metadata")  # Changed back_populates to match new name
    
    __table_args__ = (
        {'comment': 'Additional token metadata and social information'}
    )