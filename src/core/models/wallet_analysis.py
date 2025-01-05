from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, 
    Integer, String, Index, Text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.core.models.base import BaseModel


class WalletAnalysis(BaseModel):
    """Wallet analysis and risk assessment data"""
    
    token_id = Column(Integer, ForeignKey('meme_coin.id'), nullable=False)
    analysis_timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    risk_score = Column(Float)
    early_entries = Column(JSONB)  # List of early entry wallets
    wallet_clusters = Column(JSONB)  # Wallet cluster data
    coordinated_actions = Column(JSONB)  # Coordinated trading data
    high_risk_wallets = Column(JSONB)  # High risk wallet data
    
    # Relationships
    token = relationship("MemeCoin", back_populates="wallet_analyses")

    def __repr__(self):
        return f"<WalletAnalysis(token_id={self.token_id}, risk_score={self.risk_score})>"


class WalletTransaction(BaseModel):
    """Wallet transaction history for tokens"""
    
    token_id = Column(Integer, ForeignKey('meme_coin.id'), nullable=False)
    token_address = Column(String(255), index=True, nullable=False)
    wallet_address = Column(String(255), index=True, nullable=False)
    transaction_type = Column(String(50), index=True, nullable=False)
    amount = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    mcap_at_time = Column(Float)
    
    # Relationship
    meme_coin = relationship("MemeCoin", back_populates="transactions")

    __table_args__ = (
        Index('idx_wallet_token_time', 'token_id', 'timestamp'),
    )

    def __repr__(self):
        return (
            f"<WalletTransaction(token={self.token_address}, "
            f"wallet={self.wallet_address}, type={self.transaction_type})>"
        )