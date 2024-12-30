from typing import Dict, Any

def calculate_token_metrics(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate various token metrics from raw data"""
    try:
        metrics = {
            'whale_holdings': _calculate_whale_holdings(token_data),
            'smart_money_flow': _calculate_smart_money_flow(token_data),
            'liquidity_growth': _calculate_liquidity_growth(token_data),
            'holder_growth': _calculate_holder_growth(token_data),
            'market_cap': token_data.get('market_cap_usd', 0),
            'liquidity': token_data.get('liquidity', 0),
            'holder_count': token_data.get('holder_count', 0),
            'volume_24h': token_data.get('volume_24h_usd', 0)
        }
        return metrics
    except Exception as e:
        return {
            'error': str(e),
            'whale_holdings': 0,
            'smart_money_flow': 0,
            'liquidity_growth': 0,
            'holder_growth': 0
        }

def _calculate_whale_holdings(token_data: Dict[str, Any]) -> float:
    """Calculate percentage of supply held by whales"""
    try:
        total_supply = token_data.get('total_supply', 0)
        if total_supply == 0:
            return 0
            
        whale_balance = sum(
            balance for balance in token_data.get('holder_distribution', {}).values()
            if balance > (total_supply * 0.05)  # Whale = holds >5% of supply
        )
        return whale_balance / total_supply
    except:
        return 0

def _calculate_smart_money_flow(token_data: Dict[str, Any]) -> float:
    """Calculate smart money flow ratio"""
    try:
        total_volume = token_data.get('volume_24h_usd', 0)
        if total_volume == 0:
            return 0
            
        smart_volume = token_data.get('smart_money_volume', 0)
        return smart_volume / total_volume
    except:
        return 0

def _calculate_liquidity_growth(token_data: Dict[str, Any]) -> float:
    """Calculate liquidity growth rate"""
    try:
        current_liquidity = token_data.get('liquidity', 0)
        previous_liquidity = token_data.get('previous_liquidity', current_liquidity)
        
        if previous_liquidity == 0:
            return 0
            
        return (current_liquidity - previous_liquidity) / previous_liquidity
    except:
        return 0

def _calculate_holder_growth(token_data: Dict[str, Any]) -> float:
    """Calculate holder growth rate"""
    try:
        current_holders = token_data.get('holder_count', 0)
        previous_holders = token_data.get('previous_holder_count', current_holders)
        
        if previous_holders == 0:
            return 0
            
        return (current_holders - previous_holders) / previous_holders
    except:
        return 0