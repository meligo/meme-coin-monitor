import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.core.models.meme_coin import MemeCoin
from src.core.models.tier_level import TierLevel
from src.core.models.tier_models import TierAlert, TokenTier

logger = logging.getLogger(__name__)

class TierMonitor:
    def __init__(self):
        self.thresholds = {
            TierLevel.SIGNAL: {
                'whale_movement': 0.05,    # 5% of supply moved
                'liquidity_change': 0.20,  # 20% liquidity change
                'price_change': 0.30,      # 30% price change
                'volume_spike': 3.0        # 3x average volume
            },
            TierLevel.HOT: {
                'min_liquidity': 1000,     # Minimum USD liquidity
                'max_concentration': 0.80,  # Maximum holder concentration
                'min_holders': 50,         # Minimum unique holders
                'suspicious_trade_ratio': 0.20  # Max ratio of suspicious trades
            },
            TierLevel.WARM: {
                'liquidity_stability': 0.15,  # Max 15% liquidity variation
                'holder_retention': 0.70,     # Minimum 70% holder retention
                'volume_consistency': 0.50    # Max 50% volume variation
            },
            TierLevel.COLD: {
                'activity_threshold': 10,     # Minimum daily transactions
                'liquidity_maintenance': 0.30  # Max 30% liquidity drop
            }
        }

    async def monitor_token(self, db: Session, token: MemeCoin) -> Optional[Dict[str, Any]]:
        """Main monitoring function for any token"""
        if not token.tier:
            logger.warning(f"No tier information for token {token.address}")
            return None

        try:
            monitoring_result = None
            tier = token.tier.current_tier

            if tier == TierLevel.SIGNAL:
                monitoring_result = await self.monitor_signal_tier(db, token)
            elif tier == TierLevel.HOT:
                monitoring_result = await self.monitor_hot_tier(db, token)
            elif tier == TierLevel.WARM:
                monitoring_result = await self.monitor_warm_tier(db, token)
            elif tier == TierLevel.COLD:
                monitoring_result = await self.monitor_cold_tier(db, token)

            if monitoring_result and monitoring_result.get('alert_needed'):
                await self.create_alert(db, token, monitoring_result)

            return monitoring_result

        except Exception as e:
            logger.error(f"Error monitoring token {token.address}: {e}")
            return None

        async def monitor_signal_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
            """
            Monitor tokens in Signal tier for significant changes
            
            Args:
                token_data (Dict[str, Any]): Token metrics including whale_holdings, smart_money_flow etc.
                
            Returns:
                Dict[str, Any]: Monitoring results
            """
            try:
                results = {
                    'changes_detected': False,
                    'alerts': []
                }
                
                # Check whale holdings
                if token_data.get('whale_holdings', 0) > self.signal_thresholds['whale_holdings']:
                    results['changes_detected'] = True
                    results['alerts'].append({
                        'type': 'whale_activity',
                        'severity': 'high',
                        'details': f"Whale holdings above threshold: {token_data['whale_holdings']:.2%}"
                    })
                    
                # Check smart money flow
                if token_data.get('smart_money_flow', 0) > self.signal_thresholds['smart_money_flow']:
                    results['changes_detected'] = True
                    results['alerts'].append({
                        'type': 'smart_money',
                        'severity': 'high',
                        'details': f"Smart money flow detected: {token_data['smart_money_flow']:.2%}"
                    })
                    
                return results
                
            except Exception as e:
                logger.error(f"Error monitoring signal tier: {e}")
                return {'error': str(e)}


    async def monitor_hot_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Monitor tokens in Hot tier for significant changes
        
        Args:
            token_data (Dict[str, Any]): Token metrics including liquidity, market_cap etc.
            
        Returns:
            Dict[str, Any]: Monitoring results
        """
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Check liquidity changes
            if token_data.get('liquidity', 0) > 0:
                threshold = self.tier_thresholds[TierLevel.HOT]['liquidity_drop']
                if token_data.get('liquidity_change_percent', 0) > threshold:
                    results['changes_detected'] = True
                    results['alerts'].append({
                        'type': 'liquidity_alert',
                        'severity': 'medium',
                        'details': f"Significant liquidity change: {token_data['liquidity_change_percent']:.2%}"
                    })
                    
            # Check market cap changes
            if token_data.get('market_cap_usd', 0) > 0:
                # Alert on significant market cap changes (>20%)
                if token_data.get('market_cap_change_percent', 0) > 20:
                    results['changes_detected'] = True
                    results['alerts'].append({
                        'type': 'market_cap_alert',
                        'severity': 'medium',
                        'details': f"Significant market cap change: {token_data['market_cap_change_percent']:.2%}"
                    })
                    
            return results
            
        except Exception as e:
            logger.error(f"Error monitoring hot tier: {e}")
            return {'error': str(e)}

    async def monitor_warm_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Monitor tokens in Warm tier for significant changes
        
        Args:
            token_data (Dict[str, Any]): Token metrics including volume, holder data etc.
            
        Returns:
            Dict[str, Any]: Monitoring results
        """
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Check volume drops
            if token_data.get('volume_24h', 0) > 0:
                volume_drop = self.tier_thresholds[TierLevel.WARM]['volume_drop']
                if token_data.get('volume_change_percent', 0) < -volume_drop:
                    results['changes_detected'] = True
                    results['alerts'].append({
                        'type': 'volume_alert',
                        'severity': 'low',
                        'details': f"Volume decreased: {token_data['volume_change_percent']:.2%}"
                    })
            
            # Check holder changes
            if token_data.get('holder_count', 0) > 0:
                holder_loss = self.tier_thresholds[TierLevel.WARM]['holder_loss']
                if token_data.get('holder_change_percent', 0) < -holder_loss:
                    results['changes_detected'] = True
                    results['alerts'].append({
                        'type': 'holder_alert',
                        'severity': 'low',
                        'details': f"Holder count decreased: {token_data['holder_change_percent']:.2%}"
                    })
                    
            return results
            
        except Exception as e:
            logger.error(f"Error monitoring warm tier: {e}")
            return {'error': str(e)}

    async def monitor_cold_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Monitor tokens in Cold tier for potential revival signals
        
        Args:
            token_data (Dict[str, Any]): Token metrics including volume, activity etc.
            
        Returns:
            Dict[str, Any]: Monitoring results
        """
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Check for major positive changes
            major_change_threshold = self.tier_thresholds[TierLevel.COLD]['major_changes']
            
            # Check volume spikes
            if token_data.get('volume_change_percent', 0) > major_change_threshold:
                results['changes_detected'] = True
                results['alerts'].append({
                    'type': 'volume_spike',
                    'severity': 'info',
                    'details': f"Significant volume increase: {token_data['volume_change_percent']:.2%}"
                })
                
            # Check holder growth
            if token_data.get('holder_growth_rate', 0) > (major_change_threshold / 2):  # Use half threshold for holders
                results['changes_detected'] = True
                results['alerts'].append({
                    'type': 'holder_growth',
                    'severity': 'info',
                    'details': f"Holder growth detected: {token_data['holder_growth_rate']:.2%}"
                })
                
            # Check social sentiment if available
            if token_data.get('social_sentiment_score', 0) > 0.7:  # High positive sentiment
                results['changes_detected'] = True
                results['alerts'].append({
                    'type': 'sentiment_alert',
                    'severity': 'info',
                    'details': "Increased social media activity detected"
                })
                    
            return results
            
        except Exception as e:
            logger.error(f"Error monitoring cold tier: {e}")
            return {'error': str(e)}


    async def _get_historical_metrics(self, db: Session, token: MemeCoin) -> Dict[str, Any]:
        """
        Retrieves historical metrics for comparison
        Returns metrics for the last 30 days
        """
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        
        # Get price history
        price_history = [p.price_usd for p in token.price_history[-30:]]
        avg_price = sum(price_history) / len(price_history) if price_history else 0

        # Get volume history
        volume_history = [v.volume_usd for v in token.trading_volumes[-30:]]
        avg_volume = sum(volume_history) / len(volume_history) if volume_history else 0

        # Get holder history
        holder_history = [h.holder_count for h in token.holder_snapshots[-30:]]
        avg_holders = sum(holder_history) / len(holder_history) if holder_history else 0

        # Get liquidity history
        liquidity_history = [p.liquidity_usd for p in token.price_history[-30:]]
        initial_liquidity = token.price_history[0].liquidity_usd if token.price_history else 0
        avg_liquidity = sum(liquidity_history) / len(liquidity_history) if liquidity_history else 0

        return {
            'start_date': thirty_days_ago,
            'price_history': price_history,
            'avg_price': avg_price,
            'volume_history': volume_history,
            'avg_volume': avg_volume,
            'holder_history': holder_history,
            'avg_holders': avg_holders,
            'liquidity_history': liquidity_history,
            'avg_liquidity': avg_liquidity,
            'initial_liquidity': initial_liquidity,
            'metrics_samples': len(price_history)
        }

    def _calculate_change(self, current: float, previous: float) -> float:
        """Calculates percentage change between two values"""
        if not previous:
            return 0
        return (current - previous) / previous

    def _calculate_variation(self, history: List[float]) -> float:
        """Calculate variation coefficient (standard deviation / mean)"""
        if not history:
            return 0
        mean = sum(history) / len(history)
        if mean == 0:
            return 0
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        return (variance ** 0.5) / mean

    def _calculate_whale_movement(self, token: MemeCoin) -> float:
        """Calculate whale movement metric"""
        if not token.holder_distribution:
            return 0
        
        whale_threshold = 0.01  # 1% of supply
        total_supply = token.total_supply or 0
        
        whale_movements = sum(
            balance for holder, balance in token.holder_distribution.items()
            if balance > total_supply * whale_threshold
        )
        
        return whale_movements / total_supply if total_supply > 0 else 0

    def _calculate_holder_concentration(self, token: MemeCoin) -> float:
        """Calculate holder concentration (Gini coefficient)"""
        if not token.holder_distribution:
            return 0
            
        values = sorted(token.holder_distribution.values())
        if not values:
            return 0
            
        n = len(values)
        index = n + 1
        return sum(
            (index - i) / (index * n) * value
            for i, value in enumerate(values)
        )

    def _get_daily_transactions(self, token: MemeCoin) -> int:
        """Gets the number of transactions in the last 24 hours"""
        if not token.trading_volumes:
            return 0
            
        latest_volume = token.trading_volumes[-1] if token.trading_volumes else None
        return latest_volume.trade_count if latest_volume else 0

    async def create_alert(self, db: Session, token: MemeCoin, monitoring_result: Dict[str, Any]) -> None:
        """Create an alert based on monitoring results"""
        try:
            alert = TierAlert(
                token_tier_id=token.tier.id,
                alert_type=monitoring_result['alert_type'],
                severity='high' if token.tier.current_tier == TierLevel.SIGNAL else 'medium',
                description=f"Alert triggered for {token.address}: {monitoring_result['alert_type']}",
                metrics=monitoring_result['analysis']
            )
            db.add(alert)
            await db.commit()
            
        except Exception as e:
            logger.error(f"Error creating alert: {e}")
            db.rollback()