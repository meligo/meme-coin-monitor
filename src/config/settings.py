import base64
import os
from dataclasses import dataclass
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

@dataclass
class RPCEndpoint:
    url: str
    priority: int = 1
    weight: float = 1.0
    is_healthy: bool = True

    def to_dict(self) -> Dict:
        return {
            "url": self.url,
            "priority": self.priority,
            "weight": self.weight
        }

class Settings:
    def __init__(self):
        self._validate_required_env_vars()
        self._load_settings()

    def _validate_required_env_vars(self):
        """Validate that all required environment variables are present"""
        required_vars = [
            'PUMP_PROGRAM',
            'PUMP_GLOBAL',
            'PUMP_EVENT_AUTHORITY',
            'PUMP_FEE',
            'TOKEN_METADATA_PROGRAM_ID',
            'DATABASE_URL',
            'RPC_ENDPOINT'
        ]

        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

    def _load_settings(self):
        """Load and process all settings from environment variables"""
        # RPC Configuration
        self.rpc_endpoints: List[RPCEndpoint] = [
            RPCEndpoint(url=os.getenv('RPC_ENDPOINT'))
        ]
        if backup_rpc := os.getenv('BACKUP_RPC_ENDPOINT'):
            self.rpc_endpoints.append(RPCEndpoint(
                url=backup_rpc,
                priority=2,
                weight=0.5
            ))

        self.rpc_config = {
            "endpoints": [endpoint.to_dict() for endpoint in self.rpc_endpoints],
            "health_check_interval": int(os.getenv('RPC_HEALTH_CHECK_INTERVAL', '60')),
            "error_threshold": int(os.getenv('RPC_ERROR_THRESHOLD', '5')),
            "max_retries": int(os.getenv('RPC_MAX_RETRIES', '3')),
            "retry_delay": float(os.getenv('RPC_RETRY_DELAY', '1.0')),
        }

        # Program IDs and Important Addresses
        self.pump_program: str = os.getenv('PUMP_PROGRAM', '')
        self.pump_global: str = os.getenv('PUMP_GLOBAL', '')
        self.pump_event_authority: str = os.getenv('PUMP_EVENT_AUTHORITY', '')
        self.pump_fee: str = os.getenv('PUMP_FEE', '')
        self.token_metadata_program_id: str = os.getenv('TOKEN_METADATA_PROGRAM_ID', '')

        # System Programs
        self.system_program: str = os.getenv('SYSTEM_PROGRAM', '')
        self.system_associated_token_account_program: str = os.getenv('SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM', '')
        self.system_token_program: str = os.getenv('SYSTEM_TOKEN_PROGRAM', '')

        # Account Discriminators
        self.token_discriminator: Optional[str] = os.getenv('TOKEN_DISCRIMINATOR')
        self.curve_discriminator: Optional[str] = os.getenv('CURVE_DISCRIMINATOR')
        self.token_discriminator_bytes: Optional[bytes] = (
            base64.b64decode(self.token_discriminator) if self.token_discriminator else None
        )
        self.curve_discriminator_bytes: Optional[bytes] = (
            base64.b64decode(self.curve_discriminator) if self.curve_discriminator else None
        )

        # Constants
        self.lamports_per_sol: int = int(os.getenv('LAMPORTS_PER_SOL', '1000000000'))
        self.token_decimals: int = int(os.getenv('TOKEN_DECIMALS', '6'))

        # Database Configuration
        self.database_url: str = os.getenv('DATABASE_URL', '')

        # Monitoring Configuration
        self.check_interval: int = int(os.getenv('CHECK_INTERVAL', '60'))
        self.batch_size: int = int(os.getenv('BATCH_SIZE', '100'))
        self.signal_tier_interval: int = int(os.getenv('SIGNAL_TIER_INTERVAL', '30'))
        self.hot_tier_interval: int = int(os.getenv('HOT_TIER_INTERVAL', '300'))
        self.warm_tier_interval: int = int(os.getenv('WARM_TIER_INTERVAL', '1800'))
        self.cold_tier_interval: int = int(os.getenv('COLD_TIER_INTERVAL', '21600'))

        # Logging Configuration
        self.log_level: str = os.getenv('LOG_LEVEL', 'INFO')
        self.log_format: str = os.getenv('LOG_FORMAT', '%(asctime)s - %(levelname)s - [%(name)s] %(message)s')

        # Alert Configuration
        self.alert_webhook_url: Optional[str] = os.getenv('ALERT_WEBHOOK_URL')
        self.alert_min_severity: str = os.getenv('ALERT_MIN_SEVERITY', 'WARNING')

        # Resource Limits
        self.max_workers: int = int(os.getenv('MAX_WORKERS', '10'))
        self.max_memory_usage: int = int(os.getenv('MAX_MEMORY_USAGE', '4096'))
        self.cache_ttl: int = int(os.getenv('CACHE_TTL', '3600'))

        # Redis Configuration
        self.redis_host: Optional[str] = os.getenv('REDIS_HOST')
        self.redis_port: int = int(os.getenv('REDIS_PORT', '6379'))
        self.redis_db: int = int(os.getenv('REDIS_DB', '0'))

        # Backtesting Configuration
        self.enable_backtesting: bool = str(os.getenv('ENABLE_BACKTESTING', 'false')).lower() == 'true'
        self.backtest_start_date: Optional[str] = os.getenv('BACKTEST_START_DATE')
        self.backtest_end_date: Optional[str] = os.getenv('BACKTEST_END_DATE')
        self.backtest_batch_size: int = int(os.getenv('BACKTEST_BATCH_SIZE', '100'))

    def __getitem__(self, key: str) -> any:
        """Allow dictionary-style access for backward compatibility"""
        return getattr(self, key.lower())

# Create global settings instance
settings = Settings()

# For backward compatibility and type hints
PUMP_PROGRAM: str = settings.pump_program
PUMP_GLOBAL: str = settings.pump_global
PUMP_EVENT_AUTHORITY: str = settings.pump_event_authority
PUMP_FEE: str = settings.pump_fee
TOKEN_METADATA_PROGRAM_ID: str = settings.token_metadata_program_id
SYSTEM_PROGRAM: str = settings.system_program
SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM: str = settings.system_associated_token_account_program
SYSTEM_TOKEN_PROGRAM: str = settings.system_token_program
TOKEN_DISCRIMINATOR: Optional[str] = settings.token_discriminator
CURVE_DISCRIMINATOR: Optional[str] = settings.curve_discriminator
LAMPORTS_PER_SOL: int = settings.lamports_per_sol
TOKEN_DECIMALS: int = settings.token_decimals