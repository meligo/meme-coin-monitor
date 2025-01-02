import logging
from typing import Dict, Any
from src.services.blockchain.token_metrics import TokenMetrics

logger = logging.getLogger(__name__)

def calculate_token_metrics(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate token metrics from token data
    
    Args:
        token_data: Token data including historical and current metrics
        
    Returns:
        Dict containing calculated metrics
    """
    try:
        total_supply = token_data.get('total_supply', 0)
        if total_supply == 0:
            return _get_default_metrics()
            
        # Calculate whale holdings
        holder_distribution = token_data.get('holder_distribution', {})
        whale_threshold = total_supply * 0.05  # 5% of supply
        whale_holdings = sum(
            balance for balance in holder_distribution.values()
            if balance > whale_threshold
        ) / total_supply  # As percentage of total supply
        
        # Calculate growth rates
        current_liquidity = token_data.get('liquidity', 0)
        previous_liquidity = token_data.get('previous_liquidity', current_liquidity)
        liquidity_growth = _calculate_growth_rate(current_liquidity, previous_liquidity)
        
        current_holders = token_data.get('holder_count', 0)
        previous_holders = token_data.get('previous_holder_count', current_holders)
        holder_growth = _calculate_growth_rate(current_holders, previous_holders)
        
        current_volume = token_data.get('volume_24h_usd', 0)
        previous_volume = token_data.get('previous_volume_24h_usd', current_volume)
        volume_growth = _calculate_growth_rate(current_volume, previous_volume)
        
        smart_money_flow = token_data.get('smart_money_flow', 0)
        
        return {
            'whale_holdings': whale_holdings,
            'smart_money_flow': smart_money_flow,
            'liquidity_growth': liquidity_growth,
            'holder_growth': holder_growth,
            'volume_growth': volume_growth
        }
        
    except Exception as e:
        logger.error(f"Error calculating token metrics: {e}")
        return _get_default_metrics()

def _calculate_growth_rate(current: float, previous: float) -> float:
    """Calculate growth rate between two values"""
    if previous == 0:
        return 0
    return ((current - previous) / previous)

def _get_default_metrics() -> Dict[str, float]:
    """Get default metrics with zero values"""
    return {
        'whale_holdings': 0,
        'smart_money_flow': 0,
        'liquidity_growth': 0,
        'holder_growth': 0,
        'volume_growth': 0
    }

class TokenMetricsProcessor:
    """
    Processes token metrics and growth rates, working with TokenMetrics for core data.
    Focuses on calculating derived metrics and growth rates while using TokenMetrics
    for base calculations.
    """
    
    def __init__(self, rpc_client):
        self.token_metrics = TokenMetrics(rpc_client)
        
    async def get_token_metrics(
        self,
        token_address: str,
        token_data: Dict[str, Any],
        curve_state: Any
    ) -> Dict[str, Any]:
        """
        Get comprehensive token metrics combining core and derived metrics
        
        Args:
            token_address: Token's address
            token_data: Historical and current token data
            curve_state: Current bonding curve state
            
        Returns:
            Dict containing all metrics including growth rates
        """
        try:
            # Get core metrics from TokenMetrics
            core_metrics = await self.token_metrics.get_all_metrics(
                token_address,
                curve_state
            )
            
            # Calculate additional metrics
            additional_metrics = {
                'whale_holdings': self._calculate_whale_holdings(token_data),
                'whale_holding_percentage': self._calculate_whale_holding_percentage(token_data),
                'liquidity_growth': self._calculate_liquidity_growth(token_data),
                'holder_growth': self._calculate_holder_growth(token_data),
                'volume_growth': self._calculate_volume_growth(token_data),
                'price_growth': self._calculate_price_growth(token_data),
                'distribution_metrics': self._calculate_distribution_metrics(token_data)
            }
            
            # Combine all metrics
            metrics = {
                **core_metrics,
                **additional_metrics,
                'timestamp': token_data.get('timestamp'),
                'growth_metrics': {
                    'weekly_growth': self._calculate_period_growth(token_data, days=7),
                    'monthly_growth': self._calculate_period_growth(token_data, days=30)
                }
            }
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error calculating metrics for {token_address}: {e}")
            return self._get_default_metrics()

    def _calculate_whale_holdings(self, token_data: Dict[str, Any]) -> float:
        """
        Calculate absolute value of whale holdings in tokens
        
        Args:
            token_data: Token data including holder distribution
            
        Returns:
            float: Total tokens held by whales
        """
        try:
            total_supply = token_data.get('total_supply', 0)
            if total_supply == 0:
                return 0
                
            whale_threshold = total_supply * 0.05  # 5% of supply
            whale_balance = sum(
                balance for balance in token_data.get('holder_distribution', {}).values()
                if balance > whale_threshold
            )
            
            return whale_balance
            
        except Exception as e:
            logger.error(f"Error calculating whale holdings: {e}")
            return 0

    def _calculate_whale_holding_percentage(self, token_data: Dict[str, Any]) -> float:
        """Calculate percentage of supply held by whales"""
        try:
            total_supply = token_data.get('total_supply', 0)
            if total_supply == 0:
                return 0
                
            whale_holdings = self._calculate_whale_holdings(token_data)
            return (whale_holdings / total_supply) * 100
            
        except Exception as e:
            logger.error(f"Error calculating whale holding percentage: {e}")
            return 0

    def _calculate_liquidity_growth(self, token_data: Dict[str, Any]) -> float:
        """Calculate liquidity growth rate between snapshots"""
        try:
            current_liquidity = token_data.get('liquidity', 0)
            previous_liquidity = token_data.get('previous_liquidity', current_liquidity)
            
            if previous_liquidity == 0:
                return 0
                
            growth = ((current_liquidity - previous_liquidity) / previous_liquidity) * 100
            return round(growth, 2)
            
        except Exception as e:
            logger.error(f"Error calculating liquidity growth: {e}")
            return 0

    def _calculate_holder_growth(self, token_data: Dict[str, Any]) -> float:
        """Calculate holder count growth rate"""
        try:
            current_holders = token_data.get('holder_count', 0)
            previous_holders = token_data.get('previous_holder_count', current_holders)
            
            if previous_holders == 0:
                return 0
                
            growth = ((current_holders - previous_holders) / previous_holders) * 100
            return round(growth, 2)
            
        except Exception as e:
            logger.error(f"Error calculating holder growth: {e}")
            return 0

    def _calculate_volume_growth(self, token_data: Dict[str, Any]) -> float:
        """Calculate 24h volume growth rate"""
        try:
            current_volume = token_data.get('volume_24h_usd', 0)
            previous_volume = token_data.get('previous_volume_24h_usd', current_volume)
            
            if previous_volume == 0:
                return 0
                
            growth = ((current_volume - previous_volume) / previous_volume) * 100
            return round(growth, 2)
            
        except Exception as e:
            logger.error(f"Error calculating volume growth: {e}")
            return 0

    def _calculate_price_growth(self, token_data: Dict[str, Any]) -> float:
        """Calculate price growth rate"""
        try:
            current_price = token_data.get('price_usd', 0)
            previous_price = token_data.get('previous_price_usd', current_price)
            
            if previous_price == 0:
                return 0
                
            growth = ((current_price - previous_price) / previous_price) * 100
            return round(growth, 2)
            
        except Exception as e:
            logger.error(f"Error calculating price growth: {e}")
            return 0

    def _calculate_distribution_metrics(self, token_data: Dict[str, Any]) -> Dict[str, float]:
        """Calculate holder distribution metrics"""
        try:
            total_supply = token_data.get('total_supply', 0)
            if total_supply == 0:
                return self._get_default_distribution_metrics()

            holder_distribution = token_data.get('holder_distribution', {})
            
            # Calculate different holder group percentages
            whales = sum(bal for bal in holder_distribution.values() if bal > total_supply * 0.05)
            large = sum(bal for bal in holder_distribution.values() if total_supply * 0.01 < bal <= total_supply * 0.05)
            medium = sum(bal for bal in holder_distribution.values() if total_supply * 0.001 < bal <= total_supply * 0.01)
            small = sum(bal for bal in holder_distribution.values() if bal <= total_supply * 0.001)
            
            return {
                'whale_percentage': (whales / total_supply) * 100,
                'large_holder_percentage': (large / total_supply) * 100,
                'medium_holder_percentage': (medium / total_supply) * 100,
                'small_holder_percentage': (small / total_supply) * 100
            }
            
        except Exception as e:
            logger.error(f"Error calculating distribution metrics: {e}")
            return self._get_default_distribution_metrics()

    def _calculate_period_growth(self, token_data: Dict[str, Any], days: int) -> Dict[str, float]:
        """Calculate growth metrics over specified period"""
        try:
            period_data = token_data.get(f'{days}d_data', {})
            if not period_data:
                return self._get_default_period_growth()

            return {
                'price_growth': self._calculate_price_growth(period_data),
                'volume_growth': self._calculate_volume_growth(period_data),
                'holder_growth': self._calculate_holder_growth(period_data),
                'liquidity_growth': self._calculate_liquidity_growth(period_data)
            }
            
        except Exception as e:
            logger.error(f"Error calculating {days}d growth: {e}")
            return self._get_default_period_growth()

    def _get_default_metrics(self) -> Dict[str, Any]:
        """Get default metrics structure with zero values"""
        return {
            'market_cap_usd': 0,
            'volume_24h_usd': 0,
            'holder_count': 0,
            'liquidity': 0,
            'smart_money_flow': 0,
            'liquidity_ratio': 0,
            'price_usd': 0,
            'whale_holdings': 0,
            'whale_holding_percentage': 0,
            'liquidity_growth': 0,
            'holder_growth': 0,
            'volume_growth': 0,
            'price_growth': 0,
            'distribution_metrics': self._get_default_distribution_metrics(),
            'growth_metrics': {
                'weekly_growth': self._get_default_period_growth(),
                'monthly_growth': self._get_default_period_growth()
            }
        }

    def _get_default_distribution_metrics(self) -> Dict[str, float]:
        """Get default distribution metrics structure"""
        return {
            'whale_percentage': 0,
            'large_holder_percentage': 0,
            'medium_holder_percentage': 0,
            'small_holder_percentage': 0
        }

    def _get_default_period_growth(self) -> Dict[str, float]:
        """Get default period growth structure"""
        return {
            'price_growth': 0,
            'volume_growth': 0,
            'holder_growth': 0,
            'liquidity_growth': 0
        }