from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from src.core.models import MemeCoin
from src.config.database import SessionLocal
from src.config.settings import MonitoringTier
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class CoinService:
    def __init__(self):
        self.db = SessionLocal()

    def __del__(self):
        self.db.close()

    def save_new_coin(self, coin_data: dict) -> MemeCoin:
        """
        Save a new coin to the database
        """
        try:
            # Create new coin instance
            coin = MemeCoin(
                address=coin_data['address'],
                name=coin_data.get('name', 'Unknown'),
                symbol=coin_data.get('symbol', 'UNKNOWN'),
                tier=MonitoringTier.HOT,  # New coins start in HOT tier
                launch_date=datetime.utcnow(),
                total_supply=coin_data.get('total_supply', 0),
                price=coin_data.get('price', 0),
                market_cap=coin_data.get('market_cap', 0),
                liquidity=coin_data.get('liquidity', 0),
                monitoring_frequency=300,  # 5 minutes default for HOT tier
                next_check_time=datetime.utcnow() + timedelta(minutes=5)
            )

            self.db.add(coin)
            self.db.commit()
            self.db.refresh(coin)
            logger.info(f"Successfully saved new coin: {coin.symbol} ({coin.address})")
            return coin

        except IntegrityError:
            self.db.rollback()
            logger.warning(f"Coin with address {coin_data['address']} already exists")
            return self.update_coin(coin_data['address'], coin_data)
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving coin: {str(e)}")
            raise

    def update_coin(self, address: str, update_data: dict) -> MemeCoin:
        """
        Update existing coin information
        """
        try:
            coin = self.db.query(MemeCoin).filter(MemeCoin.address == address).first()
            if not coin:
                logger.warning(f"Coin with address {address} not found")
                return None

            # Update fields that are present in update_data
            for key, value in update_data.items():
                if hasattr(coin, key):
                    setattr(coin, key, value)

            coin.last_updated = datetime.utcnow()
            self.db.commit()
            self.db.refresh(coin)
            logger.info(f"Successfully updated coin: {coin.symbol} ({coin.address})")
            return coin

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating coin: {str(e)}")
            raise

    def get_coins_by_tier(self, tier: MonitoringTier) -> list:
        """
        Get all coins in a specific monitoring tier
        """
        return self.db.query(MemeCoin).filter(MemeCoin.tier == tier).all()

    def get_coins_for_monitoring(self) -> list:
        """
        Get coins that need to be checked based on their next_check_time
        """
        return self.db.query(MemeCoin).filter(
            MemeCoin.next_check_time <= datetime.utcnow()
        ).all()

    def update_tier(self, address: str, new_tier: MonitoringTier) -> MemeCoin:
        """
        Update a coin's monitoring tier
        """
        try:
            coin = self.db.query(MemeCoin).filter(MemeCoin.address == address).first()
            if not coin:
                return None

            coin.tier = new_tier
            # Update monitoring frequency based on tier
            frequencies = {
                MonitoringTier.SIGNAL: 15,    # 15 seconds
                MonitoringTier.HOT: 300,      # 5 minutes
                MonitoringTier.WARM: 1800,    # 30 minutes
                MonitoringTier.COLD: 21600,   # 6 hours
                MonitoringTier.ARCHIVE: 86400 # 24 hours
            }
            coin.monitoring_frequency = frequencies.get(new_tier, 300)
            coin.next_check_time = datetime.utcnow() + timedelta(seconds=coin.monitoring_frequency)

            self.db.commit()
            self.db.refresh(coin)
            return coin

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating tier: {str(e)}")
            raise

# Example usage:
# service = CoinService()
# 
# # Save new coin
# new_coin_data = {
#     'address': '0x123...',
#     'name': 'New Meme Coin',
#     'symbol': 'MEME',
#     'total_supply': 1000000
# }
# coin = service.save_new_coin(new_coin_data)
#
# # Update coin
# update_data = {
#     'price': 0.01,
#     'market_cap': 10000
# }
# updated_coin = service.update_coin(coin.address, update_data)