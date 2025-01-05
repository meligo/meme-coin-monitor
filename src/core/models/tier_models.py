from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
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

from src.core.models.base import BaseModel


class TierLevel(str, PyEnum):
    SIGNAL = 'SIGNAL'
    HOT = 'HOT'
    WARM = 'WARM'
    COLD = 'COLD'
    RUGGED = 'RUGGED'


class TokenTier(BaseModel):
    """Token tier and monitoring information"""
    
    # Core fields
    token_id = Column(Integer, ForeignKey('meme_coin.id'), nullable=False)


    token_address = Column(String(255), unique=True, nullable=False, index=True)
    current_tier = Column(Enum(TierLevel), nullable=False, index=True)
    previous_tier = Column(Enum(TierLevel))
    
    # Monitoring settings
    check_frequency = Column(Integer, nullable=False)  # Seconds between checks
    is_active = Column(Boolean, default=True)
    is_monitoring_paused = Column(Boolean, default=False)
    processing_priority = Column(Integer, default=0)
    
    # Timestamps
    last_checked = Column(DateTime(timezone=True))
    next_check_at = Column(DateTime(timezone=True))
    tier_updated_at = Column(DateTime(timezone=True))
    
    # Metrics
    confidence_score = Column(Float)
    market_cap = Column(BigInteger)
    volume_24h = Column(BigInteger)
    holder_count = Column(Integer)
    price_change_24h = Column(Float)
    
    # Additional data
    tier_history = Column(JSONB)  # Historical tier changes
    monitoring_notes = Column(Text)  # Notes about monitoring status
    
    # Relationships
    token = relationship(
        "MemeCoin", 
        back_populates="tier",
        uselist=False
    )
    tier_transitions = relationship(
        "TierTransition",
        back_populates="token_tier",
        order_by="desc(TierTransition.transition_time)"
    )
    tier_alerts = relationship(
        "TierAlert",
        back_populates="token_tier",
        order_by="desc(TierAlert.created_at)"
    )

    def __repr__(self):
        return (
            f"<TokenTier(address={self.token_address}, "
            f"tier={self.current_tier}, active={self.is_active})>"
        )


class TierTransition(BaseModel):
    """Record of tier level changes"""
    
    token_tier_id = Column(Integer, ForeignKey('token_tier.id'), nullable=False)
    from_tier = Column(Enum(TierLevel))
    to_tier = Column(Enum(TierLevel), nullable=False)
    transition_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    confidence_score = Column(Float)
    reason = Column(Text)
    metrics_snapshot = Column(JSONB)
    
    # Relationship
    token_tier = relationship("TokenTier", back_populates="tier_transitions")

    def __repr__(self):
        return (
            f"<TierTransition(from={self.from_tier}, "
            f"to={self.to_tier}, time={self.transition_time})>"
        )


class TierAlert(BaseModel):
    """Alerts related to tier changes or monitoring"""
    
    token_tier_id = Column(Integer, ForeignKey('token_tier.id'), nullable=False)
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime(timezone=True))
    resolution_note = Column(Text)
    alert_data = Column(JSONB)
    
    # Relationship
    token_tier = relationship("TokenTier", back_populates="tier_alerts")

    def __repr__(self):
        return (
            f"<TierAlert(type={self.alert_type}, "
            f"severity={self.severity}, resolved={self.resolved})>"
        )