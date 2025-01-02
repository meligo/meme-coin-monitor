import logging
from typing import Dict, List

import numpy as np

logger = logging.getLogger(__name__)

class DistributionCalculator:
    @staticmethod
    def gini_coefficient(arr: np.ndarray) -> float:
        """Calculate Gini coefficient for inequality measurement"""
        if len(arr) == 0:
            return 0
        arr = np.sort(arr)
        index = np.arange(1, len(arr) + 1)
        return ((np.sum((2 * index - len(arr) - 1) * arr)) / 
                (len(arr) * np.sum(arr)))

    async def calculate_distribution_metrics(self, holders: List[Dict]) -> Dict:
        """Calculate holder distribution metrics using numpy for efficiency"""
        try:
            balances = np.array([h['balance'] for h in holders])
            total_supply = np.sum(balances)
            
            # Calculate percentiles for distribution ranges
            percentiles = np.percentile(balances, [25, 50, 75, 90, 95, 99])
            
            # Calculate Gini coefficient
            gini_coefficient = self.gini_coefficient(balances)
            
            return {
                'percentiles': {
                    'p25': float(percentiles[0]),
                    'p50': float(percentiles[1]),
                    'p75': float(percentiles[2]),
                    'p90': float(percentiles[3]),
                    'p95': float(percentiles[4]),
                    'p99': float(percentiles[5])
                },
                'gini_coefficient': float(gini_coefficient),
                'ranges': {
                    'small': int(len(balances[balances <= percentiles[1]])),
                    'medium': int(len(balances[(balances > percentiles[1]) & (balances <= percentiles[3])])),
                    'large': int(len(balances[balances > percentiles[3]]))
                }
            }
        except Exception as e:
            logger.error(f"Error calculating distribution metrics: {e}")
            return {}

    async def calculate_concentration_metrics(self, holders: List[Dict], total_supply: int) -> Dict:
        """Calculate concentration metrics for holders"""
        try:
            if not holders or total_supply == 0:
                return {}

            sorted_holders = sorted(holders, key=lambda x: x['balance'], reverse=True)
            
            # Calculate concentrations for different holder groups
            top_10_balance = sum(h['balance'] for h in sorted_holders[:10])
            top_50_balance = sum(h['balance'] for h in sorted_holders[:50])
            top_100_balance = sum(h['balance'] for h in sorted_holders[:100])
            
            return {
                'top_10_concentration': (top_10_balance / total_supply) * 100,
                'top_50_concentration': (top_50_balance / total_supply) * 100,
                'top_100_concentration': (top_100_balance / total_supply) * 100,
                'total_holders': len(holders)
            }
            
        except Exception as e:
            logger.error(f"Error calculating concentration metrics: {e}")
            return {}


