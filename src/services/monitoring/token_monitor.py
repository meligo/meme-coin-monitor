import asyncio
import binascii
import logging
import struct  # Import struct directly
import time
from base64 import b64decode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional  # Remove struct from typing import

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey
from sqlalchemy import and_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.config.database import get_async_session, get_db
from src.config.settings import settings
from src.core.models import MemeCoin, TokenTier
from src.core.models.meme_coin import HolderSnapshot, MemeCoin
from src.core.models.tier_level import TierLevel
from src.core.models.tier_models import TokenTier
from src.core.models.wallet_analysis import WalletAnalysis
from src.services.analysis.rug_detector import RugDetector
from src.services.holder_analysis.metrics_updater import HolderMetricsUpdater
from src.services.monitoring.performance import (
    MeasureBlockPerformance,
    measure_performance,
)
from src.services.pump_fun.liquidity_checker import LiquidityChecker
from src.services.transaction_processing.processor import TransactionProcessor
from src.utils.rate_limiter import GlobalRateLimiter

logger = logging.getLogger(__name__)
@dataclass
class CurveState:
    discriminator: str
    data: bytes
    account_address: str
    
class TokenMonitor:
    @property
    def CURVE_DISCRIMINATOR(self):
        return self._curve_discriminator

    @CURVE_DISCRIMINATOR.setter
    def CURVE_DISCRIMINATOR(self, value):
        if isinstance(value, str):
            self._curve_discriminator = bytes.fromhex(value)
        elif isinstance(value, bytes):
            self._curve_discriminator = value

    def __init__(self):
        self.logger = logging.getLogger(__name__)  # Initialize the logger

        # Initialize RPC client with the correct endpoint
        self.rpc_client = AsyncClient(
            endpoint=settings['RPC_ENDPOINT'],
            timeout=30,  # 30 seconds timeout
            commitment=Commitment("confirmed")
        )
        
        # Initialize program IDs from settings
        self.pump_program_id = Pubkey.from_string(settings['PUMP_PROGRAM'])
        self.token_program_id = Pubkey.from_string(settings['SYSTEM_TOKEN_PROGRAM'])
        self.pump_global = Pubkey.from_string(settings['PUMP_GLOBAL'])
        self.rate_limiter = GlobalRateLimiter()

        # Initialize curve discriminator
        self._curve_discriminator = bytes.fromhex("0b849dab1ee5ab5e")
        self._discriminator_confirmed = False
        self.MAX_RETRIES = 3
        self.RETRY_DELAY = 2
        # Initialize other components
        self.rug_detector = RugDetector()
        self.liquidity_checker = LiquidityChecker(self.rpc_client)
        self.is_running = False
        self.monitoring_tasks = {}
        self.task_lock = asyncio.Lock()
        self.holder_metrics_updater = HolderMetricsUpdater()
        self.transaction_processor = TransactionProcessor(self.rpc_client)

    async def start_monitoring(self):
        """Start the token monitoring system"""
        self.is_running = True
        asyncio.create_task(self._run_monitoring_loop())
        logger.info("Token monitoring system started")

    async def _get_curve_state(self, account_address: str) -> Optional[CurveState]:
        try:
            account_pubkey = Pubkey.from_string(account_address)

            # Use rate limiter for RPC call
            response = await self.rate_limiter.call(
                self.rpc_client.get_account_info,
                account_pubkey
            )
            
            if not response or not response.value:
                return None

            data = response.value.data
            if len(data) < 8:
                self.logger.warning(f"Data too short for curve state: {len(data)} bytes")
                return None

            discriminator = data[:8].hex()
            
            # Detailed logging
            self.logger.info(f"Processing account {account_address}")
            self.logger.info(f"Data length: {len(data)} bytes")
            self.logger.info(f"First 8 bytes (hex): {discriminator}")
            self.logger.info(f"Expected discriminator (hex): {self.CURVE_DISCRIMINATOR}")

            if discriminator != self.CURVE_DISCRIMINATOR:
                self.logger.warning(f"Discriminator mismatch for {account_address}")
                self.logger.warning(f"Got:      {discriminator}")
                self.logger.warning(f"Expected: {self.CURVE_DISCRIMINATOR}")
                return None

            return CurveState(
                discriminator=discriminator,
                data=data,
                account_address=account_address
            )

        except Exception as e:
            self.logger.error(f"Error getting curve state: {str(e)}")
            self.logger.error("Exception traceback:", exc_info=True)
            return None
    
    async def _auto_update_discriminator(self, observed_discriminator: bytes) -> bool:
        """
        Update discriminator if we consistently see a different value
        Returns True if discriminator was updated
        """
        if not self._discriminator_confirmed and observed_discriminator != self._curve_discriminator:
            # Log the potential update
            self.logger.info(f"Updating discriminator from {self._curve_discriminator.hex()} to {observed_discriminator.hex()}")
            
            # Update the discriminator
            self._curve_discriminator = observed_discriminator
            self._discriminator_confirmed = True
            
            return True
        return False
    
    
    async def _get_bonding_curve_state(self, bonding_curve_address: str) -> Optional[dict]:
        """Get bonding curve state from the account"""
        try:
            pubkey = Pubkey.from_string(bonding_curve_address)
            # Use rate limiter for RPC call
            response = await self.rate_limiter.call(
                self.rpc_client.get_account_info,
                pubkey
            )
            if not response.value or not response.value.data:
                logger.warning(f"No data found for bonding curve: {pubkey}")
                return None

            data = response.value.data
            if len(data) < 8:
                self.logger.warning(f"Data too short for curve state: {len(data)} bytes")
                return None
                
            # Get the observed discriminator
            observed_discriminator = data[:8]
            
            # Log for debugging
            self.logger.info(f"Processing account {bonding_curve_address}")
            self.logger.info(f"Data length: {len(data)} bytes")
            self.logger.info(f"First 8 bytes (hex): {observed_discriminator.hex()}")
            
            # Auto-update discriminator if needed
            if await self._auto_update_discriminator(observed_discriminator):
                self.logger.info("Discriminator was auto-updated based on observed value")
            
            # Now use the potentially updated discriminator for comparison
            if observed_discriminator != self.CURVE_DISCRIMINATOR:
                self.logger.warning(f"Discriminator mismatch for {bonding_curve_address}")
                self.logger.warning(f"Got:      {observed_discriminator.hex()}")
                self.logger.warning(f"Expected: {self.CURVE_DISCRIMINATOR.hex()}")
                return None

            # Parse the data (offset 8 bytes to skip discriminator)
            curve_data = data[8:]
            
            # Pump.fun bonding curve layout:
            parsed = struct.unpack("<QQQQQ", curve_data[:40])
            
            return {
                'base_tokens': parsed[0] / 1e9,  # Convert lamports to SOL
                'current_supply': parsed[1],
                'max_supply': parsed[2],
                'buy_tax_bps': parsed[3],
                'sell_tax_bps': parsed[4]
            }
            
        except Exception as e:
            self.logger.error(f"Error getting curve state: {str(e)}", exc_info=True)
            return None
        
    def _parse_curve_state(self, data: bytes) -> dict:
        """Parse binary curve state data into a dictionary"""
        try:
            # Pump.fun bonding curve state layout
            # u64: supply
            # u64: base_tokens
            # u64: max_tokens
            # u64: total_tokens_sold
            # u64: buy_tax_bps
            # u64: sell_tax_bps
            # u64: tax_recipient
            fields = struct.unpack("<QQQQQQQ", data[:56])
            
            return {
                "supply": fields[0],
                "base_tokens": fields[1],
                "max_tokens": fields[2],
                "total_tokens_sold": fields[3],
                "buy_tax_bps": fields[4],
                "sell_tax_bps": fields[5],
                "tax_recipient": fields[6]
            }
        except Exception as e:
            self.logger.error(f"Error parsing curve state: {str(e)}")
            raise
            
    async def _get_token_price(self, token_address: str, bonding_curve_address: str) -> float:
        """Calculate current token price from bonding curve state"""
        try:
            curve_state = await self._get_bonding_curve_state(bonding_curve_address)
            if not curve_state:
                return 0.0
            
            # Price calculation based on Pump.fun's step function bonding curve
            # Price increases as more tokens are sold
            total_sold = curve_state["total_tokens_sold"]
            max_tokens = curve_state["max_tokens"]
            
            # Implement step function pricing
            progress = total_sold / max_tokens
            base_price = 0.0001  # Starting price in SOL
            
            # Price increases in steps based on progress
            if progress < 0.2:
                multiplier = 1
            elif progress < 0.4:
                multiplier = 2
            elif progress < 0.6:
                multiplier = 4
            elif progress < 0.8:
                multiplier = 8
            else:
                multiplier = 16
                
            return base_price * multiplier
            
        except Exception as e:
            self.logger.error(f"Error calculating price: {str(e)}")
            return 0.0
        
        
    
    async def _get_24h_volume(self, token_address: str, bonding_curve_address: str) -> float:
        """Calculate 24h trading volume from on-chain transactions"""
        try:
            # Use rate limiter for RPC call
            signatures_resp = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                Pubkey.from_string(bonding_curve_address),
                limit=1000
            )
            
            if not signatures_resp.value:
                return 0.0
                
            total_volume = 0.0
            current_time = int(time.time())
            
            # Prepare batch of transaction requests
            tx_requests = []
            for sig in signatures_resp.value:
                if current_time - sig.block_time > 86400:  # Skip if older than 24h
                    continue
                    
                tx_requests.append({
                    'func': self.rpc_client.get_transaction,
                    'args': [sig.signature],
                    'kwargs': {
                        'max_supported_transaction_version': 0,
                        'commitment': Commitment.CONFIRMED
                    }
                })
            
            # Execute batch of transaction requests with rate limiter
            tx_responses = await self.rate_limiter.execute_batch(tx_requests, batch_size=10)
            
            # Process responses
            for tx_response in tx_responses:
                if isinstance(tx_response, Exception):
                    continue
                    
                if not tx_response or not tx_response.value or not tx_response.value.meta:
                    continue
                    
                # Calculate volume from pre/post balances
                pre_sol = tx_response.value.meta.pre_balances[0] / 1e9
                post_sol = tx_response.value.meta.post_balances[0] / 1e9
                
                if abs(post_sol - pre_sol) > 0.0001:  # Filter out dust
                    total_volume += abs(post_sol - pre_sol)
            
            return total_volume
            
        except Exception as e:
            self.logger.error(f"Error calculating 24h volume: {str(e)}")
            return 0.0
        
    async def _get_liquidity(self, token_address: str, bonding_curve_address: str) -> float:
        """Calculate token liquidity based on bonding curve state"""
        try:
            curve_state = await self._get_bonding_curve_state(bonding_curve_address)
            if not curve_state:
                return 0.0
                
            # For Pump.fun, liquidity is the amount of SOL in the bonding curve
            return curve_state['base_tokens']
                
        except Exception as e:
            self.logger.error(f"Error calculating liquidity: {str(e)}")
            return 0.0
        
    
    async def _run_monitoring_loop(self):
            while self.is_running:
                try:
                    async with get_async_session() as db:
                        # Get current time and remove timezone for db comparison
                        current_time = datetime.now(timezone.utc)
                        current_time_naive = current_time.replace(tzinfo=None)
                        
                        # Query tokens due for checking
                        result = await db.execute(
                            select(TokenTier).where(
                                and_(
                                    TokenTier.is_active.is_(True),
                                    TokenTier.is_monitoring_paused.is_(False),
                                    TokenTier.next_check_at <= current_time_naive
                                )
                            )
                        )
                        tokens_to_check = result.scalars().all()

                        for token in tokens_to_check:
                            await self._ensure_token_task(token.token_address)

                        await self._cleanup_tasks()

                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")
                    await asyncio.sleep(5)
    
    def _parse_transaction_volume(self, transaction) -> float:
        """Parse transaction to extract trading volume from Pump.fun trades"""
        try:
            if not transaction or not transaction.meta:
                return 0.0

            volume = 0.0
            
            # Get pre and post token balances
            pre_balances = {
                acc.account_index: acc.ui_token_amount.ui_amount
                for acc in transaction.meta.pre_token_balances
                if acc.ui_token_amount.ui_amount is not None
            }
            
            post_balances = {
                acc.account_index: acc.ui_token_amount.ui_amount
                for acc in transaction.meta.post_token_balances
                if acc.ui_token_amount.ui_amount is not None
            }

            # Process each instruction in the transaction
            for ix in transaction.transaction.message.instructions:
                # Check if it's a Pump.fun program instruction
                if str(ix.program_id) != str(self.PUMP_PROGRAM_ID):
                    continue

                # Decode instruction data
                if len(ix.data) < 8:
                    continue
                    
                # First 8 bytes are the instruction discriminator
                discriminator = ix.data[:8].hex()
                
                # Check instruction type
                if discriminator == "7796a649dd114d":  # Buy instruction
                    # Parse buy instruction data
                    # Layout: [discriminator(8), amount(8)]
                    if len(ix.data) >= 16:
                        amount_data = ix.data[8:16]
                        amount = int.from_bytes(amount_data, 'little') / 1e9  # Convert from lamports
                        volume += amount
                        
                elif discriminator == "41969aa66af11":  # Sell instruction
                    # Parse sell instruction data
                    # Layout: [discriminator(8), amount(8)]
                    if len(ix.data) >= 16:
                        amount_data = ix.data[8:16]
                        amount = int.from_bytes(amount_data, 'little') / 1e9  # Convert from lamports
                        volume += amount

            # Calculate net token movement
            for acc_idx, pre_balance in pre_balances.items():
                post_balance = post_balances.get(acc_idx, 0.0)
                balance_change = abs(post_balance - pre_balance)
                
                # Only count significant changes (filter out dust)
                if balance_change > 0.000001:
                    volume = max(volume, balance_change)

            return volume

        except Exception as e:
            self.logger.error(f"Error parsing transaction volume: {str(e)}", exc_info=True)
            return 0.0

    def _decode_instruction_data(self, data: bytes) -> dict:
        """Decode Pump.fun instruction data"""
        try:
            if len(data) < 8:
                return {}
                
            discriminator = data[:8].hex()
            
            # Instruction type mapping
            INSTRUCTION_TYPES = {
                "7796a649dd114d": "buy",
                "41969aa66af11": "sell",
                "f99acc6e6e42": "create",
                "ae3d31a9b69": "migrate"
            }
            
            instruction_type = INSTRUCTION_TYPES.get(discriminator)
            if not instruction_type:
                return {}
                
            # Decode based on instruction type
            if instruction_type in ["buy", "sell"]:
                if len(data) >= 16:
                    amount = int.from_bytes(data[8:16], 'little')
                    return {
                        "type": instruction_type,
                        "amount": amount / 1e9  # Convert from lamports
                    }
                    
            elif instruction_type == "create":
                if len(data) >= 24:
                    supply = int.from_bytes(data[8:16], 'little')
                    decimals = int.from_bytes(data[16:17], 'little')
                    return {
                        "type": instruction_type,
                        "supply": supply,
                        "decimals": decimals
                    }
                    
            elif instruction_type == "migrate":
                return {"type": instruction_type}
                
            return {}
            
        except Exception as e:
            self.logger.error(f"Error decoding instruction data: {str(e)}")
            return {}

    async def _ensure_token_task(self, token_address: str):
        """Ensure a monitoring task exists for the token"""
        async with self.task_lock:
            if (token_address not in self.monitoring_tasks or 
                self.monitoring_tasks[token_address].done()):
                task = asyncio.create_task(
                    self._monitor_token(token_address),
                    name=f"monitor_{token_address}"
                )
                self.monitoring_tasks[token_address] = task

    async def _cleanup_tasks(self):
        """Clean up completed monitoring tasks"""
        async with self.task_lock:
            completed_tasks = [
                addr for addr, task in self.monitoring_tasks.items()
                if task.done()
            ]
            for addr in completed_tasks:
                # Handle any exceptions from the task
                try:
                    await self.monitoring_tasks[addr]
                except Exception as e:
                    logger.error(f"Task for {addr} failed: {e}")
                del self.monitoring_tasks[addr]
    
    @measure_performance('tier_monitoring')
    async def _monitor_token(self, token_address: str):
        """Monitor a token and update its metrics"""
        retry_count = 0
        while retry_count < self.MAX_RETRIES:
            try:
                async with get_async_session() as db:
                    # Get token from database
                    result = await db.execute(
                        select(MemeCoin).where(MemeCoin.address == token_address)
                    )
                    token = result.scalar_one_or_none()
                    
                    if not token:
                        logger.error(f"Token {token_address} not found in database")
                        return False

                    try:
                        async with MeasureBlockPerformance(f"holder_analysis_{token.address}"):
                            holders = await self._get_token_holders(token.address)
                            await self.holder_metrics_updater.update_holder_metrics(
                                db, 
                                token.address,
                                holders
                            )

                        async with MeasureBlockPerformance(f"transaction_processing_{token.address}"):
                            await self.transaction_processor.process_transactions(
                                token.address
                            )
                            
                        current_metrics = await self._get_current_metrics(
                            token_address=token_address,
                            bonding_curve_address=token.bonding_curve_address
                        )
                        
                        token.liquidity = current_metrics['liquidity']
                        token.volume_24h_usd = current_metrics['volume_24h']
                        token.holder_count = current_metrics['holder_count']
                        token.updated_at = datetime.utcnow()
                        
                        await self._update_tier_metrics(token, db)
                        await db.commit()
                        return True
                        
                    except Exception as e:
                        await db.rollback()
                        logger.error(f"Error getting metrics: {str(e)}")
                        raise

            except OperationalError as e:
                retry_count += 1
                if retry_count < self.MAX_RETRIES:
                    logger.warning(f"Database operation failed, attempt {retry_count} of {self.MAX_RETRIES}")
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue
                logger.error(f"Max retries reached for {token_address}")
                raise
            except Exception as e:
                logger.error(f"Error monitoring token {token_address}: {str(e)}")
                raise

    async def _get_token_holders(self, token_address: str) -> List[Dict]:
        """Get token holders using getTokenLargestAccounts"""
        try:
            mint_pubkey = Pubkey.from_string(token_address)
            
            # Get largest token accounts
            response = await self.rate_limiter.call(
                self.rpc_client.get_token_largest_accounts,
                mint_pubkey,
                commitment=Commitment("confirmed")
            )
            
            if not response or not response.value:
                return []

            holders = []
            for account in response.value:
                try:
                    # Get the UI amount directly - this is already in decimal format
                    if hasattr(account.amount, 'ui_amount'):
                        balance = float(account.amount.ui_amount)  # Use ui_amount for human-readable value
                    else:
                        # Fallback if ui_amount isn't available
                        balance = float(account.amount)
                    
                    holders.append({
                        'wallet': account.address,
                        'balance': balance,  # This will be the human-readable decimal amount
                        'decimals': getattr(account.amount, 'decimals', 0)
                    })
                except (KeyError, AttributeError) as e:
                    logger.debug(f"Error parsing account data: {e}")
                    continue

            return holders

        except Exception as e:
            logger.error(f"Error getting token holders for {token_address}: {e}")
            return []
        
    async def _update_tier_metrics(self, token: MemeCoin, db):
        """Update tier-specific metrics for the token"""
        try:
            result = await db.execute(
                select(TokenTier).where(TokenTier.token_id == token.id)
            )
            tier = result.scalar_one_or_none()
            
            if not tier:
                logger.warning(f"No tier found for token {token.address}")
                return

            if tier.current_tier == TierLevel.SIGNAL:
                tier.whale_holdings = await self._calculate_whale_holdings(token.address)
                tier.smart_money_flow = await self._calculate_smart_money_flow(token.address)
                tier.holder_growth_rate = await self._calculate_holder_growth_rate(token.address)
            
            tier.liquidity_level = token.liquidity
            tier.holder_count = token.holder_count
            tier.last_checked = datetime.utcnow()
            
            check_interval = self._get_tier_check_interval(tier.current_tier)
            tier.next_check_at = datetime.utcnow() + check_interval

            db.add(tier)

        except Exception as e:
            logger.error(f"Error updating tier metrics: {str(e)}")
            raise
        
    async def _calculate_whale_holdings(self, token_address: str) -> float:
        """Calculate percentage of supply held by whale wallets"""
        try:
            # Use rate limiter for RPC call
            response = await self.rate_limiter.call(
                self.rpc_client.get_token_largest_accounts,
                token_address
            )
            
            if not response or not response.value:
                return 0.0

            total_supply = sum(account.amount for account in response.value)
            if total_supply == 0:
                return 0.0

            whale_threshold = total_supply * 0.01
            whale_total = sum(
                account.amount for account in response.value 
                if account.amount > whale_threshold
            )

            return (whale_total / total_supply) * 100

        except Exception as e:
            self.logger.error(f"Error calculating whale holdings: {str(e)}")
            return 0.0

    async def _calculate_smart_money_flow(self, token_address: str) -> float:
        """Calculate net flow of tokens from/to known smart money wallets"""
        try:
            # Get recent transactions with rate limiter
            signatures = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                token_address,
                before="",  # Latest
                limit=1000
            )
            
            if not signatures.value:
                return 0.0

            # Prepare batch of transaction requests
            tx_requests = []
            for sig in signatures:
                tx_requests.append({
                    'func': self.rpc_client.get_confirmed_transaction,
                    'args': [sig.signature]
                })
            
            # Execute batch with rate limiter
            tx_responses = await self.rate_limiter.execute_batch(tx_requests, batch_size=10)
            
            inflow = 0.0
            outflow = 0.0
            
            for tx_response in tx_responses:
                if isinstance(tx_response, Exception):
                    continue
                    
                if not tx_response or not tx_response.meta:
                    continue

                # Analyze pre and post token balances
                for balance_change in tx_response.meta.pre_token_balances:
                    if self._is_smart_money_wallet(balance_change.owner):
                        pre_bal = balance_change.ui_token_amount.amount
                        post_bal = next(
                            (b.ui_token_amount.amount for b in tx_response.meta.post_token_balances 
                            if b.owner == balance_change.owner),
                            0
                        )
                        net_flow += (post_bal - pre_bal)

            # Calculate net flow ratio
            total_volume = inflow + outflow
            if total_volume > 0:
                return (inflow - outflow) / total_volume
            return 0.0

        except Exception as e:
            self.logger.error(f"Error analyzing smart money flow: {str(e)}")
            return 0.0
    
    async def _calculate_holder_growth_rate(self, token_address: str) -> float:
        """Calculate 24h holder count growth rate in percent"""
        try:
            current_holders = await self._get_current_holder_count(token_address)
            
            async with get_async_session() as db:
                day_ago = datetime.utcnow() - timedelta(days=1)
                result = await db.execute(
                    select(HolderSnapshot).where(
                        and_(
                            HolderSnapshot.token_address == token_address,
                            HolderSnapshot.timestamp > day_ago
                        )
                    ).order_by(HolderSnapshot.timestamp.asc())
                )
                snapshot = result.scalar_one_or_none()

                if not snapshot or not snapshot.holder_count or snapshot.holder_count == 0:
                    return 0.0

                growth_rate = ((current_holders - snapshot.holder_count) / snapshot.holder_count) * 100
                return growth_rate

        except Exception as e:
            logger.error(f"Error calculating holder growth rate: {str(e)}")
            return 0.0

    def _get_tier_check_interval(self, tier_level: TierLevel) -> timedelta:
        """Get check interval based on tier level"""
        intervals = {
            TierLevel.SIGNAL: timedelta(seconds=30),
            TierLevel.HOT: timedelta(minutes=5),
            TierLevel.WARM: timedelta(minutes=30),
            TierLevel.COLD: timedelta(hours=6),
            TierLevel.ARCHIVE: timedelta(days=1)
        }
        return intervals.get(tier_level, timedelta(hours=1))

    async def _get_current_metrics(self, token_address: str, bonding_curve_address: str) -> Dict:
        """Get current metrics for a token with rate limiting"""
        try:
            # Prepare requests for parallel execution
            requests = [
                {
                    'func': self._get_liquidity,
                    'args': [token_address, bonding_curve_address]
                },
                {
                    'func': self._get_24h_volume,
                    'args': [token_address, bonding_curve_address]
                },
                {
                    'func': self._get_holder_count,
                    'args': [token_address]
                }
            ]
            
            # Execute requests in parallel with rate limiting
            results = await self.rate_limiter.execute_batch(requests)
            
            return {
                'liquidity': float(results[0] or 0.0),
                'volume_24h': float(results[1] or 0.0),
                'holder_count': int(results[2] or 0),
                'updated_at': datetime.utcnow()
            }
                
        except Exception as e:
            logger.error(f"Error getting metrics for {token_address}: {str(e)}")
            return {
                'liquidity': 0.0,
                'volume_24h': 0.0,
                'holder_count': 0,
                'updated_at': datetime.utcnow()
            }
            
    async def _get_total_supply(self, token_address: str) -> int:
        try:
            supply_response = await self.rate_limiter.call(
                self.rpc_client.get_token_supply,
                token_address,
                commitment=Commitment("confirmed")
            )
            if not supply_response.value:
                return 0
            return supply_response.value.amount
        except Exception as e:
            self.logger.error(f"Error getting total supply: {str(e)}")
            return 0

    async def stop_monitoring(self):
        """Stop the monitoring system"""
        self.is_running = False
            
        # Cancel all running tasks
        async with self.task_lock:
            for task in self.monitoring_tasks.values():
                if not task.done():
                    task.cancel()
            
            await asyncio.gather(*self.monitoring_tasks.values(), return_exceptions=True)
        
        logger.info("Token monitoring system stopped")


    def _get_associated_bonding_curve_address(self, mint: Pubkey, program_id: Pubkey) -> tuple[Pubkey, int]:
        """Get the associated bonding curve address"""
        return Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint)],
            program_id
        )
        
    async def _get_token_accounts(self, mint: Pubkey) -> List:
        """Get all token accounts for a mint"""
        try:
            memcmp = {
                "offset": 0,
                "bytes": str(mint)
            }
            
            # Use rate limiter for RPC call
            response = await self.rate_limiter.call(
                self.rpc_client.get_program_accounts,
                self.token_program_id,
                filters=[
                    {"memcmp": memcmp},
                    {"dataSize": 165}
                ],
                commitment="confirmed",
                encoding="jsonParsed"
            )
            
            if not response or not hasattr(response, 'value'):
                return []
                
            return response.value or []
                
        except Exception as e:
            logger.error(f"Error getting token accounts: {str(e)}")
            return []
        
    async def _get_holder_count(self, token_address: str) -> int:
        """Get current holder count for token"""
        try:
            mint = Pubkey.from_string(token_address)
            accounts = await self._get_token_accounts_by_mint(mint)
            
            if not accounts:
                return 0

            # Count unique holders with non-zero balance
            unique_holders = set()
            for acc in accounts:
                try:
                    parsed_data = acc.account.data['parsed']['info']
                    balance = int(parsed_data['tokenAmount']['amount'])
                    if balance > 0:
                        owner = parsed_data['owner']
                        unique_holders.add(owner)
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug(f"Error parsing account data: {e}")
                    continue

            return len(unique_holders)

        except Exception as e:
            logger.error(f"Error getting holder count for {token_address}: {e}")
            return 0

    async def _analyze_smart_money_flow(self, token_address: str) -> float:
        """Analyze smart money movements"""
        try:
            # Get recent transactions
            mint = Pubkey.from_string(token_address)
            signatures = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                mint,
                limit=50,
                commitment=Commitment("confirmed")
            )
            
            if not signatures.value:
                return 0.0

            inflow = 0.0
            outflow = 0.0
            
            for sig in signatures.value:
                tx = await self.rate_limiter.call(
                    self.rpc_client.get_transaction,
                    sig.signature,
                    encoding="jsonParsed",
                    commitment=Commitment("confirmed"),
                    maxSupportedTransactionVersion=0
                )
                if not tx.value or not tx.value.meta:
                    continue
                    
                # Analyze pre and post token balances
                pre = tx.value.meta.pre_token_balances
                post = tx.value.meta.post_token_balances
                
                if pre and post:
                    for p_balance, n_balance in zip(pre, post):
                        if p_balance and n_balance:
                            change = float(n_balance.ui_token_amount.ui_amount or 0) - float(p_balance.ui_token_amount.ui_amount or 0)
                            if change > 0:
                                inflow += change
                            else:
                                outflow += abs(change)

            # Calculate net flow ratio
            total_volume = inflow + outflow
            if total_volume > 0:
                return (inflow - outflow) / total_volume
            return 0.0

        except Exception as e:
            logger.error(f"Error analyzing smart money for {token_address}: {e}")
            return 0.0

    async def _update_token_status(
        self,
        db: Session,
        token: TokenTier,
        meme_coin: MemeCoin,
        current_metrics: Dict,
        risk_score: float,
        alerts: List[str]
    ):
        """Update token status based on analysis results"""
        try:
            current_time = datetime.now(timezone.utc)

            # Update MemeCoin metrics
            meme_coin.liquidity = current_metrics['liquidity']
            meme_coin.volume_24h_usd = current_metrics['volume_24h']
            meme_coin.holder_count = current_metrics['holder_count']
            meme_coin.market_cap_usd = current_metrics['market_cap_usd']
            meme_coin.risk_score = risk_score

            # Update tier metrics
            token.tier_metrics = current_metrics
            token.tier_score = risk_score
            token.last_checked = current_time
            
            # Calculate metric changes
            if token.tier_metrics:
                old_metrics = token.tier_metrics
                token.liquidity_change_percent = ((current_metrics['liquidity'] - old_metrics.get('liquidity', 0)) 
                                                / old_metrics.get('liquidity', 1) * 100 if old_metrics.get('liquidity', 0) > 0 else 0)
                token.volume_change_percent = ((current_metrics['volume_24h'] - old_metrics.get('volume_24h', 0)) 
                                             / old_metrics.get('volume_24h', 1) * 100 if old_metrics.get('volume_24h', 0) > 0 else 0)
                token.holder_change_percent = ((current_metrics['holder_count'] - old_metrics.get('holder_count', 0)) 
                                            / old_metrics.get('holder_count', 1) * 100 if old_metrics.get('holder_count', 0) > 0 else 0)

            # Check for rug pull conditions
            is_rugged = any([
                token.liquidity_change_percent <= -70,  # 70% liquidity drop
                token.volume_change_percent >= 500,     # 5x volume spike
                token.holder_change_percent <= -30,     # 30% holder decrease
                current_metrics.get('smart_money_flow', 0) <= -0.4  # 40% smart money outflow
            ])

            # Update tier based on age and metrics
            new_tier = await self._determine_tier(token, current_metrics, is_rugged)
            
            # Handle tier transition
            if new_tier != token.current_tier:
                await self._handle_tier_transition(token, new_tier, current_time, risk_score, alerts, current_metrics)

            # Update check frequency based on tier and risk
            check_frequency = await self._get_check_frequency(new_tier, risk_score)
            token.check_frequency = check_frequency
            token.next_check_at = current_time + timedelta(seconds=check_frequency)

            db.flush()

        except Exception as e:
            logger.error(f"Error updating token status: {e}")
            raise

    async def _determine_tier(self, token: TokenTier, metrics: Dict, is_rugged: bool) -> str:
        """Determine the appropriate tier for a token based on its metrics and age"""
        if is_rugged:
            return TierLevel.RUGGED

        token_age = (datetime.now(timezone.utc) - token.created_at).days

        # Signal tier check (potential pump detection)
        if metrics.get('smart_money_flow', 0) > 0.6 and metrics.get('volume_24h', 0) > 0:
            return TierLevel.SIGNAL

        # Age-based tier determination
        if token_age <= 7:
            return TierLevel.HOT
        elif token_age <= 30:
            return TierLevel.WARM
        else:
            # Check for activity level for Cold vs Archive
            if metrics.get('volume_24h', 0) < 1 and metrics.get('holder_count', 0) < 10:
                return TierLevel.ARCHIVE
            return TierLevel.COLD

    async def _handle_tier_transition(
        self,
        token: TokenTier,
        new_tier: str,
        timestamp: datetime,
        risk_score: float,
        alerts: List[str],
        metrics: Dict
    ):
        """Handle the transition of a token from one tier to another"""
        # Add to tier history
        tier_history = token.tier_history or []
        tier_history.append({
            'tier': new_tier,
            'timestamp': timestamp.isoformat(),
            'reason': self._get_tier_transition_reason(token.current_tier, new_tier),
            'risk_score': risk_score,
            'alerts': alerts,
            'metrics': metrics
        })
        
        # Update token tier
        token.current_tier = new_tier
        token.tier_history = tier_history
        
        # Handle specific tier transitions
        if new_tier == TierLevel.RUGGED:
            token.is_monitoring_paused = True
            token.last_alert_at = timestamp
            token.alert_count = (token.alert_count or 0) + len(alerts)
            logger.warning(f"🚨 Token {token.token_address} marked as RUGGED")
            for alert in alerts:
                logger.warning(f"Alert: {alert}")
        
        elif new_tier == TierLevel.SIGNAL:
            token.last_alert_at = timestamp
            token.alert_count = (token.alert_count or 0) + 1
            logger.info(f"🔔 Token {token.token_address} moved to SIGNAL tier")

    def _get_tier_transition_reason(self, old_tier: str, new_tier: str) -> str:
        """Get a descriptive reason for the tier transition"""
        transition_reasons = {
            (TierLevel.HOT, TierLevel.WARM): "Token matured past 7 days",
            (TierLevel.WARM, TierLevel.COLD): "Token matured past 30 days",
            (TierLevel.COLD, TierLevel.ARCHIVE): "Token became inactive",
            (TierLevel.ARCHIVE, TierLevel.COLD): "Token activity resumed",
            (TierLevel.SIGNAL, TierLevel.HOT): "Pump signal expired",
            ('*', TierLevel.SIGNAL): "Potential pump detected",
            ('*', TierLevel.RUGGED): "Rug pull detected"
        }
        
        # Check for exact match
        reason = transition_reasons.get((old_tier, new_tier))
        if reason:
            return reason
            
        # Check for wildcard matches
        reason = transition_reasons.get(('*', new_tier))
        if reason:
            return reason
            
        return f"Moved from {old_tier} to {new_tier}"

    async def _get_check_frequency(self, tier: str, risk_score: float) -> int:
        """Get the check frequency in seconds based on tier and risk score"""
        base_frequencies = {
            TierLevel.SIGNAL: 15,    # 15 seconds
            TierLevel.HOT: 60,       # 1 minute
            TierLevel.WARM: 1800,    # 30 minutes
            TierLevel.COLD: 21600,   # 6 hours
            TierLevel.ARCHIVE: 86400 # 24 hours
        }
        
        # Get base frequency for tier
        base_freq = base_frequencies.get(tier, 3600)  # Default 1 hour
        
        # Adjust based on risk score (higher risk = more frequent checks)
        if risk_score > 0.8:
            return max(15, base_freq // 4)  # At least every 15 seconds
        elif risk_score > 0.6:
            return max(30, base_freq // 2)  # At least every 30 seconds
        elif risk_score > 0.4:
            return base_freq
        else:
            return base_freq * 2  # Less frequent for lower risk
        
    async def _get_current_holder_count(self, token_address: str) -> int:
        try:
            response = await self.rate_limiter.call(
                self.rpc_client.get_token_accounts_by_owner,
                token_address,
                {"programId": settings['SYSTEM_TOKEN_PROGRAM']},
                commitment=Commitment("confirmed"),
                encoding="jsonParsed"
            )
            
            if not response or not response.value:
                return 0

            holder_count = sum(
                1 for account in response.value 
                if account.account.data.parsed['info']['tokenAmount']['uiAmount'] > 0
            )

            return holder_count
        except Exception as e:
            self.logger.error(f"Error getting current holder count: {str(e)}")
            return 0

    async def _is_smart_money_wallet(self, wallet_address: str) -> bool:
        """Check if a wallet is considered 'smart money' based on historical performance"""
        try:
            async with get_async_session() as db:
                result = await db.execute(
                    select(WalletAnalysis)
                    .where(WalletAnalysis.wallet_address == wallet_address)
                    .order_by(WalletAnalysis.analysis_timestamp.desc())
                )
                wallet_performance = result.scalar_one_or_none()

                if not wallet_performance:
                    return False

                return (
                    wallet_performance.profitable_exit_rate > 0.7 and
                    wallet_performance.avg_holding_time > timedelta(days=2) and
                    wallet_performance.total_volume > 1000
                )

        except Exception as e:
            logger.error(f"Error checking smart money wallet: {str(e)}")
            return False