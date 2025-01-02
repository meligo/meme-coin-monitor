import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

class RugPullRisk(Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class RugDetector:
    def __init__(self):
        # Thresholds for different metrics
        self.thresholds = {
            'liquidity': {
                'severe_drop': 0.7,     # 70% drop in liquidity
                'rapid_drop': 0.5,      # 50% drop in liquidity
                'time_window': 3600     # 1 hour window for changes
            },
            'volume': {
                'pump_spike': 5.0,      # 5x volume increase
                'dump_spike': 10.0,     # 10x volume decrease
                'sustained_drop': 0.8    # 80% drop over time
            },
            'holders': {
                'whale_threshold': 0.1,  # 10% of supply
                'concentration': 0.8,    # 80% in top holders
                'rapid_decline': 0.3     # 30% holder decrease
            },
            'smart_money': {
                'outflow': 0.4,         # 40% smart money leaving
                'inflow_suspicious': 5.0 # 5x suspicious inflow
            }
        }

    async def analyze_token(
        self,
        current_state: Dict,
        historical_state: Dict,
        tier_data: Dict = None
    ) -> Tuple[float, List[str], Dict]:
        """
        Analyze token for rug pull patterns.
        
        Args:
            current_state: Current token metrics
            historical_state: Historical metrics for comparison
            tier_data: Optional tier-specific data
            
        Returns:
            Tuple containing:
            - risk_score (float): 0-1 score
            - alerts (List[str]): Alert messages
            - risk_factors (Dict): Detailed risk breakdown
        """
        risk_score = 0.0
        alerts = []
        risk_factors = {}

        # 1. Analyze liquidity patterns
        liquidity_risk = self._analyze_liquidity(current_state, historical_state)
        if liquidity_risk.score > 0:
            risk_score = max(risk_score, liquidity_risk.score)
            alerts.extend(liquidity_risk.alerts)
            risk_factors['liquidity'] = liquidity_risk.details

        # 2. Analyze volume patterns
        volume_risk = self._analyze_volume(current_state, historical_state)
        if volume_risk.score > 0:
            risk_score = max(risk_score, volume_risk.score)
            alerts.extend(volume_risk.alerts)
            risk_factors['volume'] = volume_risk.details

        # 3. Analyze holder distribution
        holder_risk = self._analyze_holders(current_state, historical_state)
        if holder_risk.score > 0:
            risk_score = max(risk_score, holder_risk.score)
            alerts.extend(holder_risk.alerts)
            risk_factors['holders'] = holder_risk.details

        # 4. Smart money movement analysis
        if 'smart_money_flow' in current_state:
            smart_money_risk = self._analyze_smart_money(current_state, historical_state)
            if smart_money_risk.score > 0:
                risk_score = max(risk_score, smart_money_risk.score)
                alerts.extend(smart_money_risk.alerts)
                risk_factors['smart_money'] = smart_money_risk.details

        # 5. Analyze tier transitions if available
        if tier_data and 'tier_history' in tier_data:
            tier_risk = self._analyze_tier_changes(tier_data)
            if tier_risk.score > 0:
                risk_score = max(risk_score, tier_risk.score)
                alerts.extend(tier_risk.alerts)
                risk_factors['tier_changes'] = tier_risk.details

        return risk_score, alerts, risk_factors

    def _analyze_liquidity(self, current: Dict, historical: Dict) -> Dict:
        """Analyze liquidity changes for rug pull patterns"""
        try:
            current_liq = float(current.get('liquidity', 0))
            historical_liq = float(historical.get('liquidity', 0))
            
            if historical_liq == 0:
                return {'score': 0, 'alerts': [], 'details': {}}

            change = (current_liq - historical_liq) / historical_liq
            
            result = {
                'score': 0,
                'alerts': [],
                'details': {
                    'current_liquidity': current_liq,
                    'historical_liquidity': historical_liq,
                    'change_percentage': change * 100
                }
            }

            # Check for severe liquidity drop
            if change <= -self.thresholds['liquidity']['severe_drop']:
                result['score'] = 0.9
                result['alerts'].append({
                    'level': 'CRITICAL',
                    'message': f'Severe liquidity drop detected: {abs(change)*100:.1f}% decrease'
                })
            # Check for rapid liquidity drop
            elif change <= -self.thresholds['liquidity']['rapid_drop']:
                result['score'] = 0.7
                result['alerts'].append({
                    'level': 'HIGH',
                    'message': f'Rapid liquidity removal: {abs(change)*100:.1f}% decrease'
                })

            return result

        except Exception as e:
            logger.error(f"Error in liquidity analysis: {e}")
            return {'score': 0, 'alerts': [], 'details': {'error': str(e)}}

    def _analyze_volume(self, current: Dict, historical: Dict) -> Dict:
        """Analyze volume patterns for manipulation"""
        try:
            current_vol = float(current.get('volume_24h', 0))
            historical_vol = float(historical.get('volume_24h', 0))
            
            result = {
                'score': 0,
                'alerts': [],
                'details': {
                    'current_volume': current_vol,
                    'historical_volume': historical_vol
                }
            }

            if historical_vol > 0:
                change = current_vol / historical_vol
                result['details']['change_ratio'] = change

                # Check for suspicious volume spike
                if change >= self.thresholds['volume']['pump_spike']:
                    result['score'] = 0.8
                    result['alerts'].append({
                        'level': 'HIGH',
                        'message': f'Suspicious volume spike: {change:.1f}x increase'
                    })
                # Check for volume dump
                elif change <= 1/self.thresholds['volume']['dump_spike']:
                    result['score'] = 0.7
                    result['alerts'].append({
                        'level': 'HIGH',
                        'message': f'Severe volume drop: {(1-change)*100:.1f}% decrease'
                    })

            return result

        except Exception as e:
            logger.error(f"Error in volume analysis: {e}")
            return {'score': 0, 'alerts': [], 'details': {'error': str(e)}}

    def _analyze_holders(self, current: Dict, historical: Dict) -> Dict:
        """Analyze holder patterns and concentration"""
        try:
            current_holders = int(current.get('holder_count', 0))
            whale_holdings = float(current.get('whale_holdings', 0))
            
            result = {
                'score': 0,
                'alerts': [],
                'details': {
                    'current_holders': current_holders,
                    'whale_holdings': whale_holdings
                }
            }

            # Check holder concentration
            if whale_holdings > self.thresholds['holders']['whale_threshold']:
                result['score'] = max(result['score'], 0.6)
                result['alerts'].append({
                    'level': 'MEDIUM',
                    'message': f'High whale concentration: {whale_holdings*100:.1f}% held by top holders'
                })

            # Check holder count
            if 'holder_count' in historical:
                holder_change = (current_holders - historical['holder_count']) / historical['holder_count']
                result['details']['holder_change'] = holder_change

                if holder_change <= -self.thresholds['holders']['rapid_decline']:
                    result['score'] = max(result['score'], 0.7)
                    result['alerts'].append({
                        'level': 'HIGH',
                        'message': f'Rapid holder decline: {abs(holder_change)*100:.1f}% decrease'
                    })

            return result

        except Exception as e:
            logger.error(f"Error in holder analysis: {e}")
            return {'score': 0, 'alerts': [], 'details': {'error': str(e)}}

    async def _analyze_smart_money(self, token_address: str) -> Dict:
        """
        Analyze smart money movements by tracking whale transactions and pattern analysis.
        Returns a dictionary containing risk assessment results.
        """
        try:
            # Initialize result structure
            result = {
                'score': 0.0,
                'alerts': [],
                'details': {
                    'inflow': 0.0,
                    'outflow': 0.0,
                    'whale_transactions': [],
                    'smart_money_flow': 0.0,
                    'whale_concentration': 0.0
                }
            }
            
            # Get recent transactions
            try:
                signatures = await self.rpc_client.get_signatures_for_address(
                    Pubkey.from_string(token_address),
                    limit=100,  # Last 100 transactions
                    commitment="confirmed"
                )
                
                if not signatures or not hasattr(signatures, 'value'):
                    logger.debug(f"No transaction history found for {token_address}")
                    return result
                    
                if not signatures.value:
                    return result
                    
            except Exception as e:
                logger.error(f"Error getting signatures: {str(e)}")
                return result

            current_time = datetime.now(timezone.utc)
            total_inflow = 0.0
            total_outflow = 0.0
            whale_txs = []
            
            # Process each transaction
            for sig in signatures.value:
                try:
                    # Skip if older than 24h
                    if sig.block_time:
                        tx_time = datetime.fromtimestamp(sig.block_time, timezone.utc)
                        if tx_time < current_time - timedelta(hours=24):
                            continue

                    # Get transaction with proper version handling
                    tx = await self.rpc_client.get_transaction(
                        sig.signature,
                        commitment="confirmed",
                        max_supported_transaction_version=0  # Required for newer transactions
                    )
                    
                    if not tx or not hasattr(tx, 'value') or not tx.value:
                        continue
                        
                    # Safely access meta
                    meta = getattr(tx.value, 'meta', None)
                    if not meta:
                        continue
                    
                    # Analyze token balance changes
                    pre_balances = getattr(meta, 'pre_token_balances', [])
                    post_balances = getattr(meta, 'post_token_balances', [])
                    
                    if pre_balances and post_balances:
                        for pre, post in zip(pre_balances, post_balances):
                            # Extract balances safely
                            pre_amount = float(pre.ui_token_amount.ui_amount or 0) if hasattr(pre, 'ui_token_amount') else 0
                            post_amount = float(post.ui_token_amount.ui_amount or 0) if hasattr(post, 'ui_token_amount') else 0
                            
                            change = post_amount - pre_amount
                            
                            # Track flow direction
                            if change > 0:
                                total_inflow += change
                                # Check for whale transaction (over 1% of supply)
                                if change / pre_amount > 0.01:
                                    whale_txs.append({
                                        'signature': sig.signature,
                                        'amount': change,
                                        'type': 'inflow',
                                        'timestamp': tx_time.isoformat()
                                    })
                            else:
                                total_outflow += abs(change)
                                # Check for whale transaction (over 1% of supply)
                                if abs(change) / pre_amount > 0.01:
                                    whale_txs.append({
                                        'signature': sig.signature,
                                        'amount': abs(change),
                                        'type': 'outflow',
                                        'timestamp': tx_time.isoformat()
                                    })
                                    
                except Exception as e:
                    logger.error(f"Error processing transaction {sig.signature}: {str(e)}")
                    continue

            # Calculate metrics
            total_volume = total_inflow + total_outflow
            if total_volume > 0:
                smart_money_flow = (total_inflow - total_outflow) / total_volume
            else:
                smart_money_flow = 0.0
                
            # Calculate risk score and generate alerts
            risk_score = 0.0
            alerts = []
            
            # Check for large outflows
            if total_volume > 0 and total_outflow / total_volume > 0.7:
                risk_score = max(risk_score, 0.8)
                alerts.append({
                    'level': 'HIGH',
                    'message': f'Large outflow detected: {(total_outflow/total_volume)*100:.1f}% of volume'
                })
                
            # Check for whale concentration
            if len(whale_txs) > 5:
                risk_score = max(risk_score, 0.6)
                alerts.append({
                    'level': 'MEDIUM',
                    'message': f'High whale activity detected: {len(whale_txs)} large transactions'
                })
                
            # Check for sudden changes
            if abs(smart_money_flow) > 0.5:
                risk_score = max(risk_score, 0.7)
                flow_type = "outflow" if smart_money_flow < 0 else "inflow"
                alerts.append({
                    'level': 'HIGH',
                    'message': f'Significant smart money {flow_type} detected'
                })

            # Update result
            result.update({
                'score': risk_score,
                'alerts': alerts,
                'details': {
                    'inflow': total_inflow,
                    'outflow': total_outflow,
                    'whale_transactions': whale_txs[:5],  # Only keep last 5 for brevity
                    'smart_money_flow': smart_money_flow,
                    'whale_concentration': len(whale_txs) / 100 if signatures.value else 0  # Normalized to 0-1
                }
            })
            
            return result

        except Exception as e:
            logger.error(f"Error in smart money analysis: {str(e)}", exc_info=True)
            return {
                'score': 0.0,
                'alerts': [],
                'details': {
                    'error': str(e),
                    'inflow': 0.0,
                    'outflow': 0.0,
                    'smart_money_flow': 0.0,
                    'whale_concentration': 0.0
                }
            }

    def _analyze_tier_changes(self, tier_data: Dict) -> Dict:
        """Analyze tier transition patterns"""
        try:
            tier_history = tier_data.get('tier_history', [])
            if len(tier_history) < 2:
                return {'score': 0, 'alerts': [], 'details': {}}

            result = {
                'score': 0,
                'alerts': [],
                'details': {
                    'tier_changes': len(tier_history) - 1,
                    'current_tier': tier_history[-1],
                    'previous_tier': tier_history[-2]
                }
            }

            # Check for rapid tier degradation
            if tier_history[-1] < tier_history[-2]:  # Assuming tiers are ordered by risk
                result['score'] = 0.5
                result['alerts'].append({
                    'level': 'MEDIUM',
                    'message': f'Rapid tier degradation: {tier_history[-2]} -> {tier_history[-1]}'
                })

            return result

        except Exception as e:
            logger.error(f"Error in tier analysis: {e}")
            return {'score': 0, 'alerts': [], 'details': {'error': str(e)}}

    def should_force_recheck(self, risk_score: float) -> bool:
        """Determine if immediate recheck is needed"""
        return risk_score >= 0.7

    def get_check_frequency(self, risk_score: float) -> int:
        """Get recommended check frequency in seconds based on risk score"""
        if risk_score >= 0.8:
            return 30    # Every 30 seconds
        elif risk_score >= 0.6:
            return 60    # Every minute
        elif risk_score >= 0.4:
            return 300   # Every 5 minutes
        else:
            return 900   # Every 15 minutes