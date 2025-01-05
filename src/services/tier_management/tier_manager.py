import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from src.config.database import get_db_session
from src.core.models.tier_level import TierLevel
from src.services.tier_management.utils.metrics import calculate_token_metrics

logger = logging.getLogger(__name__)

class TierManager:
    def __init__(self):
        self._init_check_frequencies()
        self._init_resource_allocations()
        self._init_thresholds()

    def _init_check_frequencies(self):
        """Initialize check frequencies for each tier in seconds"""
        self.check_frequencies = {
            TierLevel.SIGNAL: 15,     # 15 seconds
            TierLevel.HOT: 60,        # 1 minute
            TierLevel.WARM: 1800,     # 30 minutes
            TierLevel.COLD: 21600,    # 6 hours
            TierLevel.ARCHIVE: 0,      # No active monitoring
            TierLevel.RUGGED: 0       # No active monitoring
        }

    def _init_resource_allocations(self):
        """Initialize resource allocation percentages for each tier"""
        self.resource_allocation = {
            TierLevel.SIGNAL: 0.30,   # 30%
            TierLevel.HOT: 0.40,      # 40%
            TierLevel.WARM: 0.20,     # 20%
            TierLevel.COLD: 0.10,      # 10%
            TierLevel.RUGGED: 0.0     # No resources allocated

        }

    def _init_thresholds(self):
        """Initialize various thresholds for tier management"""
        self.signal_thresholds = {
            'whale_holdings': 0.05,    # 5% of total supply
            'smart_money_flow': 0.10,  # 10% positive flow
            'liquidity_growth': 0.20,  # 20% growth
            'holder_growth': 0.15      # 15% growth rate
        }

        self.tier_thresholds = {
            TierLevel.HOT: {
                'age_limit': int(timedelta(days=7).total_seconds()),
                'liquidity_drop': 0.30,
                'holder_concentration': 0.80
            },
            TierLevel.WARM: {
                'age_limit': int(timedelta(days=30).total_seconds()),
                'volume_drop': 0.50,
                'holder_loss': 0.30
            },
            TierLevel.COLD: {
                'major_changes': 0.70
            }
        }

        self.archive_thresholds = {
            'inactivity_period': int(timedelta(days=7).total_seconds()),
            'min_liquidity': 100,      # Minimum USD liquidity
            'min_volume_24h': 1000,    # Minimum 24h volume
            'max_concentration': 0.90   # Maximum holder concentration
        }
        
        self.rug_thresholds = {
            'liquidity_drop': 0.7,     # 70% liquidity drop
            'volume_spike': 5.0,       # 5x volume spike
            'holder_drop': 0.3,        # 30% holder decrease
            'smart_money_outflow': 0.4 # 40% smart money leaving
        }

    async def assign_initial_tier(self, token_data: Dict[str, Any]) -> TierLevel:
        try:
            # Convert age to seconds if it's a timedelta, or use 0 if it's an integer
            token_age = token_data.get('age', 0)
            if isinstance(token_age, timedelta):
                age_seconds = int(token_age.total_seconds())
            else:
                age_seconds = int(token_age)
            
            if await self.check_for_signals(token_data):
                return TierLevel.SIGNAL
                
            # Age-based assignment using seconds
            if age_seconds < self.tier_thresholds[TierLevel.HOT]['age_limit']:
                return TierLevel.HOT
            elif age_seconds < self.tier_thresholds[TierLevel.WARM]['age_limit']:
                return TierLevel.WARM
            else:
                return TierLevel.COLD
                
        except Exception as e:
            logger.error(f"Error assigning initial tier: {e}")
            return TierLevel.HOT  # Default to HOT tier on error

    async def check_for_signals(self, token_data: Dict[str, Any]) -> bool:
        """
        Check if token shows strong signals warranting Signal tier
        
        Args:
            token_data (Dict[str, Any]): Token metrics and information
            
        Returns:
            bool: True if strong signals detected, False otherwise
        """
        try:
            metrics = calculate_token_metrics(token_data)
            
            signals = {
                'whale_accumulation': metrics['whale_holdings'] > self.signal_thresholds['whale_holdings'],
                'smart_money_inflow': metrics['smart_money_flow'] > self.signal_thresholds['smart_money_flow'],
                'liquidity_spike': metrics['liquidity_growth'] > self.signal_thresholds['liquidity_growth'],
                'holder_acceleration': metrics['holder_growth'] > self.signal_thresholds['holder_growth']
            }
            
            if any(signals.values()):
                logger.info(f"Strong signals detected for token: {signals}")
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Error checking signals: {e}")
            return False

    async def should_archive(self, token_data: Dict[str, Any]) -> bool:
        """
        Determine if token should be moved to archive tier
        
        Args:
            token_data (Dict[str, Any]): Token metrics and information
            
        Returns:
            bool: True if token should be archived, False otherwise
        """
        try:
            last_activity = token_data.get('last_activity_at', datetime.min)
            inactive_period = datetime.utcnow() - last_activity
            
            conditions = {
                'inactive': inactive_period > self.archive_thresholds['inactivity_period'],
                'no_liquidity': token_data.get('liquidity', 0) < self.archive_thresholds['min_liquidity'],
                'no_volume': token_data.get('volume_24h', 0) < self.archive_thresholds['min_volume_24h'],
                'high_concentration': token_data.get('top_holder_percentage', 0) > self.archive_thresholds['max_concentration']
            }
            
            should_archive = conditions['inactive'] and (
                conditions['no_liquidity'] or 
                conditions['no_volume'] or 
                conditions['high_concentration']
            )
            
            if should_archive:
                logger.info(f"Token meets archive conditions: {conditions}")
                
            return should_archive
            
        except Exception as e:
            logger.error(f"Error checking archive conditions: {e}")
            return False


    async def check_for_rug(self, token_data: Dict[str, Any]) -> bool:
        """Check if token shows signs of being rugged"""
        try:
            # Check liquidity drop
            liquidity_change = token_data.get('liquidity_change_percent', 0)
            if abs(liquidity_change) >= self.rug_thresholds['liquidity_drop']:
                logger.warning(f"Severe liquidity drop detected: {liquidity_change:.2f}%")
                return True
                
            # Check volume spike
            volume_change = token_data.get('volume_change_percent', 0)
            if volume_change >= self.rug_thresholds['volume_spike']:
                logger.warning(f"Suspicious volume spike detected: {volume_change:.2f}x")
                return True
                
            # Check holder decrease
            holder_change = token_data.get('holder_change_percent', 0)
            if abs(holder_change) >= self.rug_thresholds['holder_drop']:
                logger.warning(f"Rapid holder decrease detected: {holder_change:.2f}%")
                return True
                
            # Check smart money outflow
            smart_money_flow = token_data.get('smart_money_flow', 0)
            if smart_money_flow <= -self.rug_thresholds['smart_money_outflow']:
                logger.warning(f"Smart money outflow detected: {smart_money_flow:.2f}")
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Error checking for rug: {e}")
            return False

    async def update_tier(self, current_tier: TierLevel, token_data: Dict[str, Any]) -> TierLevel:
        """
        Update token tier based on current metrics and conditions
        
        Args:
            current_tier (TierLevel): Current tier of the token
            token_data (Dict[str, Any]): Token metrics and information
            
        Returns:
            TierLevel: Updated tier for the token
        """
        try:
            # Check for rug pull first
            if await self.check_for_rug(token_data):
                return TierLevel.RUGGED
            # Skip other checks if already rugged
            if current_tier == TierLevel.RUGGED:
                return TierLevel.RUGGED
            # Check for archive conditions first
            if await self.should_archive(token_data):
                return TierLevel.ARCHIVE
                
            # Check for strong signals
            if await self.check_for_signals(token_data):
                return TierLevel.SIGNAL
                
            # Check if signals have faded for Signal tier
            if current_tier == TierLevel.SIGNAL and not await self.check_for_signals(token_data):
                return await self.assign_initial_tier(token_data)
            
            # Regular age-based progression
            token_age = token_data.get('age', timedelta(days=0))
            
            if current_tier == TierLevel.HOT and token_age > self.tier_thresholds[TierLevel.HOT]['age_limit']:
                return TierLevel.WARM
            elif current_tier == TierLevel.WARM and token_age > self.tier_thresholds[TierLevel.WARM]['age_limit']:
                return TierLevel.COLD
                
            return current_tier
            
        except Exception as e:
            logger.error(f"Error updating tier: {e}")
            return current_tier

    def get_monitoring_config(self, tier: TierLevel) -> Dict[str, Any]:
        """
        Get monitoring configuration for a specific tier
        
        Args:
            tier (TierLevel): Tier to get configuration for
            
        Returns:
            Dict[str, Any]: Monitoring configuration for the tier
        """
        priorities = {
            TierLevel.SIGNAL: 'highest',
            TierLevel.HOT: 'high',
            TierLevel.WARM: 'medium',
            TierLevel.COLD: 'low',
            TierLevel.ARCHIVE: 'none',
            TierLevel.RUGGED: 'none'
        }

        base_config = {
            'check_frequency': self.check_frequencies[tier],
            'resource_allocation': self.resource_allocation.get(tier, 0),
            'monitoring_priority': priorities[tier],
            'alert_enabled': tier not in [TierLevel.ARCHIVE, TierLevel.RUGGED]
        }

        # Add tier-specific thresholds
        if tier == TierLevel.SIGNAL:
            base_config['alert_thresholds'] = self.signal_thresholds
            base_config['cache_history'] = True
        elif tier in self.tier_thresholds:
            base_config['alert_thresholds'] = self.tier_thresholds[tier]
        elif tier == TierLevel.RUGGED:
            base_config['alert_thresholds'] = self.rug_thresholds

        return base_config

    async def monitor_signal_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor tokens in Signal tier"""
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Whale holdings check
            if token_data.get('whale_holdings', 0) > self.signal_thresholds['whale_holdings']:
                results['changes_detected'] = True
                results['alerts'].append({
                    'type': 'whale_activity',
                    'severity': 'high',
                    'details': f"Whale holdings above threshold: {token_data['whale_holdings']:.2%}"
                })
                
            # Smart money flow check
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
        """Monitor tokens in Hot tier"""
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Liquidity change check using threshold
            liquidity_threshold = self.tier_thresholds[TierLevel.HOT]['liquidity_drop']
            current_liquidity = token_data.get('liquidity', 0)
            liquidity_change = token_data.get('liquidity_change_percent', 0)
            
            if current_liquidity > 0 and abs(liquidity_change) > liquidity_threshold:
                results['changes_detected'] = True
                results['alerts'].append({
                    'type': 'liquidity',
                    'severity': 'medium',
                    'details': f"Liquidity changed by {liquidity_change:.2f}% (threshold: {liquidity_threshold*100}%)"
                })
                
            return results
        except Exception as e:
            logger.error(f"Error monitoring hot tier: {e}")
            return {'error': str(e)}

    async def monitor_warm_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor tokens in Warm tier"""
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Volume check using threshold
            volume_threshold = self.tier_thresholds[TierLevel.WARM]['volume_drop']
            current_volume = token_data.get('volume_24h', 0)
            volume_change = token_data.get('volume_change_percent', 0)
            
            if current_volume > 0 and abs(volume_change) > volume_threshold:
                results['changes_detected'] = True
                results['alerts'].append({
                    'type': 'volume',
                    'severity': 'low',
                    'details': f"Volume changed by {volume_change:.2f}% (threshold: {volume_threshold*100}%)"
                })
                
            return results
        except Exception as e:
            logger.error(f"Error monitoring warm tier: {e}")
            return {'error': str(e)}

    async def monitor_cold_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """Monitor tokens in Cold tier"""
        try:
            results = {
                'changes_detected': False,
                'alerts': []
            }
            
            # Activity check
            if token_data.get('holder_count', 0) > 0:
                results['alerts'].append({
                    'type': 'activity',
                    'severity': 'info',
                    'details': f"Current holder count: {token_data['holder_count']}"
                })
                
            return results
        except Exception as e:
            logger.error(f"Error monitoring cold tier: {e}")
            return {'error': str(e)}