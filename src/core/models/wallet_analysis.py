from datetime import datetime
from typing import Dict, List

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from src.core.models.base import Base


class WalletAnalysis(Base):
    __tablename__ = "wallet_analysis"
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'))
    analysis_timestamp = Column(DateTime, default=datetime.utcnow)
    risk_score = Column(Float)
    early_entries = Column(JSON)  # Store list of early entry wallets
    wallet_clusters = Column(JSON)  # Store wallet cluster data
    coordinated_actions = Column(JSON)  # Store coordinated trading data
    high_risk_wallets = Column(JSON)  # Store high risk wallet data
    
    # Relationships
    token = relationship("MemeCoin", back_populates="wallet_analyses")

    __table_args__ = (
        {'comment': 'Wallet analysis and risk assessment data'}
    )


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('meme_coins.id'), nullable=False)
    token_address = Column(String(255), index=True, nullable=False)  # Added this field
    wallet_address = Column(String(255), index=True, nullable=False)
    transaction_type = Column(String(50), index=True, nullable=False)
    amount = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    mcap_at_time = Column(Float)
    
    # Add relationship back to MemeCoin
    meme_coin = relationship("MemeCoin", back_populates="transactions")

    __table_args__ = (
        Index('idx_wallet_token_time', 'token_id', 'timestamp'),
        {'comment': 'Wallet transaction history for tokens'}
    )

    def __repr__(self):
        return f"<WalletTransaction(id={self.id}, token={self.token_address}, wallet={self.wallet_address})>"