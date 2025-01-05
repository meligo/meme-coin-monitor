import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import base58
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.config.database import get_db, get_db_session
from src.config.settings import settings
from src.core.models import MemeCoin, TokenTier
from src.core.models.base import Base
from src.core.models.wallet_analysis import WalletTransaction
from src.services.monitoring.performance import measure_performance
from src.utils.rate_limiter import GlobalRateLimiter
from src.utils.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

class TransactionProcessor:
    def __init__(self, rpc_manager: RPCManager):
        self.rpc_manager = rpc_manager
        self.batch_size = 100
        self.token_program_id = Pubkey.from_string(settings.system_token_program)
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
        """Process transactions efficiently using RPCManager"""
        async with get_db_session() as db:
            try:
                # Get token first to ensure it exists
                result = await db.execute(
                    select(MemeCoin).filter(MemeCoin.address == token_address)
                )
                token = result.scalar_one_or_none()
                
                if not token:
                    logger.error(f"Token {token_address} not found in database")
                    return

                # Get latest processed transaction
                result = await db.execute(
                    select(WalletTransaction)
                    .filter(WalletTransaction.token_id == token.id)
                    .order_by(WalletTransaction.timestamp.desc())
                    .limit(1)
                )
                latest_tx = result.scalar_one_or_none()
                
                start_time = latest_tx.timestamp if latest_tx else (
                    datetime.now(timezone.utc) - timedelta(days=7)
                )

                current_time = datetime.now(timezone.utc)
                pubkey = Pubkey.from_string(token_address)
                
                while start_time < current_time:
                    end_time = min(start_time + timedelta(hours=1), current_time)
                    
                    # Get signatures using RPCManager
                    signatures_resp = await self.rpc_manager.get_signatures_for_address(
                        str(pubkey),
                        limit=self.batch_size,
                        commitment="confirmed"
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
                        
                        # Process each signature in the batch
                        tx_data = []
                        for sig in batch:
                            try:
                                tx_response = await self.rpc_manager.get_transaction(
                                    sig.signature,
                                    commitment="confirmed",
                                    encoding="jsonParsed",
                                    max_supported_transaction_version=0
                                )
                                
                                if tx_response and tx_response.value:
                                    parsed_tx = self._parse_transaction(tx_response.value, token.id)
                                    if parsed_tx:
                                        tx_data.append(parsed_tx)
                                        
                            except Exception as e:
                                logger.error(f"Error processing transaction {sig.signature}: {e}")
                                continue
                        
                        # Bulk insert successfully parsed transactions
                        if tx_data:
                            stmt = insert(WalletTransaction).values(tx_data)
                            await db.execute(stmt)
                            await db.commit()
                    
                    start_time = end_time

            except Exception as e:
                logger.error(f"Error processing transactions: {e}")
                db.rollback()
                raise
            
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