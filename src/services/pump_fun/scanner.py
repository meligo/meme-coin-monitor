import asyncio
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import websockets
from construct import Flag, Int8ul, Int64ul, Struct
from solana.exceptions import SolanaRpcException
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey

from src.config.database import get_db
from src.config.settings import settings
from src.core.models.meme_coin import MemeCoin

logger = logging.getLogger(__name__)

class TokenProcessor:
    """Handles asynchronous processing of token data"""
    
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing = True
        self._processing_task = None
        self.processed_count = 0
        self.error_count = 0
        logger.info("Token processor initialized")

    async def start(self):
        """Start the token processor"""
        self._processing_task = asyncio.create_task(self._process_queue())
        logger.info("Token processor started")

    async def stop(self):
        """Stop the token processor"""
        self.processing = False
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        logger.info(f"Token processor stopped. Processed: {self.processed_count}, Errors: {self.error_count}")

    async def add_token(self, token_data: Dict):
        """Add a token to the processing queue"""
        if not isinstance(token_data, dict):
            logger.error(f"Invalid token data type: {type(token_data)}")
            return
        await self.queue.put(token_data)
        logger.debug(f"Added token to queue: {token_data.get('address')} (Queue size: {self.queue.qsize()})")

    async def _process_queue(self):
        """Process tokens from the queue"""
        while self.processing:
            try:
                if self.queue.qsize() > 0:
                    logger.debug(f"Current queue size: {self.queue.qsize()}")
                
                token_data = await self.queue.get()
                await self._process_token(token_data)
                self.queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing token from queue: {e}")
                self.error_count += 1

    async def _process_token(self, token_data: Dict):
        """Process a single token"""
        if not token_data:
            logger.warning("⚠️ Received empty token data")
            return

        db = next(get_db())
        try:
            address = token_data.get('address')
            if not address:
                logger.error("❌ Token data missing address")
                self.error_count += 1
                return

            # Log incoming token data
            logger.info(f"💫 Processing: {token_data.get('name')} ({token_data.get('symbol')}) - {address}")

            existing_token = db.query(MemeCoin).filter(
                MemeCoin.address == address
            ).first()
            
            if existing_token:
                logger.info(f"📋 Token {token_data.get('name')} already exists in database")
                return

            meme_coin = MemeCoin(
                address=address,
                name=token_data.get('name', f"Unknown Token {address[:8]}"),
                symbol=token_data.get('symbol', 'UNKNOWN'),
                total_supply=token_data.get('total_supply', 0),
                liquidity=token_data.get('liquidity', 0),
                contract_analysis=token_data.get('contract_analysis', {}),
                bonding_curve_address=token_data.get('bonding_curve'),
                bonding_curve_state=token_data.get('curve_data', {}),
                is_curve_complete=token_data.get('curve_complete', False),
                launch_date=token_data.get('creation_date', datetime.now(timezone.utc))
            )

            db.add(meme_coin)
            db.commit()
            self.processed_count += 1
            logger.info(f"✨ Added new token: {meme_coin.name} ({meme_coin.symbol}) - Total processed: {self.processed_count}")

        except Exception as e:
            db.rollback()
            self.error_count += 1
            logger.error(f"Error storing token {token_data.get('address')}: {e}")
            logger.error(f"Token data that caused error: {json.dumps(token_data, indent=2, default=str)}")
        finally:
            db.close()

class PumpFunScanner:
    """Scanner for Pump.fun token operations"""

    def __init__(self):
        self.rpc_client = AsyncClient(settings.RPC_ENDPOINT, commitment=Commitment("confirmed"),timeout=30.0)
        self.token_processor = TokenProcessor()
        self.pump_program = Pubkey.from_string(settings.PUMP_PROGRAM)
        self.metadata_program = Pubkey.from_string(settings.TOKEN_METADATA_PROGRAM_ID)
        self.max_retries = 3
        self.retry_delay = 2

        # Token account state structure
        self.TOKEN_STATE = Struct(
            "discriminator" / Int64ul,
            "version" / Int8ul,
            "is_initialized" / Flag,
            "virtual_token_reserves" / Int64ul,
            "virtual_sol_reserves" / Int64ul,
            "real_token_reserves" / Int64ul,
            "real_sol_reserves" / Int64ul,
            "token_total_supply" / Int64ul,
            "curve_state" / Int8ul,
            "is_frozen" / Flag
        )

        # Discriminators
        self.TOKEN_DISCRIMINATOR = settings.TOKEN_DISCRIMINATOR
        self.CURVE_DISCRIMINATOR = settings.CURVE_DISCRIMINATOR

        logger.info("PumpFunScanner initialized with 2024 configurations")

    async def retry_with_backoff(self, func, *args, **kwargs) -> Any:
        """Execute function with exponential backoff retry"""
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                wait_time = self.retry_delay * (2 ** attempt)
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

    async def scan_existing_tokens(self):
        """Scan and analyze existing tokens"""
        logger.info("Starting scan of existing tokens...")
        try:
            filters = [
                MemcmpOpts(
                    offset=0,
                    bytes=self.TOKEN_DISCRIMINATOR
                )
            ]

            response = await self.retry_with_backoff(
                self.rpc_client.get_program_accounts,
                self.pump_program,
                encoding="base64",
                commitment=Commitment("confirmed"),
                filters=filters
                )

            if not response.value:
                logger.info("No existing tokens found")
                return

            total_accounts = len(response.value)
            logger.info(f"Found {total_accounts} token accounts")

            processed = 0
            for account in response.value:
                try:
                    result = await self._process_existing_token(account)
                    if result:
                        processed += 1
                        if processed % 10 == 0:  # Log every 10 tokens
                            logger.info(f"Processed {processed}/{total_accounts} tokens")
                except Exception as e:
                    logger.error(f"Error processing account: {e}")

            logger.info(f"Completed processing {processed}/{total_accounts} tokens")

        except Exception as e:
            logger.error(f"Error scanning existing tokens: {e}")
            raise

    async def listen_for_new_tokens(self):
        """Listen for new token creations"""
        logger.info("Starting new token listener...")
        retry_count = 0
        
        while True:
            try:
                ws_endpoint = settings.RPC_ENDPOINT.replace('https://', 'wss://')
                async with websockets.connect(ws_endpoint) as websocket:
                    subscription = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "blockSubscribe",
                        "params": [
                            {"mentionsAccountOrProgram": str(self.pump_program)},
                            {
                                "commitment": "confirmed",
                                "encoding": "base64",
                                "showRewards": False,
                                "transactionDetails": "full",
                                "maxSupportedTransactionVersion": 0
                            }
                        ]
                    }
                    
                    await websocket.send(json.dumps(subscription))
                    logger.info("WebSocket subscription established")
                    retry_count = 0
                    
                    while True:
                        try:
                            response = await websocket.recv()
                            await self._handle_websocket_message(response)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            logger.error(f"Error processing WebSocket message: {e}")
                            continue
                            
            except asyncio.CancelledError:
                raise
            except Exception as e:
                retry_count += 1
                wait_time = min(retry_count * 5, 30)
                logger.error(f"WebSocket connection error (retry {retry_count}): {e}")
                await asyncio.sleep(wait_time)

    async def _handle_websocket_message(self, message: str):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            if data.get('method') != 'blockNotification':
                return

            block_data = data.get('params', {}).get('result', {}).get('value', {}).get('block', {})
            transactions = block_data.get('transactions', [])

            for tx in transactions:
                if not isinstance(tx, dict):
                    continue

                if self._is_token_creation(tx):
                    logger.info("New token creation detected")
                    token_data = await self._process_transaction(tx)
                    if token_data:
                        await self.token_processor.add_token(token_data)

        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")

    def _is_token_creation(self, tx_data) -> bool:
        """Check if transaction is a token creation"""
        try:
            # Handle Solders EncodedConfirmedTransactionWithStatusMeta object
            log_messages = []
            
            if hasattr(tx_data, 'meta') and tx_data.meta:
                # Access log_messages directly from the meta field
                log_messages = tx_data.meta.log_messages if tx_data.meta.log_messages else []
            
            if not log_messages:
                return False

            # Debug logging
            logger.debug(f"Analyzing logs: {log_messages}")

            creation_patterns = {
                "Program log: Instruction: Create",
                "Program log: Initialize token",
                "Program log: Creating new token",
                "Program log: Token initialized"
            }
            validation_patterns = {
                "Program log: Token mint:",
                "Program log: Curve data:"
            }

            has_creation = any(any(cp in str(log) for cp in creation_patterns) for log in log_messages)
            has_validation = any(any(vp in str(log) for vp in validation_patterns) for log in log_messages)

            if has_creation and has_validation:
                logger.debug("Transaction identified as token creation")
                return True
                
            return False

        except Exception as e:
            logger.error(f"Error checking token creation: {str(e)}")
            if hasattr(tx_data, 'meta'):
                logger.debug(f"Transaction meta type: {type(tx_data.meta)}")
                logger.debug(f"Transaction meta dir: {dir(tx_data.meta)}")
            return False

    async def _process_existing_token(self, account) -> Optional[str]:
        """Process an existing token account"""
        try:
            data = base64.b64decode(account.account.data[0])
            if len(data) < 8 or data[:8] != self.TOKEN_DISCRIMINATOR:
                return None

            token_data = await self.analyze_token_account(account.pubkey, data)
            if token_data:
                await self.token_processor.add_token(token_data)
                return token_data['address']
            return None

        except Exception as e:
            logger.error(f"Error processing existing token: {e}")
            return None

    async def _process_transaction(self, tx_data) -> Optional[Dict]:
        """Process a transaction for token creation"""
        try:
            logger.info("🔍 Investigating new potential token transaction...") # Changed from debug
            
            # Handle Solders EncodedConfirmedTransactionWithStatusMeta object
            if not hasattr(tx_data, 'meta') or not tx_data.meta:
                return None

            post_token_balances = tx_data.meta.post_token_balances if tx_data.meta.post_token_balances else []

            for balance in post_token_balances:
                # For Solders object, access mint directly
                mint_address = balance.mint
                if not mint_address:
                    continue

                logger.info(f"📝 Analyzing token: {mint_address}") # Changed from debug
                pubkey = Pubkey.from_string(mint_address)
                account_info = await self.retry_with_backoff(
                    self.rpc_client.get_account_info,
                    pubkey,
                    commitment=Commitment("confirmed"),
                    encoding="base64"
                )

                if not account_info.value:
                    logger.debug(f"No account info found for {mint_address}")
                    continue

                data = base64.b64decode(account_info.value.data[0])
                if len(data) < 8 or data[:8] != self.TOKEN_DISCRIMINATOR:
                    logger.debug(f"Invalid token discriminator for {mint_address}")
                    continue

                token_data = await self.analyze_token_account(pubkey, data)
                if token_data:
                    logger.info(f"Successfully analyzed token: {mint_address}")
                    return token_data

            return None

        except Exception as e:
            logger.error(f"Error processing transaction: {e}")
            if hasattr(tx_data, 'meta'):
                logger.debug(f"Transaction meta type: {type(tx_data.meta)}")
                logger.debug(f"Transaction meta dir: {dir(tx_data.meta)}")
            return None

    async def analyze_token_account(self, pubkey: Pubkey, data: bytes) -> Optional[Dict]:
        """Analyze a token account and extract data"""
        try:
            logger.info(f"🔎 Analyzing token account: {pubkey}") # Changed from debug
            token_state = self.TOKEN_STATE.parse(data)
            token_info = await self._get_token_metadata(pubkey)
            
            token_data = {
                'address': str(pubkey),
                'name': token_info.get('name', f"Unknown Token {str(pubkey)[:8]}"),
                'symbol': token_info.get('symbol', 'UNKNOWN'),
                'curve_data': {
                    'virtual_token_reserves': token_state.virtual_token_reserves,
                    'virtual_sol_reserves': token_state.virtual_sol_reserves,
                    'real_token_reserves': token_state.real_token_reserves,
                    'real_sol_reserves': token_state.real_sol_reserves,
                    'total_supply': token_state.token_total_supply
                },
                'is_frozen': token_state.is_frozen,
                'version': token_state.version,
                'curve_state': token_state.curve_state,
                'creation_date': datetime.now(timezone.utc)
            }
            
            logger.info(f"✅ Token Analysis Complete - {token_data['name']} ({token_data['symbol']})") # New line
            return token_data

        except Exception as e:
            logger.error(f"Error getting token metadata: {e}")
            return None

    async def _get_token_metadata(self, token_address: Pubkey) -> Dict:
        """Get token metadata from chain"""
        try:
            # Get metadata account
            metadata_address = Pubkey.find_program_address(
                [b"metadata", bytes(self.metadata_program), bytes(token_address)],
                self.metadata_program
            )[0]
            
            metadata_account = await self.retry_with_backoff(
                self.rpc_client.get_account_info,
                metadata_address,
                commitment=Commitment("confirmed")
            )
            
            if metadata_account.value:
                data = base64.b64decode(metadata_account.value.data[0])
                metadata = {
                    'name': data[32:64].decode('utf-8').strip('\x00'),
                    'symbol': data[64:96].decode('utf-8').strip('\x00')
                }
                logger.debug(f"Retrieved metadata for {token_address}: {metadata}")
                return metadata
            
            logger.debug(f"No metadata found for {token_address}")
            return {}

        except Exception as e:
            logger.error(f"Error getting token metadata: {e}")
            return {}