from dataclasses import dataclass
from typing import Dict, Any
from datetime import timedelta

@dataclass
class TierConfig:
    check_frequency: int  # seconds
    processing_ratio: float  # percentage as decimal
    memory_ratio: float  # percentage as decimal
    max_volume: int  # approximate max tokens in tier
    monitoring_params: Dict[str, Any]

# System-wide tier configurations
TIER_CONFIGS = {
    'SIGNAL': TierConfig(
        check_frequency=15,  # 15 seconds
        processing_ratio=0.30,
        memory_ratio=0.25,
        max_volume=1000,  # Dynamic based on signals
        monitoring_params={
            'whale_threshold': 0.05,  # 5% of supply
            'smart_money_threshold': 0.1,  # 10% flow change
            'liquidity_spike_threshold': 0.2,  # 20% growth
            'holder_acceleration_threshold': 0.15  # 15% growth rate
        }
    ),
    'HOT': TierConfig(
        check_frequency=60,  # 1 minute
        processing_ratio=0.40,
        memory_ratio=0.40,
        max_volume=5000,
        monitoring_params={
            'min_liquidity_ratio': 0.05,
            'rug_pull_indicators': {
                'high_concentration': 0.80,  # 80% held by single address
                'low_liquidity': 0.05,  # 5% of market cap
                'suspicious_trades': 0.20  # 20% suspicious volume
            }
        }
    ),
    'WARM': TierConfig(
        check_frequency=1800,  # 30 minutes
        processing_ratio=0.20,
        memory_ratio=0.25,
        max_volume=35000,
        monitoring_params={
            'min_activity_threshold': 100,  # minimum transactions
            'liquidity_stability_threshold': 0.1,  # 10% variation
            'holder_retention_target': 0.7  # 70% retention
        }
    ),
    'COLD': TierConfig(
        check_frequency=21600,  # 6 hours
        processing_ratio=0.10,
        memory_ratio=0.10,
        max_volume=100000,
        monitoring_params={
            'activity_check_threshold': 10,  # minimum daily transactions
            'major_change_threshold': 0.3  # 30% change in key metrics
        }
    ),
    'ARCHIVE': TierConfig(
        check_frequency=0,  # No active monitoring
        processing_ratio=0,
        memory_ratio=0,
        max_volume=0,
        monitoring_params={
            'revival_threshold': 100,  # transactions to consider revival
            'analysis_window': timedelta(days=30)  # historical analysis window
        }
    )
}

# Transition thresholds
TIER_TRANSITION_THRESHOLDS = {
    'signal_trigger': {
        'whale_movement': 0.05,  # 5% of supply moved
        'smart_money_flow': 0.1,  # 10% positive flow
        'liquidity_growth': 0.2,  # 20% growth
        'holder_growth': 0.15  # 15% growth
    },
    'archive_trigger': {
        'inactivity_period': timedelta(days=7),
        'liquidity_loss': 0.9,  # 90% liquidity removed
        'holder_loss': 0.8  # 80% holders lost
    }
}

# Resource allocation targets
RESOURCE_ALLOCATION = {
    'processing': {
        'high_activity': 0.70,  # Signal + Hot
        'low_activity': 0.30   # Warm + Cold
    },
    'memory': {
        'signal': 0.25,
        'hot': 0.40,
        'warm': 0.25,
        'cold': 0.10
    }
}