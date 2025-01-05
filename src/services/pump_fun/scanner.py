import asyncio
import base64
import json
import logging
import re
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Final, List, Optional, Union

import base58
import websockets
from construct import Flag, Int8ul, Int64ul, Struct
from solana.exceptions import SolanaRpcException
from solana.rpc.commitment import Commitment
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey
from solders.rpc.responses import GetProgramAccountsJsonParsedResp
from sqlalchemy import select

from src.config.database import get_db_session
from src.config.settings import settings
from src.core.models.meme_coin import MemeCoin
from src.core.models.tier_models import TierAlert, TierTransition, TokenTier
from src.core.models.wallet_analysis import WalletAnalysis, WalletTransaction
from src.services.analysis.rug_detector import RugDetector
from src.services.blockchain.token_metrics import TokenMetrics
from src.services.holder_analysis.distribution_calculator import DistributionCalculator
from src.services.holder_analysis.metrics_updater import HolderMetricsUpdater
from src.services.monitoring.performance import (
    MeasureBlockPerformance,
    measure_performance,
)
from src.services.pump_fun.async_token_processor import (
    TokenProcessor,  # Import TokenProcessor directly  # Changed from AsyncTokenProcessor  # Changed from AsyncTokenProcessor
)
from src.services.pump_fun.liquidity_checker import LiquidityChecker
from src.services.pump_fun.models import (
    BondingCurveState,  # Import BondingCurveState from models
)
from src.services.tier_management import TierLevel, TierProcessor
from src.services.tier_management.monitoring import TierMonitor  # Add this line
from src.services.tier_management.tier_manager import (
    TierLevel,  # Make sure this path matches your project structure
    TierManager,
)
from src.services.tier_management.utils.metrics import calculate_token_metrics
from src.services.transaction_processing.processor import TransactionProcessor
from src.services.wallet_analysis.analyzer import WalletPatternAnalyzer
from src.utils.rate_limiter import GlobalRateLimiter
from src.utils.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

EXPECTED_DISCRIMINATOR: Final[bytes] = struct.pack("<Q", 6966180631402821399)

class PumpFunScanner:
    """Scanner for Pump.fun token operations"""
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PumpFunScanner, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, batch_size=None):
        if self._initialized:
            return
            
        self.batch_size = batch_size or 100  # Default batch size

        self.rpc_manager = RPCManager()
        self.token_processor = TokenProcessor()  # Using merged TokenProcessor
        self.token_metrics = TokenMetrics(self.rpc_manager)
        self.rate_limiter = GlobalRateLimiter()
        self.pump_program = Pubkey.from_string(settings.pump_program)
        self.metadata_program = Pubkey.from_string(settings.token_metadata_program_id)
        self.max_retries = 3
        self.retry_delay = 2
        self.tier_manager = TierManager()

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
        self.TOKEN_DISCRIMINATOR = settings.token_discriminator
        self.CURVE_DISCRIMINATOR = settings.curve_discriminator
        self._initialized = True

        logger.info("PumpFunScanner initialized..")

    def get_scanner_stats(self) -> Dict[str, Any]:
        """Get scanner statistics"""
        return {
            'processed_count': self.token_processor.processed_count,
            'error_count': self.token_processor.error_count,
            'processing': self.token_processor.processing,
            'queue_size': self.token_processor.queue.qsize() if self.token_processor.queue else 0,
            'last_processed': self.token_processor.last_processed.isoformat() if self.token_processor.last_processed else None
        }
    async def stop(self):
        """Stop the scanner and cleanup resources"""
        if hasattr(self, '_stopped') and self._stopped:
            return
            
        try:
            # Stop token processor
            if hasattr(self, 'token_processor'):
                await self.token_processor.stop()
                
            self._stopped = True
            logger.info("PumpFunScanner stopped successfully")
            
        except Exception as e:
            logger.error(f"Error stopping PumpFunScanner: {e}")
            raise
        
    def _validate_token_field(self, field: str, value: str) -> bool:
        """Validate token fields for suspicious patterns."""
        # Remove whitespace
        value = value.strip()
        
        # Basic length checks
        if len(value) < 2:
            logger.warning(f"Suspiciously short {field}: {value}")
            return False
            
        if len(value) > 50:
            logger.warning(f"Suspiciously long {field}: {value}")
            return False

        # Check for spam patterns
        spam_patterns = [
            r'^[a-z]{1,4}$',  # Very short random letters
            r'^[0-9]+$',      # Only numbers
            r'(.)\1{3,}',     # Repeated characters
            r'^[^a-zA-Z0-9\s]+$'  # Only special characters
        ]
        
        for pattern in spam_patterns:
            if re.match(pattern, value):
                logger.warning(f"Suspicious {field} pattern detected: {value}")
                return False
                
        # Additional checks for symbols
        if field == 'symbol':
            if not value.isupper():  # Symbols should be uppercase
                logger.warning(f"Invalid symbol format: {value}")
                return False
            if len(value) > 10:  # Symbols shouldn't be too long
                logger.warning(f"Symbol too long: {value}")
                return False
                
        # Additional checks for names
        if field == 'name':
            if re.match(r'^[a-zA-Z0-9\s]{1,4}$', value):  # Too simple/random
                logger.warning(f"Suspiciously simple name: {value}")
                return False
                
        return True
        
    def calculate_market_cap(self, real_sol_reserves: int, real_token_reserves: int) -> float:
        """Calculate market cap based on reserves"""
        if real_token_reserves == 0:
            return 0
        return (real_sol_reserves / real_token_reserves) * 1_000_000_000

    def calculate_tokens_left(self, total_supply: int, real_token_reserves: int) -> int:
        """Calculate remaining tokens"""
        return total_supply - real_token_reserves

    def calculate_completion_progress(self, current_mcap: float) -> float:
        """Calculate bonding curve completion progress"""
        COMPLETION_THRESHOLD = 81063  # USD
        return min((current_mcap / COMPLETION_THRESHOLD) * 100, 100)

    def calculate_koth_progress(self, current_mcap: float) -> float:
        """Calculate King of the Hill progress"""
        KOTH_THRESHOLD = 39172  # USD
        return min((current_mcap / KOTH_THRESHOLD) * 100, 100)

    # Update analyze_token_account method to use these calculations:
    async def analyze_token_account(self, pubkey: Pubkey, data: bytes) -> Optional[Dict]:
        """Analyze a token account and extract data"""
        try:
            logger.info(f"🔎 Analyzing token account: {pubkey}")
            token_state = self.TOKEN_STATE.parse(data)
            token_info = await self._get_token_metadata(pubkey)
            
            # Calculate metrics
            market_cap = self.calculate_market_cap(
                token_state.real_sol_reserves,
                token_state.real_token_reserves
            )
            
            tokens_left = self.calculate_tokens_left(
                token_state.token_total_supply,
                token_state.real_token_reserves
            )
            
            completion_progress = self.calculate_completion_progress(market_cap)
            koth_progress = self.calculate_koth_progress(market_cap)
            
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
                'creation_date': datetime.now(timezone.utc),
                'market_cap_usd': market_cap,
                'tokens_left': tokens_left,
                'bonding_curve_completion': completion_progress,
                'king_of_hill_progress': koth_progress
            }
            
            logger.info(f"✅ Token Analysis Complete - {token_data['name']} ({token_data['symbol']})")
            return token_data

        except Exception as e:
            logger.error(f"Error analyzing token account: {e}")
            return None
    def parse_create_instruction(self, data: bytes) -> Optional[Dict]:
        """
        Parse the create instruction data according to Pump.fun IDL structure.
        """
        try:
            # Log the raw data length and first few bytes for debugging
            logger.debug(f"Parsing instruction data of length {len(data)}, first 32 bytes: {data[:32].hex()}")
            
            if len(data) < 8:
                return None
                
            # Check discriminator first
            discriminator = data[:8]
            logger.debug(f"Discriminator: {discriminator.hex()}")
            
            offset = 8
            parsed_data = {}
            
            # Parse strings (name, symbol, uri)
            for field in ['name', 'symbol', 'uri']:
                if offset + 4 > len(data):
                    return None
                    
                # Get raw bytes for length before unpacking
                length_bytes = data[offset:offset+4]
                length = struct.unpack('<I', length_bytes)[0]
                
                # Sanity check for length - no field should be larger than 1000 bytes
                if length > 1000:
                    logger.debug(f"Rejecting oversized {field} field. Length bytes: {length_bytes.hex()}, Unpacked value: {length}")
                    return None
                    
                offset += 4
                
                if offset + length > len(data):
                    return None
                    
                try:
                    value = data[offset:offset+length].decode('utf-8')
                    parsed_data[field] = value.strip()
                except UnicodeDecodeError:
                    return None
                    
                offset += length

            # Parse public keys (mint, bondingCurve, user)
            for field in ['mint', 'bondingCurve', 'user']:
                if offset + 32 > len(data):
                    return None
                    
                pubkey_bytes = data[offset:offset+32]
                try:
                    value = base58.b58encode(pubkey_bytes).decode('utf-8')
                    parsed_data[field] = value
                    offset += 32
                except Exception:
                    return None

            # Log successful parse
            logger.info(f"Successfully parsed token: {parsed_data['name']} ({parsed_data['symbol']})")
            return parsed_data
                
        except struct.error as e:
            logger.debug(f"Failed to parse instruction: {e}")
            return None
        except Exception as e:
            logger.debug(f"Unexpected error parsing instruction: {e}")
            return None

    async def retry_with_backoff(self, coro_func, *args, **kwargs):
        """
        Retry an async operation with exponential backoff
        
        Args:
            coro_func: Either an async function or RPC method name string
            *args: Positional arguments for the coroutine
            **kwargs: Keyword arguments for the coroutine
            
        Returns:
            The result of the coroutine if successful
            
        Raises:
            Exception: The last error encountered after all retries fail
        """
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                if isinstance(coro_func, str):
                    # If coro_func is a string, treat it as RPC method name
                    coro = self.client.get_signatures_for_address(
                        self.pump_program,
                        *args,
                        **kwargs
                    )
                elif callable(coro_func):
                    # If it's a callable, create the coroutine as before
                    coro = coro_func(*args, **kwargs)
                else:
                    logger.error("retry_with_backoff requires a callable or RPC method name")
                    raise ValueError("coro_func must be callable or RPC method name")

                # Await the coroutine
                return await coro

            except Exception as e:
                last_exception = e
                if attempt == self.max_retries - 1:
                    logger.error(f"Final retry attempt failed: {str(e)}")
                    raise

                wait_time = self.retry_delay * (2 ** attempt)
                logger.warning(
                    f"Attempt {attempt + 1}/{self.max_retries} failed: {str(e)}. "
                    f"Retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)

        raise last_exception
    async def scan_existing_tokens(self):
        """Scan and analyze existing tokens"""
        logger.info("Starting scan of existing tokens...")
        logger.info("bytes: %s", self.TOKEN_DISCRIMINATOR)
        try:
            filters = [
                MemcmpOpts(
                    offset=0,
                    bytes=self.TOKEN_DISCRIMINATOR
                )
            ]

            # Use proper error handling for the response
            try:
                response = await self.rpc_manager.get_program_accounts(
                    str(self.pump_program),  # Convert Pubkey to string if needed
                    commitment="confirmed",
                    encoding="base64",
                    filters=filters
                )
                
                # Handle the response properly
                if isinstance(response, Exception):
                    logger.error(f"Error getting program accounts: {response}")
                    return
                
                accounts = getattr(response, 'value', None)
                if not accounts:
                    logger.info("No existing tokens found")
                    return

                total_accounts = len(accounts)
                logger.info(f"Found {total_accounts} token accounts")

                processed = 0
                for account in accounts:
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
                logger.error(f"Error in RPC call: {e}")
                return

        except Exception as e:
            logger.error(f"Error scanning existing tokens: {e}")
            raise
        
    async def _process_new_token(self, parsed_data: Dict):
        """Process a newly detected token from create instruction"""
        try:
            # Format token data for processing
            token_data = {
                'address': parsed_data['mint'],
                'name': parsed_data['name'],
                'symbol': parsed_data['symbol'],
                'bonding_curve': parsed_data['bondingCurve'],
                'creation_date': datetime.now(timezone.utc),
                'contract_analysis': {},  # Can be populated with additional analysis
                'curve_data': {},  # Will be populated by analyze_token_account
                'curve_complete': False
            }
            
            # Queue token for processing
            await self.token_processor.add_token(token_data)
            logger.info(f"Queued new token for processing: {token_data['name']} ({token_data['symbol']})")
            
        except Exception as e:
            logger.error(f"Error processing new token: {e}")

    async def listen_for_new_tokens(self):
        """Listen for new token creations with rate limiting"""
        logger.info("Starting new token listener...")
        
        subscription = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [str(self.pump_program)]},
                {"commitment": "confirmed"}
            ]
        }
        
        while True:
            try:
                ws_endpoint = settings.rpc_config["endpoints"][0]["url"].replace('https://', 'wss://')
                async with websockets.connect(ws_endpoint) as websocket:
                    await websocket.send(json.dumps(subscription))
                    logger.info("WebSocket subscription established")
                    
                    while True:
                        response = await websocket.recv()
                        
                        # Rate limit the message processing
                        await self.rate_limiter.call(
                            self._process_ws_message,
                            json.loads(response)
                        )
                        
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                await asyncio.sleep(5)

    async def _process_ws_message(self, data):
        """Process websocket message with rate limiting"""
        if 'method' in data and data['method'] == 'logsNotification':
            log_data = data['params']['result']['value']
            logs = log_data.get('logs', [])
            
            if any("Program log: Instruction: Create" in log for log in logs):
                for log in logs:
                    if "Program data:" in log:
                        try:
                            encoded_data = log.split(": ")[1]
                            decoded_data = base64.b64decode(encoded_data)
                            parsed_data = self.parse_create_instruction(decoded_data)
                            if parsed_data:
                                logger.info(f"New token detected: {parsed_data['name']} ({parsed_data['symbol']})")
                                await self._process_new_token(parsed_data)
                        except Exception as e:
                            logger.error(f"Failed to decode log: {e}")


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
                account_info = await self.rpc_manager.get_account_info(
                    str(pubkey),
                    commitment="confirmed",
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
            metadata_address = Pubkey.find_program_address(
                [b"metadata", bytes(self.metadata_program), bytes(token_address)],
                self.metadata_program
            )[0]
            metadata_account = await self.rpc_manager.get_account_info(
                str(metadata_address),
                commitment="confirmed"
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