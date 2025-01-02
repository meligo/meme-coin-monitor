import asyncio
import base64
import logging
from typing import Any, Dict, List, Optional

from base58 import b58decode
from solana.exceptions import SolanaRpcException
from solana.rpc.commitment import Commitment
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey

from src.config.database import get_db
from src.config.settings import settings
from src.core.models.meme_coin import MemeCoin
from src.services.pump_fun.scanner import PumpFunScanner
from src.utils.rate_limiter import GlobalRateLimiter

logger = logging.getLogger(__name__)

class BacktestScanner(PumpFunScanner):
    def __init__(self, batch_size: int = 20):
        super().__init__()
        self.batch_size = batch_size
        self.pump_program = Pubkey.from_string(settings['PUMP_PROGRAM'])
        self.max_retries = 3
        self.retry_delay = 2
        self.processed_tokens = 0
        self.total_tokens_found = 0
        self.rate_limiter = GlobalRateLimiter()

        logger.info("Initializing Pump.fun Historical Scanner")

    async def retry_with_backoff(self, func, *args, **kwargs) -> Any:
        """Retry function with exponential backoff"""
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                wait_time = self.retry_delay * (2 ** attempt)
                logger.warning(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

    async def get_signatures(self, before: Optional[str] = None) -> List[str]:
        """Get transaction signatures for the program"""
        try:
            response = await self.rate_limiter.call(
                self.rpc_client.get_signatures_for_address,
                self.pump_program,
                before=before,
                limit=100,
                commitment=Commitment("confirmed")
            )
            return [sig.signature for sig in response.value] if response.value else []
        except Exception as e:
            logger.error(f"Error getting signatures: {e}")
            return []

    async def scan_historical_tokens(self) -> None:
        """Scan all historical tokens from pump.fun with on-the-fly processing"""
        logger.info("Starting incremental historical token scan")
        
        try:
            last_signature = None
            total_signatures_processed = 0
            
            while True:
                # Get batch of signatures
                signatures = await self.get_signatures(before=last_signature)
                if not signatures:
                    break
                    
                total_signatures_processed += len(signatures)
                logger.info(f"Processing batch of {len(signatures)} signatures (Total processed: {total_signatures_processed})")
                
                # Process this batch of signatures immediately
                try:
                    await self.process_transaction_batch(signatures)
                    logger.info(f"Processed batch (Found {self.total_tokens_found} tokens, "
                              f"Successfully processed {self.processed_tokens})")
                except Exception as e:
                    logger.error(f"Error processing batch: {e}")
                
                # Update last signature for next iteration
                last_signature = signatures[-1]
                await asyncio.sleep(0.5)  # Rate limiting

            logger.info(f"Historical scan complete. "
                       f"Processed {total_signatures_processed} total signatures, "
                       f"Found {self.total_tokens_found} tokens, "
                       f"Successfully processed {self.processed_tokens} tokens")

        except Exception as e:
            logger.error(f"Error in historical scan: {e}")
            raise

    async def process_transaction_batch(self, signatures: List[str]) -> None:
        """Process a batch of transactions with rate limiting"""
        # Prepare batch requests
        batch_requests = [
            {
                'func': self.rpc_client.get_transaction,
                'args': [sig],
                'kwargs': {
                    'encoding': "base64",
                    'commitment': Commitment("confirmed"),
                    'max_supported_transaction_version': 0
                }
            }
            for sig in signatures
        ]
        
        # Execute batch with rate limiting
        responses = await self.rate_limiter.execute_batch(
            batch_requests,
            batch_size=20  # Process in smaller sub-batches
        )
        
        # Process responses
        for tx_response in responses:
            try:
                if isinstance(tx_response, Exception):
                    logger.error(f"Error getting transaction: {tx_response}")
                    continue
                    
                if not tx_response or not tx_response.value:
                    continue

                # Process transaction
                token_found = await self.process_transaction(tx_response.value)
                if token_found:
                    self.total_tokens_found += 1
                    
            except Exception as e:
                logger.error(f"Error processing transaction: {e}")
                continue
                
            # Small delay between transactions
            await asyncio.sleep(0.01)

    async def process_transaction(self, tx_data: Dict) -> bool:
        """Process a single transaction"""
        try:
            # Check if this is a token creation transaction
            if not self._is_token_creation(tx_data):
                return False

            found_token = False
            # Extract post token accounts
            for account_key in tx_data.get('meta', {}).get('postTokenBalances', []):
                try:
                    account_info = await self.retry_with_backoff(
                        self.rpc_client.get_account_info,
                        Pubkey.from_string(account_key['mint']),
                        commitment=Commitment("confirmed")
                    )

                    if not account_info.value:
                        continue

                    # Decode and analyze token account
                    data = base64.b64decode(account_info.value.data[0])
                    if len(data) < 8 or data[:8] != self.TOKEN_DISCRIMINATOR:
                        continue

                    # Analyze token account
                    token_data = await self.analyze_token_account(
                        Pubkey.from_string(account_key['mint']),
                        data
                    )
                    
                    if token_data:
                        found_token = True
                        await self.token_processor.add_token(token_data)
                        self.processed_tokens += 1
                        logger.info(f"Successfully processed token: {token_data.get('address')} "
                                  f"(Total processed: {self.processed_tokens})")

                except Exception as e:
                    logger.error(f"Error processing account: {e}")
                    continue

            return found_token

        except Exception as e:
            logger.error(f"Error in transaction processing: {e}")
            return False
        
    async def process_token(self, token_address: str) -> bool:
        """Process a single token address with historical data"""
        try:
            # Get token from database first
            db = next(get_db())
            try:
                token = db.query(MemeCoin).filter(
                    MemeCoin.address == token_address
                ).first()
                
                if not token:
                    logger.warning(f"Token {token_address} not found in database")
                    return False

                # Convert address to Pubkey safely
                try:
                    pubkey = Pubkey.from_string(token_address)
                except Exception as e:
                    logger.error(f"Invalid token address format: {token_address} - {e}")
                    return False

                # Get account info
                account_info = await self.retry_with_backoff(
                    self.rpc_client.get_account_info,
                    pubkey,
                    commitment=Commitment("confirmed")
                )

                if not account_info or not account_info.value:
                    logger.error(f"No account info found for {token_address}")
                    return False

                # Decode and analyze token account
                try:
                    data = base64.b64decode(account_info.value.data[0])
                except Exception as e:
                    logger.error(f"Error decoding account data: {e}")
                    return False

                if len(data) < 8 or data[:8] != self.TOKEN_DISCRIMINATOR:
                    logger.error(f"Invalid token discriminator for {token_address}")
                    return False

                # Create token data structure
                token_data = {
                    'address': token_address,
                    'name': token.name,
                    'symbol': token.symbol,
                    'creation_date': token.launch_date
                }

                # Process using token processor
                await self.token_processor.add_token(token_data)
                self.processed_tokens += 1
                
                logger.info(f"Successfully processed historical data for token: {token_address}")
                return True

            except Exception as e:
                logger.error(f"Error processing token {token_address}: {e}")
                return False
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error in process_token: {e}")
            return False