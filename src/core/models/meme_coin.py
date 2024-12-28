from sqlalchemy import Column, String, Float, Integer, Enum as SQLEnum, JSON, ARRAY, DateTime, Boolean
from datetime import datetime, timedelta
from src.core.models.base import BaseModel
from src.config.settings import MonitoringTier

class MemeCoin(BaseModel):
    __tablename__ = 'meme_coins'

    # Basic token information
    id = Column(Integer, primary_key=True)
    address = Column(String, unique=True, index=True)
    name = Column(String)
    symbol = Column(String)
    tier = Column(SQLEnum(MonitoringTier))
    launch_date = Column(DateTime, default=datetime.utcnow)

    # Token metrics
    total_supply = Column(Float)
    circulating_supply = Column(Float)
    holder_count = Column(Integer)
    liquidity = Column(Float)
    market_cap = Column(Float)
    price = Column(Float)
    volume_24h = Column(Float)

    # Pump.fun specific data
    bonding_curve_address = Column(String)
    bonding_curve_state = Column(JSON)  # Stores the complete bonding curve state
    is_curve_complete = Column(Boolean, default=False)
    is_migrated_to_raydium = Column(Boolean, default=False)

    # Contract security analysis
    contract_analysis = Column(JSON, default={
        'mint_authority_renounced': False,
        'has_mint_function': False,
        'has_blacklist': False,
        'modifiable': False,
        'proxy_contract': False,
        'can_pause_trading': False,
        'can_restrict_trading': False,
        'hidden_owner_functions': [],
        'risk_factors': [],
        'security_score': 100
    })

    # Trading analysis
    trading_metrics = Column(JSON, default={
        'buy_tax': 0.0,
        'sell_tax': 0.0,
        'max_buy': None,
        'max_sell': None,
        'price_impact': 0.0,
        'volume_change_24h': 0.0,
        'price_change_24h': 0.0,
        'buy_sell_ratio': 1.0,
        'whale_transactions_1h': 0,
        'transactions_24h': 0,
        'unique_buyers_24h': 0,
        'unique_sellers_24h': 0
    })

    # Liquidity analysis
    liquidity_metrics = Column(JSON, default={
        'total_liquidity': 0.0,
        'liquidity_added_24h': 0.0,
        'liquidity_removed_24h': 0.0,
        'is_liquidity_locked': False,
        'lock_duration': None,
        'lock_end_date': None,
        'initial_liquidity': 0.0
    })

    # Holder metrics
    holder_metrics = Column(JSON, default={
        'total_holders': 0,
        'holder_distribution': {},
        'top_10_holders_percentage': 0.0,
        'average_hold_time': 0.0,
        'new_holders_24h': 0,
        'holder_growth_rate': 0.0
    })

    # Website analysis
    website_url = Column(String)
    structure_hash = Column(String)
    content_hash = Column(String)
    feature_hash = Column(String)
    lsh_bands = Column(ARRAY(String))
    website_features = Column(JSON, default={
        'token_elements': {
            'contract_display': None,
            'token_metrics': {},
            'buy_buttons': []
        },
        'marketing_patterns': {
            'tokenomics': {'present': False},
            'roadmap': {'present': False},
            'community': {'present': False},
            'features': {'present': False}
        },
        'social_presence': {
            'telegram': None,
            'twitter': None,
            'discord': None
        },
        'launch_features': {
            'presale': False,
            'launch_date': None,
            'whitelist': False
        }
    })

    # Risk assessment
    risk_score = Column(Float)
    risk_factors = Column(ARRAY(String))
    flags = Column(ARRAY(String))

    # Monitoring metadata
    last_updated = Column(DateTime, onupdate=datetime.utcnow)
    monitoring_frequency = Column(Integer)  # seconds between checks
    next_check_time = Column(DateTime)