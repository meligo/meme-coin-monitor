from datetime import datetime

from sqlalchemy import (
    JSON,
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
from src.core.models.tier_level import TierLevel


class TokenTier(Base):
    __tablename__ = 'token_tiers'
    
    # Core Information
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'), nullable=False)
    token_address = Column(String(255), unique=True, nullable=False, index=True)  # Increased to 64 for Solana addresses
    current_tier = Column(Enum(TierLevel), nullable=False, index=True)
    tier_updated_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    last_checked = Column(DateTime, nullable=False, default=func.now(), index=True)
    check_frequency = Column(Integer, nullable=False)  # in seconds
    next_check_at = Column(DateTime, nullable=False, index=True)
    
    # Tier-specific Metrics
    tier_score = Column(Float)  # Composite score for tier assessment
    tier_metrics = Column(JSONB)  # Dynamic metrics based on tier
    monitoring_config = Column(JSONB)  # Tier-specific monitoring parameters
    
    # Signal Tier Metrics
    whale_holdings = Column(Float)
    smart_money_flow = Column(Float)
    liquidity_level = Column(Float)
    holder_count = Column(Integer)
    holder_growth_rate = Column(Float)
    volume_growth_rate = Column(Float)
    
    # Historical Tier Data
    previous_tier = Column(Enum(TierLevel))
    tier_history = Column(JSONB)  # Historical tier transitions
    signal_history = Column(JSONB)  # Historical signal detections
    
    # Resource Allocation
    processing_priority = Column(Integer)  # 1-100, higher = more priority
    memory_allocation = Column(Float)  # Percentage of tier memory quota
    
    # Monitoring Status
    is_active = Column(Boolean, default=True)
    is_monitoring_paused = Column(Boolean, default=False)
    last_alert_at = Column(DateTime)
    alert_count = Column(Integer, default=0)
    
    # Relationships
    token = relationship("MemeCoin", back_populates="tier")
    tier_transitions = relationship("TierTransition", back_populates="token_tier")
    tier_alerts = relationship("TierAlert", back_populates="token_tier")
    
    __table_args__ = (
        {'comment': 'Token tier classification and monitoring metrics'}
    )

class TierTransition(Base):
    __tablename__ = 'tier_transitions'
    
    id = Column(Integer, primary_key=True)
    token_tier_id = Column(Integer, ForeignKey('token_tiers.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=func.now())
    from_tier = Column(Enum(TierLevel), nullable=False)
    to_tier = Column(Enum(TierLevel), nullable=False)
    reason = Column(Text)
    metrics_snapshot = Column(JSONB)  # Metrics at transition time
    
    token_tier = relationship("TokenTier", back_populates="tier_transitions")
    
    __table_args__ = (
        {'comment': 'Historical record of tier transitions'}
    )

class TierAlert(Base):
    __tablename__ = 'tier_alerts'
    
    id = Column(Integer, primary_key=True)
    token_tier_id = Column(Integer, ForeignKey('token_tiers.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=func.now())
    alert_type = Column(String(50), nullable=False)  # tier_change, signal_detection, etc.
    severity = Column(String(20), nullable=False)  # info, warning, critical
    description = Column(Text)
    metrics = Column(JSONB)  # Relevant metrics at alert time
    
    token_tier = relationship("TokenTier", back_populates="tier_alerts")
    
    __table_args__ = (
        {'comment': 'Tier-specific monitoring alerts'}
    )