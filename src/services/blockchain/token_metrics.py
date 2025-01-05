import asyncio
import base64
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
from construct import Flag, Int8ul, Int64ul, Struct
from solana.rpc.commitment import Commitment
from solana.rpc.types import MemcmpOpts
from solders.instruction import Instruction
from solders.pubkey import Pubkey

from src.config.database import db, get_db_session
from src.config.settings import settings
from src.utils.rate_limiter import GlobalRateLimiter
from src.utils.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

class BondingCurveState:
    _STRUCT = Struct(
        "virtual_token_reserves" / Int64ul,
        "virtual_sol_reserves" / Int64ul,
        "real_token_reserves" / Int64ul,
        "real_sol_reserves" / Int64ul,
        "token_total_supply" / Int64ul,
        "complete" / Flag
    )

    def __init__(self, data: bytes) -> None:
        parsed = self._STRUCT.parse(data[8:])
        self.__dict__.update(parsed)
        
class TokenMetrics:
    def __init__(self, rpc_manager: RPCManager):
        self.rpc_manager = rpc_manager
        self.rate_limiter = GlobalRateLimiter()
        
    async def get_all_metrics(self, token_address: str, curve_state: Any) -> Dict[str, Any]:
        """
        Get comprehensive metrics for a token.
        
        Args:
            token_address: The token's address
            curve_state: Current bonding curve state
            
        Returns:
            Dict containing all token metrics
        """
        try:
            # Get bonding curve address
            program_id = Pubkey.from_string(settings.pump_program)
            bonding_curve_address = self.get_associated_bonding_curve_address(
                Pubkey.from_string(token_address),
                program_id
            )[0]
            
            # Call all metrics with rate limiting
            volume_24h = await self.get_token_volume_24h(token_address)
            holder_count = await self.get_holder_count(token_address)
            sol_price = await self._get_sol_price()
            
            # Calculate derived metrics
            market_cap = self.calculate_market_cap(curve_state, sol_price)
            smart_money_flow = await self._calculate_smart_money_flow(
                token_address,
                market_cap,
                volume_24h,
            )
            liquidity = self._calculate_liquidity(curve_state, sol_price)
            
            return {
                'market_cap_usd': market_cap,
                'volume_24h_usd': volume_24h,
                'holder_count': holder_count,
                'liquidity': liquidity,
                'smart_money_flow': smart_money_flow,
                'liquidity_ratio': self._calculate_liquidity_ratio(liquidity, market_cap),
                'price_usd': self._calculate_token_price(curve_state, sol_price)
            }
            
        except Exception as e:
            logger.error(f"Error getting metrics for {token_address}: {e}")
            return {
                'market_cap_usd': 0,
                'volume_24h_usd': 0,
                'holder_count': 0,
                'liquidity': 0,
                'smart_money_flow': 0,
                'liquidity_ratio': 0,
                'price_usd': 0
            }

    async def _calculate_smart_money_flow(
        self, 
        token_address: str,
        market_cap: Optional[float] = None,
        volume_24h: Optional[float] = None
    ) -> Dict[str, Any]:
        """Calculate smart money flow metrics - tracks sophisticated wallet movements"""
        try:
            # Config
            SMART_MONEY_THRESHOLD = 0.01  # 1% of supply or market cap
            WHALE_TRANSACTION_COUNT = 100
            
            # Get recent transactions
            response = await self.rpc_manager.get_signatures_for_address(
                Pubkey.from_string(token_address),  # Convert Pubkey to string
                limit=WHALE_TRANSACTION_COUNT,
                commitment="confirmed"
            )
            
            if not response.value:
                return self._get_default_smart_money_metrics()
                
            # Track metrics
            inflow = 0.0
            outflow = 0.0
            whale_txs = []
            smart_wallets = set()
            
            # Process transactions sequentially using RPCManager
            for sig in response.value:
                tx = await self.rpc_manager.get_transaction(
                    sig.signature,
                    encoding="jsonParsed",
                    commitment="confirmed",
                    max_supported_transaction_version=0
                )
                
                if not tx or not tx.value:
                    continue
                    
                tx_data = tx.value
                
                # Calculate transaction value
                pre_balances = {
                    acc.account_index: acc.ui_token_amount.ui_amount 
                    for acc in tx_data.meta.pre_token_balances
                }
                post_balances = {
                    acc.account_index: acc.ui_token_amount.ui_amount
                    for acc in tx_data.meta.post_token_balances
                }
                
                # Track changed balances
                for acc_idx, pre_bal in pre_balances.items():
                    post_bal = post_balances.get(acc_idx, 0)
                    change = post_bal - pre_bal
                    
                    # Filter for significant movements
                    if abs(change) > SMART_MONEY_THRESHOLD * (market_cap or 1.0):
                        if change > 0:
                            inflow += abs(change)
                        else:
                            outflow += abs(change)
                            
                        # Track whale transaction
                        whale_txs.append({
                            'signature': tx_data.transaction.signatures[0],
                            'amount': abs(change),
                            'direction': 'in' if change > 0 else 'out',
                            'wallet': tx_data.transaction.message.account_keys[acc_idx],
                            'timestamp': tx_data.block_time
                        })
                        
                        # Track unique smart wallets
                        smart_wallets.add(tx_data.transaction.message.account_keys[acc_idx])
            
            # Calculate metrics
            total_flow = inflow + outflow
            flow_score = ((inflow - outflow) / total_flow) if total_flow > 0 else 0
            
            # Calculate concentration
            if market_cap and market_cap > 0:
                concentration = total_flow / market_cap
            else:
                concentration = total_flow / (inflow + outflow) if (inflow + outflow) > 0 else 0
            
            return {
                'smart_money_flow_score': max(-1.0, min(1.0, flow_score)),
                'whale_transactions': whale_txs[:10],  # Return most recent 10
                'smart_wallet_count': len(smart_wallets),
                'concentration_score': max(0.0, min(1.0, concentration))
            }
            
        except Exception as e:
            logger.error(f"Error calculating smart money flow: {e}")
            return self._get_default_smart_money_metrics()
        
    def _get_default_smart_money_metrics(self) -> Dict[str, Any]:
        """Get default metrics structure with zero values"""
        return {
            'smart_money_flow_score': 0.0,
            'whale_transactions': [],
            'smart_wallet_count': 0,
            'concentration_score': 0.0
        }
            
    async def _is_smart_transaction(self, tx: Any, market_cap: float) -> bool:
        """Determine if a transaction shows sophisticated trading behavior"""
        try:
            # Size check - smart money typically makes larger trades
            volume = await self._get_transaction_volume(tx)
            if volume < (market_cap * 0.001):  # Less than 0.1% of market cap
                return False
                
            # Timing check - smart money often trades at optimal times
            if not await self._check_timing_pattern(tx):
                return False
                
            # Wallet analysis - check sender history and patterns
            if not await self._analyze_wallet_sophistication(tx):
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Error analyzing transaction: {e}")
            return False

    async def get_token_volume_24h(self, token_address: str) -> Dict[str, float]:
        """Get 24h trading volume for a token with proper pagination"""
        try:
            current_time = int(time.time())
            time_24h_ago = current_time - 86400
            
            volume_data = {
                "volume_usd": 0.0,
                "transaction_count": 0
            }
            
            all_signatures = []
            last_signature = None
            
            while True:
                response = await self.rpc_manager.get_signatures_for_address(
                    Pubkey.from_string(token_address),  # Convert Pubkey to string
                    before=last_signature,
                    limit=1000,
                    commitment="confirmed"
                )
                
                if not response.value:
                    break
                    
                current_batch = [
                    sig for sig in response.value 
                    if sig.block_time and sig.block_time >= time_24h_ago
                ]
                
                if current_batch and current_batch[-1].block_time < time_24h_ago:
                    break
                    
                all_signatures.extend(current_batch)
                
                if len(response.value) < 1000:
                    break
                    
                last_signature = response.value[-1].signature

            # Process transactions
            for sig in all_signatures:
                tx = await self.rpc_manager.get_transaction(
                    sig.signature,
                    encoding="jsonParsed",
                    commitment="confirmed",
                    max_supported_transaction_version=0
                )
                
                if tx and tx.value:
                    volume = await self._process_volume_transaction(tx.value)
                    if volume > 0:
                        volume_data["volume_usd"] += volume
                        volume_data["transaction_count"] += 1

            return volume_data

        except Exception as e:
            logger.error(f"Error getting 24h volume: {e}")
            return {
                "volume_usd": 0.0,
                "transaction_count": 0
            }

    async def get_holder_count(self, token_address: str) -> int:
        """Get current number of token holders by counting non-zero balance accounts"""
        try:
            # Convert token address to proper format
            mint_pubkey = Pubkey.from_string(token_address)
            token_program_id = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
            
            # Log the mint address for debugging
            logger.debug(f"Getting holder count for token: {token_address}")
            logger.info("bytes mint_pubkey: %s" , str(mint_pubkey))
            # Create proper filter objects - use dictionary format instead of MemcmpOpts
            filters = [
                {"dataSize": 165},  # Token account size
                {
                    "memcmp": {
                        "offset": 0,
                        "bytes": str(mint_pubkey)
                    }
                }
            ]
            
            # Log the filter structure
            logger.debug(f"Using filters: {filters}")
            
            # Use data slice to only get balance bytes
            data_slice = {
                "offset": 64,
                "length": 8
            }
            
            # Get accounts with rate limiting and debug logging
            try:
                logger.debug("Making RPC call to get_program_accounts...")
                response = await self.rpc_manager.get_program_accounts(
                    str(token_program_id),  # Convert Pubkey to string if needed
                    commitment="confirmed",
                    encoding="base64",
                    filters=filters,
                    data_slice=data_slice
                )
                logger.debug(f"RPC response received with {len(response.value) if response.value else 0} accounts")
                
            except Exception as e:
                logger.error(f"RPC call failed: {str(e)}")
                raise
                
            if not response.value:
                return 0
                
            # Count non-zero balances
            holder_count = 0
            for acc in response.value:
                try:
                    balance = int.from_bytes(base64.b64decode(acc.account.data[0]), 'little')
                    if balance > 0:
                        holder_count += 1
                except Exception as e:
                    logger.error(f"Error processing account balance: {str(e)}")
                    continue
                    
            logger.debug(f"Found {holder_count} holders for token {token_address}")
            return holder_count

        except Exception as e:
            logger.error(f"Error getting holder count: {e}")
            return 0

    def calculate_market_cap(self, curve_state: Any, sol_price: float = None) -> float:
        """Calculate market cap from bonding curve state"""
        try:
            if not curve_state:
                return 0.0

            if sol_price is None:
                sol_price = 1.0  # Will be denominated in SOL if price not provided

            real_sol_reserves = curve_state.real_sol_reserves
            real_token_reserves = curve_state.real_token_reserves
            
            if real_token_reserves == 0:
                return 0.0

            price_in_sol = real_sol_reserves / real_token_reserves
            return price_in_sol * 1_000_000_000 * sol_price

        except Exception as e:
            logger.error(f"Error calculating market cap: {e}")
            return 0.0

    def _calculate_liquidity(self, curve_state: Any, sol_price: float) -> float:
        """Calculate token liquidity in USD"""
        try:
            if not curve_state or not sol_price:
                return 0.0
                
            return curve_state.real_sol_reserves * sol_price / 1e9  # Convert from lamports
            
        except Exception as e:
            logger.error(f"Error calculating liquidity: {e}")
            return 0.0

    def _calculate_liquidity_ratio(self, liquidity: float, market_cap: float) -> float:
        """Calculate liquidity to market cap ratio"""
        try:
            if market_cap == 0:
                return 0.0
            return liquidity / market_cap
        except Exception as e:
            logger.error(f"Error calculating liquidity ratio: {e}")
            return 0.0

    def _calculate_token_price(self, curve_state: Any, sol_price: float) -> float:
        """Calculate token price in USD"""
        try:
            if not curve_state or curve_state.real_token_reserves == 0:
                return 0.0
                
            price_in_sol = curve_state.real_sol_reserves / curve_state.real_token_reserves
            return price_in_sol * sol_price
            
        except Exception as e:
            logger.error(f"Error calculating token price: {e}")
            return 0.0

    async def _get_sol_price(self) -> float:
        """Get current SOL price in USD using Raydium API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.raydium.io/v2/main/price') as response:
                    if response.status == 200:
                        data = await response.json()
                        return float(data.get('SOL', {}).get('price', 0))
            return 0.0
        except Exception as e:
            logger.error(f"Error getting SOL price: {e}")
            return 0.0

    async def _process_volume_transaction(self, tx: Any) -> float:
        """Process a single transaction for volume calculation"""
        try:
            # Check if we have a proper transaction object with metadata
            if not hasattr(tx, 'meta'):
                return 0.0
                
            # We only need meta_data for balance changes
            meta_data = tx.meta
            
            if not meta_data:
                return 0.0

            # Calculate total volume from pre/post balances
            total_volume = 0.0
            
            # Get pre and post token balances
            pre_balances = getattr(meta_data, 'pre_token_balances', []) or []
            post_balances = getattr(meta_data, 'post_token_balances', []) or []
            
            # Create balance lookup maps
            pre_map = {
                bal.account_index: float(bal.ui_token_amount.amount)
                for bal in pre_balances
                if hasattr(bal, 'ui_token_amount')
            }
            
            post_map = {
                bal.account_index: float(bal.ui_token_amount.amount)
                for bal in post_balances
                if hasattr(bal, 'ui_token_amount')
            }
            
            # Calculate volume from balance changes
            for acc_idx in set(pre_map.keys()) | set(post_map.keys()):
                pre_amount = pre_map.get(acc_idx, 0.0)
                post_amount = post_map.get(acc_idx, 0.0)
                
                change = abs(post_amount - pre_amount)
                if change > 0:
                    total_volume += change

            return total_volume

        except Exception as e:
            logger.error(f"Error processing transaction: {str(e)}")
            return 0.0

    async def _get_transaction_volume(self, tx: Any) -> float:
        """Extract transaction volume from transaction data"""
        try:
            if not tx.meta:
                return 0.0
                
            pre = tx.meta.pre_balances[0] if tx.meta.pre_balances else 0
            post = tx.meta.post_balances[0] if tx.meta.post_balances else 0
            
            return abs(post - pre) / 1e9  # Convert lamports to SOL
            
        except Exception as e:
            logger.error(f"Error getting transaction volume: {e}")
            return 0.0

    async def _check_timing_pattern(self, tx: Any) -> bool:
        """Analyze if transaction timing indicates sophisticated trading"""
        try:
            if not tx.block_time:
                return False
                
            # Check if trade was made during typically advantageous times
            tx_time = datetime.fromtimestamp(tx.block_time)
            hour = tx_time.hour
            
            # Smart money often trades during less active hours
            return 0 <= hour <= 4 or 20 <= hour <= 23
            
        except Exception as e:
            logger.error(f"Error checking timing pattern: {e}")
            return False

    async def _analyze_wallet_sophistication(self, tx: Any) -> bool:
        """Analyze wallet behavior for signs of sophisticated trading"""
        try:
            if not tx.meta or not tx.meta.pre_balances:
                return False
                
            # Get wallet info with rate limiting
            wallet = tx.message.account_keys[0]
            account = await self.rpc_manager.get_account_info(
                str(wallet)  # Convert Pubkey to string if needed
            )
            
            if not account.value:
                return False
                
            # Look for signs of sophisticated trading:
            # - Wallet has reasonable balance
            if account.value.lamports < 1e9:  # Less than 1 SOL
                return False
                
            # - Check transaction history with rate limiting
            signatures = await self.rpc_manager.get_signatures_for_address(
                str(wallet),  # Convert Pubkey to string if needed
                limit=10
            )
            
            if not signatures.value:
                return False
                
            # Smart money typically has consistent trading history
            return len(signatures.value) >= 5
            
        except Exception as e:
            logger.error(f"Error analyzing wallet sophistication: {e}")
            return False

    def get_associated_bonding_curve_address(
        self,
        mint: Pubkey,
        program_id: Pubkey
    ) -> tuple[Pubkey, int]:
        """Get the bonding curve address for a token"""
        return Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint)],
            program_id
        )