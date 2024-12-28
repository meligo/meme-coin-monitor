import os
from enum import Enum
from solders.pubkey import Pubkey
from dotenv import load_dotenv

load_dotenv()

class MonitoringTier(Enum):
    SIGNAL = 'signal'
    HOT = 'hot'
    WARM = 'warm'
    COLD = 'cold'
    ARCHIVE = 'archive'

class Settings:
    # Database URL
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://SA_PSQL_Engine:Lmmoyjwt1@pga.bittics.com:5000/meme_coin_monitor')
    
    # Solana Node Settings
    RPC_ENDPOINT = "https://solana-mainnet.core.chainstack.com/3b0ed795772b1898efd6ff013f7b764e"
    WSS_ENDPOINT = "wss://solana-mainnet.core.chainstack.com/3b0ed795772b1898efd6ff013f7b764e"
    
    # Pump.fun Program IDs
    PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
    PUMP_GLOBAL = Pubkey.from_string("4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf")
    PUMP_EVENT_AUTHORITY = Pubkey.from_string("Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1")
    PUMP_FEE = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
    PUMP_LIQUIDITY_MIGRATOR = Pubkey.from_string("39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg")
    
    # Redis settings
    REDIS_HOST = '192.168.1.175'
    REDIS_PORT = 6379
    
    # Constants
    LAMPORTS_PER_SOL = 1_000_000_000
    TOKEN_DECIMALS = 6
    
    # Monitoring intervals (in seconds)
    TIER_INTERVALS = {
        MonitoringTier.SIGNAL: 15,  # 15 seconds
        MonitoringTier.HOT: 60,     # 1 minute
        MonitoringTier.WARM: 1800,   # 30 minutes
        MonitoringTier.COLD: 21600,  # 6 hours
        MonitoringTier.ARCHIVE: None # No active monitoring
    }
    
    # Cache settings
    REDIS_KEY_PREFIXES = {
        'meme_coin': 'mc:',
        'signal_tier': 'signal:',
        'hot_tier': 'hot:',
        'warm_tier': 'warm:',
        'cold_tier': 'cold:'
    }
    
    CACHE_TTLS = {
        MonitoringTier.SIGNAL: 30,    # 30 seconds
        MonitoringTier.HOT: 300,      # 5 minutes
        MonitoringTier.WARM: 3600,    # 1 hour
        MonitoringTier.COLD: 86400    # 24 hours
    }

settings = Settings()