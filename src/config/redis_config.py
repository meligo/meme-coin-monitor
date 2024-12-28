import redis
from .settings import settings

class RedisManager:
    def __init__(self):
        self.client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            decode_responses=True
        )
        
    def get_tier_key(self, address: str, tier: str) -> str:
        """Generate Redis key for a specific coin and tier"""
        prefix = settings.REDIS_KEY_PREFIXES.get(f'{tier}_tier')
        return f"{prefix}{address}"
        
    def cache_coin_data(self, address: str, tier: str, data: dict, ttl: int = None):
        """Cache coin data with appropriate TTL based on tier"""
        key = self.get_tier_key(address, tier)
        if ttl is None:
            ttl = settings.CACHE_TTLS.get(tier, 300)
        self.client.setex(key, ttl, str(data))
        
    def get_cached_coin_data(self, address: str, tier: str) -> dict:
        """Retrieve cached coin data"""
        key = self.get_tier_key(address, tier)
        data = self.client.get(key)
        return eval(data) if data else None

redis_manager = RedisManager()