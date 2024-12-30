import base64
import os
from functools import lru_cache
from typing import Any, Dict

from dotenv import load_dotenv
from pydantic import BaseModel

# Load environment variables
load_dotenv()

class Settings(BaseModel):
    """Application settings loaded from environment variables"""
    
    # Program IDs and Important Addresses
    PUMP_PROGRAM: str
    PUMP_GLOBAL: str
    PUMP_EVENT_AUTHORITY: str
    PUMP_FEE: str
    TOKEN_METADATA_PROGRAM_ID: str

    # Solana System Programs
    SYSTEM_PROGRAM: str
    SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM: str
    SYSTEM_TOKEN_PROGRAM: str
    RPC_ENDPOINT: str

    # Account Discriminators
    TOKEN_DISCRIMINATOR: str
    CURVE_DISCRIMINATOR: str

    # Constants
    LAMPORTS_PER_SOL: int
    TOKEN_DECIMALS: int
    
    # Database Configuration
    DATABASE_URL: str

    # Monitoring Configuration
    CHECK_INTERVAL: int
    BATCH_SIZE: int
    SIGNAL_TIER_INTERVAL: int
    HOT_TIER_INTERVAL: int
    WARM_TIER_INTERVAL: int 
    COLD_TIER_INTERVAL: int

    # Logging Configuration
    LOG_LEVEL: str
    LOG_FORMAT: str

    # Alert Configuration
    ALERT_WEBHOOK_URL: str
    ALERT_MIN_SEVERITY: str

    # Resource Limits
    MAX_WORKERS: int
    MAX_MEMORY_USAGE: int
    CACHE_TTL: int

    # Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int

    # Backtesting Configuration
    ENABLE_BACKTESTING: bool
    BACKTEST_START_DATE: str
    BACKTEST_END_DATE: str
    BACKTEST_BATCH_SIZE: int

    @property
    def token_discriminator_bytes(self) -> bytes:
        return base64.b64decode(self.TOKEN_DISCRIMINATOR)

    @property
    def curve_discriminator_bytes(self) -> bytes:
        return base64.b64decode(self.CURVE_DISCRIMINATOR)

    @property
    def tier_intervals(self) -> Dict[str, int]:
        return {
            "signal": self.SIGNAL_TIER_INTERVAL,
            "hot": self.HOT_TIER_INTERVAL,
            "warm": self.WARM_TIER_INTERVAL,
            "cold": self.COLD_TIER_INTERVAL
        }


def get_settings():
    env_settings = {}
    for key, value in os.environ.items():
        if key in ['CHECK_INTERVAL', 'BATCH_SIZE', 'SIGNAL_TIER_INTERVAL',
                   'HOT_TIER_INTERVAL', 'WARM_TIER_INTERVAL', 'COLD_TIER_INTERVAL',
                   'MAX_WORKERS', 'MAX_MEMORY_USAGE', 'CACHE_TTL']:
            env_settings[key] = int(value)
        elif key in ['ENABLE_BACKTESTING']:
            env_settings[key] = value.lower() in ['true', '1', 'yes']
        elif key in ['BACKTEST_START_DATE', 'BACKTEST_END_DATE']:
            from datetime import datetime
            env_settings[key] = datetime.strptime(value, '%Y-%m-%d')
        else:
            env_settings[key] = value  # Keep as string by default
    return env_settings

# Create settings instance
settings = get_settings()

# Export constants for backward compatibility
PUMP_PROGRAM = settings['PUMP_PROGRAM']
PUMP_GLOBAL = settings['PUMP_GLOBAL']
PUMP_EVENT_AUTHORITY = settings['PUMP_EVENT_AUTHORITY']
PUMP_FEE = settings['PUMP_FEE']
TOKEN_METADATA_PROGRAM_ID = settings['TOKEN_METADATA_PROGRAM_ID']
SYSTEM_PROGRAM = settings['SYSTEM_PROGRAM']
SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM = settings['SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM']
SYSTEM_TOKEN_PROGRAM = settings['SYSTEM_TOKEN_PROGRAM']
RPC_ENDPOINT = settings['RPC_ENDPOINT']
TOKEN_DISCRIMINATOR = settings['TOKEN_DISCRIMINATOR']
CURVE_DISCRIMINATOR = settings['CURVE_DISCRIMINATOR']
LAMPORTS_PER_SOL = settings['LAMPORTS_PER_SOL']
TOKEN_DECIMALS = settings['TOKEN_DECIMALS'] 