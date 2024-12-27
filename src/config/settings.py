from enum import Enum

class MonitoringTier(Enum):
    SIGNAL = 'signal'
    HOT = 'hot'
    WARM = 'warm'
    COLD = 'cold'
    ARCHIVE = 'archive'

class Settings:
    # Database settings
    POSTGRES_HOST = 'pga.bittics.com'
    POSTGRES_DB = 'datahub'
    POSTGRES_USER = 'SA_PSQL_Engine'
    POSTGRES_PASSWORD = 'Lmmoyjwt1'
    POSTGRES_PORT = '5000'
    
    # Redis settings
    REDIS_HOST = '192.168.1.175'
    REDIS_PORT = 6379
    
    # Monitoring intervals (in seconds)
    TIER_INTERVALS = {
        MonitoringTier.SIGNAL: 15,  # 15 seconds
        MonitoringTier.HOT: 60,     # 1 minute
        MonitoringTier.WARM: 1800,   # 30 minutes
        MonitoringTier.COLD: 21600,  # 6 hours
        MonitoringTier.ARCHIVE: None # No active monitoring
    }
    
    # Worker resource allocation
    WORKER_RATIOS = {
        MonitoringTier.SIGNAL: 0.30,
        MonitoringTier.HOT: 0.40,
        MonitoringTier.WARM: 0.20,
        MonitoringTier.COLD: 0.10,
        MonitoringTier.ARCHIVE: 0.00
    }

settings = Settings()