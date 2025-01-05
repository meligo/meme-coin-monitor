from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.core.models.base import BaseModel


class MemeCoin(BaseModel):
    """Main table storing meme coin information and metrics"""
    
    # Core Information
    address = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255))
    symbol = Column(String(255))
    decimals = Column(Integer)
    total_supply = Column(BigInteger)
    
    # Contract Details
    creator_address = Column(String(255))
    creation_tx_hash = Column(Text)
    contract_verified = Column(Boolean, default=False)
    contract_code = Column(Text)
    contract_analysis = Column(JSONB)
    is_active = Column(Boolean, default=True, nullable=False)
    monitoring_status = Column(String(50), default='active')
    # Supply Information
    circulating_supply = Column(BigInteger)
    burned_supply = Column(BigInteger)
    
    # Bonding Curve Information
    bonding_curve_address = Column(String(255))
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
    liquidity_pairs = Column(JSONB)
    
    # Holder Information
    holder_count = Column(Integer)
    holder_distribution = Column(JSONB)
    top_holders = Column(JSONB)
    
    # Risk Metrics
    risk_score = Column(Float)
    risk_factors = Column(JSONB)
    
    # Monitoring Metrics
    whale_holdings = Column(Float)
    smart_money_flow = Column(Float)
    holder_growth_rate = Column(Float)
    
    # Launch Information
    launch_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    tier = relationship(
        "TokenTier",
        back_populates="token",
        uselist=False
    )
    price_history = relationship(
        "TokenPrice",
        back_populates="token",
        order_by="desc(TokenPrice.timestamp)"
    )
    holder_snapshots = relationship(
        "HolderSnapshot",
        back_populates="token",
        order_by="desc(HolderSnapshot.timestamp)"
    )
    trading_volumes = relationship(
        "TradingVolume",
        back_populates="token",
        order_by="desc(TradingVolume.timestamp)"
    )
    token_metadata = relationship(
        "TokenMetadata",
        back_populates="token",
        uselist=False
    )
    wallet_analyses = relationship(
        "WalletAnalysis",
        back_populates="token",
        order_by="desc(WalletAnalysis.analysis_timestamp)"
    )
    transactions = relationship(
        "WalletTransaction",
        back_populates="meme_coin",
        order_by="desc(WalletTransaction.timestamp)"
    )

    def __repr__(self):
        return f"<MemeCoin(address={self.address}, symbol={self.symbol})>"


class TokenPrice(BaseModel):
    """Historical price and market data for tokens"""
    
    token_id = Column(Integer, ForeignKey('meme_coin.id'), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    price_usd = Column(Float, nullable=False)
    volume_usd = Column(Float)
    market_cap_usd = Column(Float)
    liquidity_usd = Column(Float)
    
    # Relationship
    token = relationship("MemeCoin", back_populates="price_history")
    
    __table_args__ = (
        UniqueConstraint('token_id', 'timestamp', name='uq_token_price_token_timestamp'),
    )

    def __repr__(self):
        return f"<TokenPrice(token_id={self.token_id}, price=${self.price_usd:.4f})>"


class HolderSnapshot(BaseModel):
    """Historical holder data and distribution metrics"""
    
    token_id = Column(Integer, ForeignKey('meme_coin.id'), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    holder_count = Column(Integer)
    holder_distribution = Column(JSONB)
    concentration_metrics = Column(JSONB)
    
    # Relationship
    token = relationship("MemeCoin", back_populates="holder_snapshots")
    
    __table_args__ = (
        UniqueConstraint('token_id', 'timestamp', name='uq_holder_snapshot_token_timestamp'),
    )

    def __repr__(self):
        return f"<HolderSnapshot(token_id={self.token_id}, holders={self.holder_count})>"


class TradingVolume(BaseModel):
    """Detailed trading volume and participant metrics"""
    
    token_id = Column(Integer, ForeignKey('meme_coin.id'), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    volume_usd = Column(Float)
    trade_count = Column(Integer)
    buyer_count = Column(Integer)
    seller_count = Column(Integer)
    whale_volume = Column(Float)
    smart_money_volume = Column(Float)
    
    # Relationship
    token = relationship("MemeCoin", back_populates="trading_volumes")
    
    __table_args__ = (
        UniqueConstraint('token_id', 'timestamp', name='uq_trading_volume_token_timestamp'),
    )

    def __repr__(self):
        return f"<TradingVolume(token_id={self.token_id}, volume=${self.volume_usd:.2f})>"


class TokenMetadata(BaseModel):
    """Additional token metadata and social information"""
    
    token_id = Column(Integer, ForeignKey('meme_coin.id'), unique=True, nullable=False)
    website = Column(String(255))
    telegram = Column(String(255))
    twitter = Column(String(255))
    discord = Column(String(255))
    description = Column(Text)
    tags = Column(JSONB)
    custom_metadata = Column(JSONB)
    
    # Relationship
    token = relationship("MemeCoin", back_populates="token_metadata")

    def __repr__(self):
        return f"<TokenMetadata(token_id={self.token_id})>"