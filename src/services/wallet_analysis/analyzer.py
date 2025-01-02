import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any

from sqlalchemy.orm import Session
from src.services.blockchain.token_metrics import TokenMetrics

logger = logging.getLogger(__name__)

@dataclass
class WalletMetrics:
    address: str
    entry_price: float
    entry_time: datetime
    current_holdings: float
    total_bought: float
    total_sold: float
    transactions: List[Dict]
    profit_taken: float
    related_wallets: Set[str]

class WalletPatternAnalyzer:
    def __init__(self, rpc_client):
        self.token_metrics = TokenMetrics(rpc_client)
        self.suspicious_patterns = {
            'early_entry': {
                'max_mcap': 50000,  # $50k market cap threshold
                'min_holdings': 0.01  # 1% minimum supply
            },
            'coordinated_trading': {
                'time_window': timedelta(minutes=5),
                'min_wallets': 3,
                'volume_threshold': 0.02
            },
            'wallet_relationships': {
                'shared_patterns': 0.8,
                'timing_correlation': 0.7
            }
        }

    async def analyze_wallet_patterns(
        self,
        token_address: str,
        token_data: Dict[str, Any],
        curve_state: Any,
        holder_data: List[Dict],
        transaction_history: List[Dict]
    ) -> Tuple[float, Dict]:
        """
        Analyze wallet patterns to detect sophisticated trading behavior and risks
        
        Args:
            token_address: Token's address
            token_data: Current token metrics
            curve_state: Current bonding curve state
            holder_data: List of current token holders
            transaction_history: List of token transactions
            
        Returns:
            Tuple[float, Dict]: Risk score and detailed analysis
        """
        try:
            # Get token metrics including smart money flow
            metrics = await self.token_metrics.get_all_metrics(
                token_address,
                curve_state
            )
            
            # Process wallet data
            wallet_metrics = self._process_wallet_metrics(
                holder_data,
                transaction_history
            )

            # Identify suspicious patterns
            early_entries = self._identify_early_entries(
                wallet_metrics,
                metrics['market_cap_usd']
            )

            wallet_clusters = self._find_related_wallets(
                wallet_metrics,
                self.suspicious_patterns['wallet_relationships']
            )

            coordinated_actions = self._detect_coordinated_actions(
                wallet_metrics,
                wallet_clusters,
                self.suspicious_patterns['coordinated_trading']
            )

            # Calculate aggregated risk score
            risk_score = self._calculate_risk_score(
                metrics['market_cap_usd'],
                early_entries,
                wallet_clusters,
                coordinated_actions
            )

            analysis_results = {
                'risk_score': risk_score,
                'early_entries': early_entries,
                'wallet_clusters': wallet_clusters,
                'coordinated_actions': coordinated_actions,
                'high_risk_wallets': self._identify_high_risk_wallets(wallet_metrics),
                'smart_money_flow': metrics['smart_money_flow'],
                'whale_concentration': self._calculate_whale_concentration(wallet_metrics),
                'analysis_timestamp': datetime.utcnow()
            }

            return risk_score, analysis_results

        except Exception as e:
            logger.error(f"Error analyzing wallet patterns: {e}")
            return 0.0, {}
