import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.instruction import Instruction
from src.config.settings import settings
from src.utils.rate_limiter import GlobalRateLimiter

logger = logging.getLogger(__name__)

class TokenMetrics:
    def __init__(self, rpc_client: AsyncClient):
        self.rpc_client = rpc_client
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
            program_id = Pubkey.from_string(settings['PUMP_PROGRAM'])
            bonding_curve_address = self.get_associated_bonding_curve_address(
                Pubkey.from_string(token_address),
                program_id
            )[0]
            
            # Call all metrics with rate limiting
            volume_24h = await self.get_token_volume_24h(token_address, str(bonding_curve_address))
            holder_count = await self.get_holder_count(token_address)
            sol_price = await self._get_sol_price()
            
            # Calculate derived metrics
            market_cap = self.calculate_market_cap(curve_state, sol_price)
            smart_money_flow = await self._calculate_smart_money_flow(
                token_address,
                volume_24h,
                market_cap
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
        volume_24h: float,
        market_cap: float
    ) -> float:
        """
        Calculate smart money flow metric based on transaction patterns
        
        Args:
            token_address: Token's address
            volume_24h: 24h trading volume
            market_cap: Current market cap
            
        Returns:
            float: Smart money flow score (0-1)
        """
        try:
            if volume_24h == 0 or market_cap == 0:
                return 0.0
            
            # Get recent transactions with rate limiting
            signatures = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                Pubkey.from_string(token_address),
                limit=100
            )
            
            if not signatures.value:
                return 0.0
                
            total_volume = 0
            smart_volume = 0
            
            # Process transactions in batches with rate limiting
            batch_size = 10
            for i in range(0, len(signatures.value), batch_size):
                batch = signatures.value[i:i + batch_size]
                batch_calls = [
                    {'func': self.rpc_client.get_transaction, 'args': [sig.signature]}
                    for sig in batch
                ]
                
                batch_results = await self.rate_limiter.execute_batch(batch_calls, batch_size=5)
                
                for tx in batch_results:
                    if isinstance(tx, Exception) or not tx.value:
                        continue
                        
                    # Analyze transaction
                    volume = await self._get_transaction_volume(tx.value)
                    if volume == 0:
                        continue
                        
                    total_volume += volume
                    
                    # Check if it's a "smart money" transaction
                    if await self._is_smart_transaction(tx.value, market_cap):
                        smart_volume += volume
                    
            # Calculate smart money ratio
            if total_volume == 0:
                return 0.0
                
            return min(1.0, smart_volume / total_volume)
            
        except Exception as e:
            logger.error(f"Error calculating smart money flow: {e}")
            return 0.0
            
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

    async def get_token_volume_24h(self, token_address: str, bonding_curve_address: str) -> float:
        """Get 24h volume for a token by analyzing bonding curve transactions"""
        try:
            # Get recent signatures with rate limiting
            signatures = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                Pubkey.from_string(bonding_curve_address),
                limit=1000
            )
            
            if not signatures.value:
                return 0.0

            total_volume = 0.0
            time_cutoff = datetime.now() - timedelta(hours=24)
            
            # Process in batches with rate limiting
            batch_size = 10
            for i in range(0, len(signatures.value), batch_size):
                batch = signatures.value[i:i + batch_size]
                batch_calls = [
                    {'func': self.rpc_client.get_transaction, 'args': [sig.signature]}
                    for sig in batch
                    if sig.block_time and datetime.fromtimestamp(sig.block_time) >= time_cutoff
                ]
                
                if not batch_calls:
                    continue
                    
                batch_results = await self.rate_limiter.execute_batch(batch_calls, batch_size=5)
                
                for result in batch_results:
                    if isinstance(result, Exception):
                        continue
                    volume = await self._process_volume_transaction(result)
                    if isinstance(volume, float):
                        total_volume += volume

            return total_volume

        except Exception as e:
            logger.error(f"Error getting volume for {token_address}: {e}")
            return 0.0

    async def get_holder_count(self, token_address: str) -> int:
        """Get current number of token holders"""
        try:
            # Get token accounts with rate limiting
            accounts = await self.rate_limiter.call(
                self.rpc_client.get_token_accounts_by_mint,
                mint=Pubkey.from_string(token_address),
                commitment="confirmed"
            )
            
            if not accounts.value:
                return 0

            # Count non-zero balance holders
            unique_holders = set()
            for account in accounts.value:
                try:
                    parsed_data = account.account.data['parsed']['info']
                    balance = int(parsed_data['tokenAmount']['amount'])
                    if balance > 0:
                        unique_holders.add(parsed_data['owner'])
                except (KeyError, ValueError):
                    continue

            return len(unique_holders)

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
        """Get current SOL price in USD"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://price.jup.ag/v4/price?ids=SOL') as response:
                    if response.status == 200:
                        data = await response.json()
                        return float(data['data']['SOL']['price'])
            return 0.0
        except Exception as e:
            logger.error(f"Error getting SOL price: {e}")
            return 0.0

    async def _process_volume_transaction(self, tx: Any) -> float:
        """Process a single transaction for volume calculation"""
        try:
            if not tx.value or not tx.value.meta:
                return 0.0

            total_volume = 0.0
            for pre, post in zip(tx.value.meta.pre_balances, tx.value.meta.post_balances):
                if pre != post:
                    volume = abs(post - pre) / 1e9  # Convert lamports to SOL
                    total_volume += volume

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
            account = await self.rate_limiter.call(
                self.rpc_client.get_account_info,
                wallet
            )
            
            if not account.value:
                return False
                
            # Look for signs of sophisticated trading:
            # - Wallet has reasonable balance
            if account.value.lamports < 1e9:  # Less than 1 SOL
                return False
                
            # - Check transaction history with rate limiting
            signatures = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                wallet,
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