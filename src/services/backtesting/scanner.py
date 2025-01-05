import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from sqlalchemy import and_, insert, select

from src.config.database import db, get_db, get_db_session
from src.config.settings import settings
from src.core.models.meme_coin import (
    HolderSnapshot,
    MemeCoin,
    TokenMetadata,
    TokenPrice,
)
from src.services.pump_fun.scanner import PumpFunScanner
from src.utils.rate_limiter import GlobalRateLimiter
from src.utils.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

class BacktestScanner(PumpFunScanner):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            batch_size = kwargs.pop('batch_size', None)
            cls._instance = super().__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
            cls._instance._batch_size = batch_size
        return cls._instance

    def __init__(self, batch_size=100):
        if not hasattr(self, '_initialized') or not self._initialized:
            self.batch_size = getattr(self, '_batch_size', batch_size)
            super().__init__()
            logger.info(f"Initializing Pump.fun Historical Scanner with batch_size={self.batch_size}")
            self._initialized = True

        self.rpc_manager = RPCManager()
        self.pump_program = Pubkey.from_string(settings.pump_program)
        self.max_retries = 3
        self.retry_delay = 2
        self.processed_tokens = 0
        self.total_tokens_found = 0
        self.rate_limiter = GlobalRateLimiter()
        self.client = AsyncClient(settings.rpc_endpoints[0].url)

    async def get_price_history(self, token_address: str, days: int = 30) -> List[Dict]:
        """Get historical price data for a token"""
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=days)
            
            async with get_db() as db:
                result = await db.execute(
                    select(TokenPrice)
                    .where(
                        and_(
                            TokenPrice.token_address == token_address,
                            TokenPrice.timestamp >= start_time,
                            TokenPrice.timestamp <= end_time
                        )
                    )
                    .order_by(TokenPrice.timestamp)
                )
                prices = result.scalars().all()
                
                return [
                    {
                        'timestamp': price.timestamp,
                        'price': float(price.price),
                        'volume': float(price.volume_24h)
                    }
                    for price in prices
                ]

        except Exception as e:
            logger.error(f"Error getting price history for {token_address}: {e}")
            return []

    async def analyze_transaction_patterns(self, token_address: str) -> Dict:
        """Analyze transaction patterns for suspicious activity"""
        try:
            # Get recent transactions
            signatures = await self.client.get_signatures_for_address(
                Pubkey.from_string(token_address),
                limit=1000
            )
            
            if not signatures.value:
                return {'suspicious_patterns': False, 'confidence': 0}

            # Analyze patterns
            large_txs = 0
            rapid_txs = 0
            prev_time = None
            
            for sig in signatures.value:
                # Check transaction size
                if sig.slot and sig.slot > 1_000_000:  # Large transaction threshold
                    large_txs += 1
                    
                # Check transaction timing
                if prev_time and sig.block_time:
                    time_diff = prev_time - sig.block_time
                    if time_diff < 2:  # Less than 2 seconds between transactions
                        rapid_txs += 1
                        
                prev_time = sig.block_time

            # Calculate suspicion score
            total_txs = len(signatures.value)
            large_tx_ratio = large_txs / total_txs if total_txs > 0 else 0
            rapid_tx_ratio = rapid_txs / total_txs if total_txs > 0 else 0
            
            suspicion_score = (large_tx_ratio * 0.5 + rapid_tx_ratio * 0.5) * 100
            
            return {
                'suspicious_patterns': suspicion_score > 50,
                'confidence': suspicion_score,
                'metrics': {
                    'large_transactions': large_txs,
                    'rapid_transactions': rapid_txs,
                    'total_transactions': total_txs
                }
            }

        except Exception as e:
            logger.error(f"Error analyzing transaction patterns for {token_address}: {e}")
            return {'suspicious_patterns': False, 'confidence': 0}

    async def get_historical_metrics(self, token_address: str) -> Dict:
        """Get historical liquidity, volume, and holder metrics"""
        try:
            async with get_db() as db:
                # Get holder snapshots
                holder_result = await db.execute(
                    select(HolderSnapshot)
                    .where(HolderSnapshot.token_address == token_address)
                    .order_by(HolderSnapshot.timestamp.desc())
                    .limit(30)  # Last 30 snapshots
                )
                holder_snapshots = holder_result.scalars().all()
                
                # Get price/volume data
                price_result = await db.execute(
                    select(TokenPrice)
                    .where(TokenPrice.token_address == token_address)
                    .order_by(TokenPrice.timestamp.desc())
                    .limit(30)  # Last 30 days
                )
                price_data = price_result.scalars().all()
                
                return {
                    'holder_metrics': [
                        {
                            'timestamp': snapshot.timestamp,
                            'total_holders': snapshot.total_holders,
                            'active_holders': snapshot.active_holders
                        }
                        for snapshot in holder_snapshots
                    ],
                    'market_metrics': [
                        {
                            'timestamp': data.timestamp,
                            'price': float(data.price),
                            'volume': float(data.volume_24h),
                            'liquidity': float(data.liquidity)
                        }
                        for data in price_data
                    ]
                }

        except Exception as e:
            logger.error(f"Error getting historical metrics for {token_address}: {e}")
            return {'holder_metrics': [], 'market_metrics': []}

    async def save_historical_data(self, token_data: Dict):
        """Save historical analysis data to database"""
        try:
            async with get_db() as db:
                # Prepare token metrics
                metrics_data = {
                    'token_address': token_data['address'],
                    'timestamp': datetime.now(timezone.utc),
                    'price': Decimal(str(token_data.get('current_price', 0))),
                    'volume_24h': Decimal(str(token_data.get('volume_24h', 0))),
                    'liquidity': Decimal(str(token_data.get('liquidity', 0))),
                    'holder_count': token_data.get('holder_count', 0)
                }

                # Insert price metrics
                price_stmt = insert(TokenPrice).values(metrics_data)
                await db.execute(price_stmt)

                # Prepare and insert holder snapshot
                holder_data = {
                    'token_address': token_data['address'],
                    'timestamp': datetime.now(timezone.utc),
                    'total_holders': token_data.get('holder_count', 0),
                    'active_holders': token_data.get('active_holders', 0),
                    'top_holders': token_data.get('top_holders', [])
                }
                
                holder_stmt = insert(HolderSnapshot).values(holder_data)
                await db.execute(holder_stmt)

                # Update token metadata with latest analysis
                metadata_update = {
                    'risk_score': token_data.get('risk_analysis', {}).get('risk_score', 0),
                    'is_suspicious': token_data.get('suspicious_patterns', False),
                    'last_analyzed': datetime.now(timezone.utc)
                }
                
                await db.execute(
                    update(TokenMetadata)
                    .where(TokenMetadata.token_address == token_data['address'])
                    .values(metadata_update)
                )

                await db.commit()

        except Exception as e:
            logger.error(f"Error saving historical data: {e}")
            await db.rollback()
            raise

    async def get_top_holders(self, token_address: str) -> List[Dict]:
        """Get detailed information about top token holders"""
        try:
            # Get token holder accounts
            accounts = await self.client.get_token_largest_accounts(
                Pubkey.from_string(token_address)
            )
            
            if not accounts.value:
                return []

            holders = []
            total_supply = Decimal('0')
            
            # Calculate total supply first
            for account in accounts.value:
                total_supply += Decimal(str(account.amount))

            # Get detailed holder information
            for account in accounts.value:
                try:
                    account_info = await self.client.get_account_info(
                        Pubkey.from_string(account.address)
                    )
                    
                    if not account_info.value:
                        continue

                    percentage = (Decimal(str(account.amount)) / total_supply * 100) \
                        if total_supply > 0 else Decimal('0')
                    
                    holders.append({
                        'address': account.address,
                        'balance': float(account.amount),
                        'percentage': float(percentage.quantize(Decimal('0.01')))
                    })

                except Exception as e:
                    logger.error(f"Error getting holder info for {account.address}: {e}")
                    continue

            # Sort by balance descending
            return sorted(holders, key=lambda x: x['balance'], reverse=True)

        except Exception as e:
            logger.error(f"Error getting top holders for {token_address}: {e}")
            return []

    async def get_historical_high(self, token_address: str) -> float:
        """Get the historical high price for a token"""
        try:
            async with get_db() as db:
                result = await db.execute(
                    select(TokenPrice.price)
                    .where(TokenPrice.token_address == token_address)
                    .order_by(TokenPrice.price.desc())
                    .limit(1)
                )
                highest_price = result.scalar_one_or_none()
                return float(highest_price) if highest_price else 0

        except Exception as e:
            logger.error(f"Error getting historical high for {token_address}: {e}")
            return 0
        
    async def get_token_details(self, token_address: str) -> Optional[Dict]:
        """Get detailed token information"""
        try:
            # Get account info
            account_info = await self.retry_with_backoff(
                self.client.get_account_info,
                Pubkey.from_string(token_address)
            )

            if not account_info.value:
                return None

            # Parse token state
            data = base64.b64decode(account_info.value.data[0])
            if len(data) < 8 or data[:8] != base64.b64decode(self.TOKEN_DISCRIMINATOR):
                return None

            token_state = self.TOKEN_STATE.parse(data)
            if not token_state.is_initialized:
                return None

            # Get metadata
            token_info = await self._get_token_metadata(token_address)

            # Get bonding curve data
            bonding_curve_address, _ = self.get_associated_bonding_curve_address(
                token_address,
                self.pump_program
            )
            curve_state = await self.get_bonding_curve_state(bonding_curve_address)
            if not curve_state:
                return None

            # Get token metrics
            metrics = await self.token_metrics.get_all_metrics(
                str(token_address),
                curve_state
            )

            return {
                'address': str(token_address),
                'name': token_info.get('name', f"Unknown Token {str(token_address)[:8]}"),
                'symbol': token_info.get('symbol', 'UNKNOWN'),
                'bonding_curve_address': str(bonding_curve_address),
                'creation_date': datetime.fromtimestamp(
                    account_info.value.block_time or 0,
                    tz=timezone.utc
                ),
                'market_cap_usd': metrics.get('market_cap_usd', 0),
                'volume_24h': metrics.get('volume_24h', 0),
                'holder_count': metrics.get('holder_count', 0),
                'curve_data': {
                    'virtual_token_reserves': token_state.virtual_token_reserves,
                    'virtual_sol_reserves': token_state.virtual_sol_reserves,
                    'real_token_reserves': token_state.real_token_reserves,
                    'real_sol_reserves': token_state.real_sol_reserves,
                    'token_total_supply': token_state.token_total_supply
                }
            }

        except Exception as e:
            logger.error(f"Error getting token details for {token_address}: {e}")
            return None
        
    async def scan_token_batch(self, size: int) -> List[Dict]:
        """Scan a batch of historical tokens - Now integrated with new workflow"""
        try:
            start_time = datetime.now(timezone.utc) - timedelta(days=1)
            signatures = await self.fetch_all_signatures(start_time)
            
            batch_size = min(size, len(signatures))
            current_batch = signatures[:batch_size]
            
            tokens = await self.process_signature_batch(current_batch)
            if tokens:
                await self.process_token_batch(tokens)
                
            return tokens

        except Exception as e:
            logger.error(f"Error in scan_token_batch: {e}")
            return []
        
    async def process_token_batch(self, tokens: List[str]):
        """Process a batch of tokens with rate limiting"""
        try:
            for token_address in tokens:
                try:
                    # Get token details
                    token_data = await self.get_token_details(token_address)
                    if not token_data:
                        continue

                    # Enhance with additional analysis
                    token_data.update({
                        'price_history': await self.get_price_history(token_address),
                        'risk_analysis': await self.calculate_rug_risk(token_data),
                        'suspicious_patterns': await self.analyze_transaction_patterns(token_address),
                        'historical_metrics': await self.get_historical_metrics(token_address)
                    })

                    # Save historical data
                    await self.save_historical_data(token_data)

                    # Process through token processor
                    await self.token_processor.add_token(token_data)
                    
                    logger.info(f"Processed token {token_address}")
                    
                except Exception as e:
                    logger.error(f"Error processing token {token_address}: {e}")
                    continue

                # Rate limiting
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Error in batch processing: {e}")
            raise

    async def retry_with_backoff(self, func, *args, **kwargs):
        """
        Retry an async operation with exponential backoff
        
        Args:
            func: Async function to retry
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function
                
        Returns:
            The result of the function if successful
                
        Raises:
            Exception: The last error encountered after retries
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                if not callable(func):
                    logger.error("retry_with_backoff requires a callable function")
                    raise ValueError("Function must be callable")

                result = await func(*args, **kwargs)
                return result

            except Exception as error:
                last_error = error
                if attempt == self.max_retries - 1:
                    logger.error(f"Final retry attempt failed: {str(error)}")
                    raise

                delay = self.retry_delay * (2 ** attempt)
                logger.warning(
                    f"Attempt {attempt + 1}/{self.max_retries} failed: {str(error)}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

        raise last_error
    
    async def fetch_all_signatures(self, start_time: Optional[datetime] = None) -> List[str]:
        """
        Fetch signatures for program transactions since the given start time
        
        Args:
            start_time: Optional timestamp to start fetching from
            
        Returns:
            List of transaction signatures
        """
        signatures = []
        last_signature = None

        try:
            logger.info("going to fetch all signatures")
            while True:
                # Get batch of signatures
                response = await self.retry_with_backoff(
                    self.client.get_signatures_for_address,
                    self.pump_program,
                    before=last_signature,
                    limit=1000
                )

                if not response.value:
                    logger.info("not response.value")
                    break

                # Process each signature in response
                for sig in response.value:
                    if start_time and sig.block_time:
                        sig_time = datetime.fromtimestamp(sig.block_time, tz=timezone.utc)
                        if sig_time < start_time:
                            return signatures
                    logger.info(f"signature append {sig.signature}")
                    signatures.append(sig.signature)

                if not signatures:
                    logger.info("not signatures")
                    break

                # Prepare for next batch
                last_signature = signatures[-1]
                await asyncio.sleep(0.1)  # Rate limit between batches

            return signatures

        except Exception as error:
            logger.error(f"Failed to fetch signatures: {error}")
            return []

    async def get_transaction_details(self, signature: str) -> Optional[Dict]:
        """Get detailed transaction information"""
        try:
            tx = await self.retry_with_backoff(
                self.client.get_transaction(
                    signature,
                    commitment=Commitment("confirmed")
                )
            )
            
            if not tx.value or not tx.value.transaction.meta:
                return None
                
            return {
                'signature': signature,
                'block_time': tx.value.block_time,
                'slot': tx.value.slot,
                'logs': tx.value.transaction.meta.log_messages,
                'pre_balances': tx.value.transaction.meta.pre_balances,
                'post_balances': tx.value.transaction.meta.post_balances,
                'pre_token_balances': tx.value.transaction.meta.pre_token_balances,
                'post_token_balances': tx.value.transaction.meta.post_token_balances
            }
            
        except Exception as e:
            logger.error(f"Error getting transaction details for {signature}: {e}")
            return None

    async def identify_instruction_type(self, logs: List[str]) -> str:
        """Identify the type of pump.fun instruction from transaction logs"""
        try:
            for log in logs:
                if "Instruction: Create" in log:
                    return "CREATE"
                elif "Instruction: Buy" in log:
                    return "BUY"
                elif "Instruction: Sell" in log:
                    return "SELL"
                elif "Instruction: AddLiquidity" in log:
                    return "ADD_LIQUIDITY"
                elif "Instruction: RemoveLiquidity" in log:
                    return "REMOVE_LIQUIDITY"
            return "UNKNOWN"
            
        except Exception as e:
            logger.error(f"Error identifying instruction type: {e}")
            return "ERROR"

    async def process_signature_batch(self, signatures: List[str]) -> List[Dict]:
        """Process a batch of transaction signatures"""
        results = []
        for signature in signatures:
            try:
                tx_details = await self.get_transaction_details(signature)
                if not tx_details:
                    continue
                    
                # Identify instruction type
                instruction_type = await self.identify_instruction_type(tx_details['logs'])
                
                # For CREATE instructions, extract token data
                if instruction_type == "CREATE":
                    # Extract newly created token from post balances
                    new_tokens = [
                        balance.mint 
                        for balance in tx_details['post_token_balances']
                        if not any(
                            pre_bal.mint == balance.mint 
                            for pre_bal in tx_details['pre_token_balances']
                        )
                    ]
                    
                    if new_tokens:
                        results.extend(new_tokens)
                
                # Rate limiting
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error processing signature {signature}: {e}")
                continue
                
        return results

    async def validate_token_data(self, token_data: Dict) -> bool:
        """Validate token data structure and required fields"""
        try:
            required_fields = [
                'address', 
                'curve_data', 
                'holder_count',
                'market_cap_usd',
                'volume_24h'
            ]
            
            # Check required fields
            for field in required_fields:
                if field not in token_data:
                    logger.warning(f"Missing required field: {field}")
                    return False
                    
            # Validate curve data
            curve_data = token_data.get('curve_data', {})
            required_curve_fields = [
                'virtual_token_reserves',
                'virtual_sol_reserves',
                'real_token_reserves',
                'real_sol_reserves'
            ]
            
            for field in required_curve_fields:
                if field not in curve_data:
                    logger.warning(f"Missing required curve data field: {field}")
                    return False
                    
            return True
            
        except Exception as e:
            logger.error(f"Error validating token data: {e}")
            return False

    async def cleanup(self):
        """Cleanup resources"""
        try:
            if hasattr(self, 'client'):
                await self.client.close()
            
            if self.token_processor:
                await self.token_processor.stop()
                
            logger.info("Cleanup completed successfully")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise
    async def scan_historical_tokens(self):
        """Start historical scanning process with proper backfilling"""
        try:
            logger.info("Starting historical token scan...")
            
            # Get start time for scanning (last 3 days by default)
            start_time = datetime.now(timezone.utc) - timedelta(days=1)
            logger.info(f"starttime: {start_time}")
            # Get all signatures since start_time
            signatures = await self.fetch_all_signatures(start_time)
            logger.info(f"Found {len(signatures)} signatures to process")
            
            # Process signatures in batches
            batch_size = 50
            total_processed = 0
            
            for i in range(0, len(signatures), batch_size):
                batch = signatures[i:i + batch_size]
                tokens = await self.process_signature_batch(batch)
                
                if tokens:
                    await self.process_token_batch(tokens)
                    total_processed += len(tokens)
                    logger.info(f"Processed {total_processed} tokens so far...")
                
                await asyncio.sleep(0.5)  # Rate limiting between batches
            
            logger.info(f"Historical scan complete. Total tokens processed: {total_processed}")
            
        except Exception as e:
            logger.error(f"Error in historical scan: {e}")
            raise

    async def update_existing_tokens(self):
        """Update all existing tokens in database"""
        try:
            async with get_db_session() as db:
                # Get all tokens from database
                result = await db.execute(
                    select(MemeCoin.address)
                    .where(MemeCoin.is_active == True)
                )
                tokens = [str(row[0]) for row in result.fetchall()]
                
            logger.info(f"Found {len(tokens)} existing tokens to update")
            await self.process_token_batch(tokens)
            
        except Exception as e:
            logger.error(f"Error updating existing tokens: {e}")
            raise

    async def calculate_rug_risk(self, token_data: Dict) -> Dict:
        """Calculate detailed rug pull risk indicators"""
        try:
            risk_score = 0
            risk_factors = {
                'high_concentration': False,
                'low_liquidity': False,
                'suspicious_activity': False,
                'high_sell_tax': False,
                'contract_risk': False
            }
            
            # Check holder concentration
            top_holders = await self.get_top_holders(token_data['address'])
            if top_holders and top_holders[0]['percentage'] > 80:
                risk_factors['high_concentration'] = True
                risk_score += 30
                
            # Check liquidity
            curve_data = token_data.get('curve_data', {})
            total_liquidity = curve_data.get('real_sol_reserves', 0)
            mcap = token_data.get('market_cap_usd', 0)
            
            if mcap > 0 and (total_liquidity / mcap) < 0.05:
                risk_factors['low_liquidity'] = True
                risk_score += 25
                
            # Check trading patterns
            patterns = await self.analyze_transaction_patterns(token_data['address'])
            if patterns.get('suspicious_patterns', False):
                risk_factors['suspicious_activity'] = True
                risk_score += patterns.get('confidence', 0) * 0.2
                
            # Check metrics volatility
            metrics = await self.get_historical_metrics(token_data['address'])
            if metrics:
                market_data = metrics.get('market_metrics', [])
                if market_data:
                    volatility = self._calculate_volatility(market_data)
                    if volatility > 200:  # More than 200% daily volatility
                        risk_score += 15
            
            return {
                'risk_score': min(risk_score, 100),
                'risk_factors': risk_factors,
                'analysis_time': datetime.now(timezone.utc)
            }
            
        except Exception as e:
            logger.error(f"Error calculating rug risk: {e}")
            return {
                'risk_score': 0,
                'risk_factors': risk_factors,
                'error': str(e)
            }

    def _calculate_volatility(self, market_data: List[Dict]) -> float:
        """Calculate price volatility from market data"""
        try:
            if len(market_data) < 2:
                return 0
                
            prices = [d['price'] for d in market_data]
            changes = []
            
            for i in range(1, len(prices)):
                if prices[i-1] > 0:
                    pct_change = abs((prices[i] - prices[i-1]) / prices[i-1] * 100)
                    changes.append(pct_change)
                    
            return sum(changes) / len(changes) if changes else 0
            
        except Exception as e:
            logger.error(f"Error calculating volatility: {e}")
            return 0

    async def start(self):
        """Start the historical scanner"""
        try:
            logger.info("Starting historical scanner...")
            
            # First update existing tokens
            await self.update_existing_tokens()
            
            # Then scan for new tokens
            await self.scan_historical_tokens()
            
        except Exception as e:
            logger.error(f"Error in scanner start: {e}")
            raise
        finally:
            await self.cleanup()