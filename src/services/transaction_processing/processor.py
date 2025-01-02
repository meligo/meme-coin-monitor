import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import base58
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.config.database import Base, engine, get_db
from src.config.settings import settings
from src.core.models import MemeCoin, TokenTier
from src.core.models.wallet_analysis import WalletTransaction
from src.services.monitoring.performance import measure_performance
from src.utils.rate_limiter import GlobalRateLimiter

logger = logging.getLogger(__name__)

class TransactionProcessor:
    def __init__(self, rpc_client):
        self.rpc_client = rpc_client
        self.batch_size = 100
        self.token_program_id = Pubkey.from_string(settings['SYSTEM_TOKEN_PROGRAM'])
        self.rate_limiter = GlobalRateLimiter()


    def _parse_transaction(self, tx_data, token_id: int) -> Optional[dict]:
        """Parse transaction data into database format"""
        try:
            # Change from using .get() to direct property access
            if not hasattr(tx_data, 'meta') or not tx_data.meta or not tx_data.transaction:
                return None

            # Extract basic transaction info
            return {
                'token_id': token_id,
                'timestamp': datetime.fromtimestamp(
                    tx_data.block_time if hasattr(tx_data, 'block_time') else 0,
                    tz=timezone.utc
                ),
                'transaction_type': self._determine_transaction_type(tx_data),
                'amount': self._calculate_transaction_amount(tx_data),
                'wallet_address': self._extract_wallet_address(tx_data),
                'price': self._calculate_transaction_price(tx_data),
            }

        except Exception as e:
            logger.error(f"Error parsing transaction: {e}")
            return None

    @measure_performance('transaction_processing')
    async def process_transactions(self, token_address: str) -> None:
        """Process transactions efficiently using batch processing"""
        db = next(get_db())
        try:
            # Get token first to ensure it exists
            token = db.query(MemeCoin).filter(
                MemeCoin.address == token_address
            ).first()
            
            if not token:
                logger.error(f"Token {token_address} not found in database")
                return

            # Get latest processed transaction
            latest_tx = db.execute(
                select(WalletTransaction)
                .filter(WalletTransaction.token_id == token.id)
                .order_by(WalletTransaction.timestamp.desc())
                .limit(1)
            ).scalar_one_or_none()
            
            start_time = latest_tx.timestamp if latest_tx else (
                datetime.now(timezone.utc) - timedelta(days=7)
            )

            current_time = datetime.now(timezone.utc)
            pubkey = Pubkey.from_string(token_address)
            
            while start_time < current_time:
                end_time = min(start_time + timedelta(hours=1), current_time)
                
                # Get signatures with updated API call
                signatures_resp = await self.rate_limiter.call(
                    self.rpc_client.get_signatures_for_address,
                    pubkey,
                    limit=self.batch_size,
                    commitment=Commitment("confirmed")
                )
                
                # Extract signatures from response
                signatures = []
                if signatures_resp and hasattr(signatures_resp, 'value'):
                    signatures = signatures_resp.value
                
                if not signatures:
                    break
                
                # Process signatures in batches
                for i in range(0, len(signatures), self.batch_size):
                    batch = signatures[i:i + self.batch_size]
                    
                    # Prepare batch requests
                    batch_requests = [
                        {
                            'func': self.rpc_client.get_transaction,
                            'args': [sig.signature],
                            'kwargs': {
                                'commitment': Commitment("confirmed"),
                                'encoding': "jsonParsed",
                                'max_supported_transaction_version': 0
                            }
                        }
                        for sig in batch
                    ]
                    
                    # Execute batch with rate limiting
                    tx_responses = await self.rate_limiter.execute_batch(
                        batch_requests, 
                        batch_size=10
                    )
                    
                    # Process responses
                    tx_data = []
                    for tx_response in tx_responses:
                        if isinstance(tx_response, Exception):
                            logger.error(f"Error getting transaction: {tx_response}")
                            continue
                            
                        if tx_response and tx_response.value:
                            parsed_tx = self._parse_transaction(tx_response.value, token.id)
                            if parsed_tx:
                                tx_data.append(parsed_tx)
                    
                    # Bulk insert successfully parsed transactions
                    if tx_data:
                        stmt = insert(WalletTransaction).values(tx_data)
                        db.execute(stmt)
                        db.commit()
                    
                    # Small delay between batches
                    await asyncio.sleep(0.1)
                
                start_time = end_time

        except Exception as e:
            logger.error(f"Error processing transactions: {e}")
            db.rollback()
            raise
        finally:
            db.close()
            
    def _parse_instruction_data(self, instruction: Dict) -> Optional[Dict]:
        """Parse instruction data to extract transaction details"""
        try:
            # This is where you'd implement the specific parsing logic
            # based on your token program's instruction format
            if instruction.get('data'):
                # Parse the instruction data to determine:
                # - transaction_type (buy/sell/transfer)
                # - amount
                # - price
                # - wallet_address
                return {
                    'transaction_type': 'transfer',  # Example - implement actual parsing
                    'amount': 0.0,  # Example - implement actual parsing
                    'price': 0.0,  # Example - implement actual parsing
                    'wallet_address': '',  # Example - implement actual parsing
                    'mcap_at_time': 0.0  # Example - implement actual parsing
                }
            return None
        except Exception as e:
            logger.error(f"Error parsing instruction data: {e}")
            return None