import logging
from datetime import datetime
from typing import Dict, Any
from sqlalchemy.orm import Session
from src.core.models.tier_models import TierAlert

logger = logging.getLogger(__name__)

async def create_alert(token_address: str, alert_type: str, data: Dict[str, Any]) -> None:
    """Create a new tier alert"""
    try:
        # Get severity based on alert type
        severity = _determine_severity(alert_type, data)
        
        # Create alert description
        description = _generate_alert_description(alert_type, data)
        
        alert = {
            'token_address': token_address,
            'alert_type': alert_type,
            'severity': severity,
            'description': description,
            'data': data,
            'timestamp': datetime.utcnow()
        }
        
        logger.info(f"Created {severity} alert for {token_address}: {alert_type}")
        return alert
        
    except Exception as e:
        logger.error(f"Error creating alert: {e}")
        return None

def _determine_severity(alert_type: str, data: Dict[str, Any]) -> str:
    """Determine alert severity based on type and data"""
    high_severity_conditions = {
        'whale_movement': lambda d: d.get('amount', 0) > d.get('threshold', 0) * 2,
        'liquidity_change': lambda d: abs(d.get('change', 0)) > 0.3,
        'price_change': lambda d: abs(d.get('change', 0)) > 0.5,
        'holder_change': lambda d: abs(d.get('change', 0)) > 0.4
    }
    
    if alert_type in high_severity_conditions and high_severity_conditions[alert_type](data):
        return 'critical'
    elif 'risk' in alert_type or 'suspicious' in alert_type:
        return 'warning'
    return 'info'

def _generate_alert_description(alert_type: str, data: Dict[str, Any]) -> str:
    """Generate human-readable alert description"""
    templates = {
        'whale_movement': "Whale movement detected: {amount} tokens moved",
        'liquidity_change': "Liquidity changed by {change:.1%}",
        'price_change': "Price changed by {change:.1%}",
        'holder_change': "Holder count changed by {change:.1%}",
        'risk_assessment': "Risk level: {risk_level}"
    }
    
    template = templates.get(alert_type, "Alert: {alert_type}")
    try:
        return template.format(**data, alert_type=alert_type)
    except:
        return f"Alert: {alert_type}"