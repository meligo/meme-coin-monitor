# [Previous imports remain the same...]

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
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey
from solders.rpc.responses import GetProgramAccountsJsonParsedResp

from src.config.database import get_db
from src.config.settings import settings
from src.core.models.meme_coin import MemeCoin
from src.core.models.tier_models import TierAlert, TierTransition, TokenTier
from src.services.pump_fun.liquidity_checker import LiquidityChecker
from src.services.tier_management import TierLevel, TierProcessor
from src.services.tier_management.monitoring import TierMonitor  # Add this line
from src.services.tier_management.tier_manager import (
    TierLevel,  # Make sure this path matches your project structure
    TierManager,
)
from src.services.tier_management.utils.metrics import calculate_token_metrics

logger = logging.getLogger(__name__)

EXPECTED_DISCRIMINATOR: Final[bytes] = struct.pack("<Q", 6966180631402821399)


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

class TokenProcessor:
    """Handles asynchronous processing of token data"""
    
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing = True
        self._processing_task = None
        self.processed_count = 0
        self.error_count = 0
        self.rpc_client = AsyncClient(settings['RPC_ENDPOINT'], commitment=Commitment("confirmed"),timeout=30.0)
        self.liquidity_checker = LiquidityChecker(self.rpc_client)
        self.tier_processor = TierProcessor()
        self.tier_manager = TierManager()  # Add this line
        self.tier_monitor = TierMonitor()  # Add this line

        logger.info("Token processor initialized with tier management")

        
        # Start the token processor
    def calculate_market_cap(self, real_sol_reserves: int, real_token_reserves: int) -> float:
        if real_token_reserves == 0:
            return 0
        # Price = real_sol_reserves / real_token_reserves
        # Market cap = price * total_supply (1B)
        return (real_sol_reserves / real_token_reserves) * 1_000_000_000

    def calculate_tokens_left(self, total_supply: int, real_token_reserves: int) -> int:
        return total_supply - real_token_reserves

    def calculate_completion_progress(self, current_mcap: float) -> float:
        COMPLETION_THRESHOLD = 81063  # USD
        return min((current_mcap / COMPLETION_THRESHOLD) * 100, 100)

    def calculate_koth_progress(self, current_mcap: float) -> float:
        KOTH_THRESHOLD = 39172  # USD
        return min((current_mcap / KOTH_THRESHOLD) * 100, 100)

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
        
        await self.rpc_client.close()

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

    def convert_timedelta_to_seconds(self, config):
        """Convert any timedelta objects in config to seconds"""
        if isinstance(config, dict):
            return {k: self.convert_timedelta_to_seconds(v) for k, v in config.items()}
        elif isinstance(config, timedelta):
            return int(config.total_seconds())
        elif isinstance(config, list):
            return [self.convert_timedelta_to_seconds(item) for item in config]  # Fixed: x -> item
        return config


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

            logger.info(f"💫 Processing: {token_data.get('name')} ({token_data.get('symbol')}) - {address}")

            # Check if the token already exists in the database 
            # Get real-time bonding curve data
            mint = Pubkey.from_string(address)
            logger.info("1. Getting bonding curve data...")
            bonding_curve_address, _ = self.get_associated_bonding_curve_address(
                mint, 
                Pubkey.from_string(settings['PUMP_PROGRAM'])
            )
            curve_state = await self.get_bonding_curve_state(bonding_curve_address)

            # Log incoming token data
            logger.info("2. check if existing token...")
            existing_token = db.query(MemeCoin).filter(
                MemeCoin.address == address
            ).first()
            
            if existing_token:
                logger.info(f"📋 Token {token_data.get('name')} already exists in database")
                # Update existing token's tier if needed
                await self._update_token_tier(existing_token, token_data)
                return
            logger.info("3. checking marketcap for token...")
            market_cap = self.calculate_market_cap(
                curve_state.real_sol_reserves,
                curve_state.real_token_reserves
            )
            logger.info("4. checking tokens left for token...")
            tokens_left = self.calculate_tokens_left(
                curve_state.token_total_supply,
                curve_state.real_token_reserves
            )
            logger.info("5. checking completion progress for token...")
            completion_progress = self.calculate_completion_progress(market_cap)
            
            logger.info("6. checking koth progress for token...")
            koth_progress = self.calculate_koth_progress(market_cap)
            
            logger.info("7. checking liquidity for token...")
            liquidity_info = await self.liquidity_checker.check_liquidity(
                address,
                str(bonding_curve_address)
            )

             # Create the base token
            meme_coin = MemeCoin(
                address=address,
                name=token_data.get('name', f"Unknown Token {address[:8]}"),
                symbol=token_data.get('symbol', 'UNKNOWN'),
                total_supply=curve_state.token_total_supply if curve_state else 0,
                contract_analysis=token_data.get('contract_analysis', {}),
                bonding_curve_address=str(bonding_curve_address),
                bonding_curve_state={
                    'virtual_token_reserves': curve_state.virtual_token_reserves,
                    'virtual_sol_reserves': curve_state.virtual_sol_reserves,
                    'real_token_reserves': curve_state.real_token_reserves,
                    'real_sol_reserves': curve_state.real_sol_reserves,
                    'token_total_supply': curve_state.token_total_supply
                } if curve_state else {},
                is_curve_complete=curve_state.complete if curve_state else False,
                launch_date=token_data.get('creation_date', datetime.now(timezone.utc)),
                tokens_left=tokens_left,
                market_cap_usd=market_cap,
                bonding_curve_completion=completion_progress,
                king_of_hill_progress=koth_progress,
                liquidity=liquidity_info.get('current_liquidity', 0.0),  # Keep only this liquidity parameter
                price_usd=market_cap / (10**9) if market_cap > 0 else 0,  # Assuming 9 decimals
                volume_24h_usd=0,  # Will be updated by volume tracker
                holder_count=0,  # Will be updated by holder tracker
                risk_score=0.0,  # Will be calculated by risk analyzer
                whale_holdings=0.0,  # Will be updated by whale tracker
                smart_money_flow=0.0  # Will be updated by smart money tracker
            )
            
            db.add(meme_coin)
            db.flush() 
            # 2. Now create the tier data with the token ID
            initial_metrics = {
                'market_cap_usd': market_cap,
                'liquidity': liquidity_info.get('current_liquidity', 0.0),
                'holder_count': 0,
                'volume_24h': 0,
                'age': 0
            }
            
            # 3. Create tier entry
            tier_level = await self.tier_manager.assign_initial_tier(initial_metrics)
            monitoring_config = self.tier_manager.get_monitoring_config(tier_level)
            monitoring_config = self.convert_timedelta_to_seconds(monitoring_config)

            # Convert any timedelta in tier_metrics
            tier_metrics = initial_metrics.copy()
            if 'age' in tier_metrics and isinstance(tier_metrics['age'], timedelta):
                tier_metrics['age'] = int(tier_metrics['age'].total_seconds())
                
                
            
            token_tier = TokenTier(
                token_id=meme_coin.id,
                token_address=address,
                current_tier=tier_level,
                tier_updated_at=datetime.utcnow(),
                last_checked=datetime.utcnow(),
                check_frequency=monitoring_config['check_frequency'],
                next_check_at=datetime.utcnow() + timedelta(
                    seconds=monitoring_config['check_frequency']
                ),
                tier_metrics=tier_metrics,  # Use converted metrics
                monitoring_config=monitoring_config,  # Use converted config
                processing_priority=self._calculate_priority(tier_level, initial_metrics),
                is_active=True,
                is_monitoring_paused=False,
                alert_count=0
            )

            # Add tier and commit all changes
            db.add(token_tier)
            db.commit()
            db.refresh(meme_coin)
            db.refresh(token_tier)

            
            # Start monitoring based on tier
            await self._start_tier_monitoring(meme_coin)

            self.processed_count += 1
            logger.info(f"✨ Added new token: {meme_coin.name} ({meme_coin.symbol}) - Total processed: {self.processed_count}")
            logger.info(f"✨ Added token: {meme_coin.name} ({meme_coin.symbol}) - "
                       f"Market Cap: ${market_cap:.2f}, "
                       f"Completion: {completion_progress:.1f}%, "
                       f"KOTH: {koth_progress:.1f}%")
        except Exception as e:
            db.rollback()
            self.error_count += 1
            logger.error(f"Error storing token {token_data.get('address')}: {e}")
            logger.error(f"Token data that caused error: {json.dumps(token_data, indent=2, default=str)}")
        finally:
            db.close()

    def _calculate_priority(self, tier_level: TierLevel, metrics: Dict[str, Any]) -> int:
        """Calculate processing priority (1-100)"""
        try:
            base_priority = {
                TierLevel.SIGNAL: 90,
                TierLevel.HOT: 70,
                TierLevel.WARM: 50,
                TierLevel.COLD: 30,
                TierLevel.ARCHIVE: 10
            }.get(tier_level, 10)
            
            # Adjust based on market cap and liquidity
            mcap_score = min((metrics.get('market_cap_usd', 0) / 100000), 5)  # Max 5 points
            liq_score = min((metrics.get('liquidity', 0) / 50000), 5)  # Max 5 points
            
            return min(100, int(base_priority + mcap_score + liq_score))
            
        except Exception as e:
            logger.error(f"Error calculating priority: {e}")
            return 50  # Default middle priority

    async def _initialize_token_tier(self, meme_coin, token_data):
        """Initialize tier monitoring for a new token with complete metrics integration"""
        
        # Calculate initial metrics
        initial_metrics = {
            'market_cap_usd': token_data.get('market_cap_usd', 0),
            'liquidity': token_data.get('liquidity', 0),
            'holder_count': token_data.get('holder_count', 0),
            'volume_24h': token_data.get('volume_24h', 0),
            'creation_date': token_data.get('creation_date', datetime.now(timezone.utc)),
            'whale_holdings': 0.0,  # Initial whale holdings metric
            'smart_money_flow': 0.0,  # Initial smart money flow
            'holder_growth_rate': 0.0,  # Initial holder growth rate
            'volume_growth_rate': 0.0,  # Initial volume growth rate
            'liquidity_level': token_data.get('liquidity', 0) / token_data.get('market_cap_usd', 1) if token_data.get('market_cap_usd', 0) > 0 else 0
        }

        # Process metrics through tier processor
        tier_result = await self.tier_processor.process_new_token(
            address=meme_coin.address,
            token_data=initial_metrics
        )

        # Initialize monitoring configuration based on tier
        monitoring_config = {
            'whale_threshold': 0.02,  # 2% of supply for whale detection
            'smart_money_threshold': 100000,  # $100k for smart money detection
            'min_liquidity_ratio': 0.1,  # 10% minimum liquidity ratio
            'max_holder_concentration': 0.25,  # 25% maximum holder concentration
            'alert_thresholds': {
                'price_change': 0.2,  # 20% price change alert
                'volume_spike': 3.0,  # 3x volume spike alert
                'liquidity_drop': 0.3  # 30% liquidity drop alert
            }
        }

        # Construct complete tier data
        tier_data = {
            'tier': tier_result.tier_level,
            'score': tier_result.tier_score,
            'metrics': initial_metrics,
            'config': monitoring_config,
            'priority': self._calculate_priority(tier_result.tier_level, initial_metrics)
        }

        # Create new TokenTier instance with all fields
        token_tier = TokenTier(
            # Core Information
            token_address=meme_coin.address,
            current_tier=tier_data['tier'],
            tier_updated_at=datetime.utcnow(),
            last_checked=datetime.utcnow(),
            check_frequency=self.tier_manager.check_frequencies[tier_data['tier']],
            next_check_at=datetime.utcnow() + timedelta(
                seconds=self.tier_manager.check_frequencies[tier_data['tier']]
            ),
            
            # Tier-specific Metrics
            tier_score=tier_data['score'],
            tier_metrics=tier_data['metrics'],
            monitoring_config=tier_data['config'],
            processing_priority=tier_data['priority'],
            
            # Signal Tier Metrics (from initial_metrics)
            whale_holdings=initial_metrics['whale_holdings'],
            smart_money_flow=initial_metrics['smart_money_flow'],
            liquidity_level=initial_metrics['liquidity_level'],
            holder_count=initial_metrics['holder_count'],
            holder_growth_rate=initial_metrics['holder_growth_rate'],
            volume_growth_rate=initial_metrics['volume_growth_rate'],
            
            # Historical Tier Data
            previous_tier=None,
            tier_history=[{
                'tier': tier_data['tier'].value,
                'timestamp': datetime.utcnow().isoformat(),
                'reason': 'Initial classification'
            }],
            signal_history=[],
            
            # Resource Allocation
            memory_allocation=self._calculate_memory_allocation(tier_data['tier']),
            
            # Monitoring Status
            is_active=True,
            is_monitoring_paused=False,
            last_alert_at=None,
            alert_count=0
        )

        # Create initial tier transition record
        tier_transition = TierTransition(
            token_tier=token_tier,
            from_tier=None,
            to_tier=tier_data['tier'],
            reason="Initial token classification",
            metrics_snapshot=initial_metrics
        )

        # Setup relationships
        meme_coin.tier = token_tier
        token_tier.tier_transitions.append(tier_transition)

        return tier_data

    def _calculate_tier_score(self, metrics: Dict) -> float:
        """Calculate a score to determine tier priority"""
        try:
            score = 0.0
            
            # Market cap weight
            if metrics['market_cap'] > 0:
                score += min((metrics['market_cap'] / 100000), 40)  # Max 40 points
                
            # Liquidity weight
            if metrics['liquidity'] > 0:
                score += min((metrics['liquidity'] / 50000), 30)  # Max 30 points
                
            # Growth metrics
            if metrics['liquidity_growth'] > 0:
                score += min((metrics['liquidity_growth'] * 100), 15)  # Max 15 points
                
            if metrics['holder_growth'] > 0:
                score += min((metrics['holder_growth'] * 100), 15)  # Max 15 points
                
            return min(score, 100)  # Cap at 100
            
        except Exception as e:
            logger.error(f"Error calculating tier score: {e}")
            return 0.0

    async def _start_tier_monitoring(self, token: MemeCoin):
        """Start tier-specific monitoring for a token"""
        try:
            if not token.tier:
                logger.error(f"No tier information for token {token.address}")
                return

            token_data = {
                'address': token.address,
                'whale_holdings': token.whale_holdings,
                'smart_money_flow': token.smart_money_flow,
                'liquidity': token.liquidity,
                'market_cap_usd': token.market_cap_usd,
                'holder_count': token.holder_count,
                'volume_24h': token.volume_24h_usd,
                'holder_distribution': getattr(token, 'holder_distribution', {}),
                'volume_change_percent': getattr(token, 'volume_change_percent', 0),
                'holder_change_percent': getattr(token, 'holder_change_percent', 0),
                'holder_growth_rate': getattr(token, 'holder_growth_rate', 0),
                'social_sentiment_score': getattr(token, 'social_sentiment_score', 0)
            }

            # Pass both token and token_data to the monitor method
            if token.tier.current_tier == TierLevel.SIGNAL:
                monitoring_result = await self.tier_monitor.monitor_signal_tier(token_data)
            elif token.tier.current_tier == TierLevel.HOT:
                monitoring_result = await self.tier_monitor.monitor_hot_tier(token_data)
            elif token.tier.current_tier == TierLevel.WARM:
                monitoring_result = await self.tier_monitor.monitor_warm_tier(token_data)
            elif token.tier.current_tier == TierLevel.COLD:
                monitoring_result = await self.tier_monitor.monitor_cold_tier(token_data)
            else:
                monitoring_result = None

            if monitoring_result:
                if monitoring_result.get('error'):
                    logger.error(f"Monitoring error for {token.address}: {monitoring_result['error']}")
                else:
                    logger.info(f"Started monitoring for {token.address} in {token.tier.current_tier.value} tier")
                    if monitoring_result.get('changes_detected'):
                        logger.info(f"Alerts detected for {token.address}: {monitoring_result['alerts']}")
                        
        except Exception as e:
            logger.error(f"Error starting tier monitoring: {e}")

    def _calculate_memory_allocation(self, tier_level):
        """Calculate memory allocation percentage based on tier"""
        return {
            TierLevel.SIGNAL: 1.0,   # 100% of tier quota
            TierLevel.HOT: 0.8,      # 80% of tier quota
            TierLevel.WARM: 0.6,     # 60% of tier quota
            TierLevel.COLD: 0.4,     # 40% of tier quota
            TierLevel.ARCHIVE: 0.2   # 20% of tier quota
        }.get(tier_level, 0.2)
        
    def get_associated_bonding_curve_address(self, mint: Pubkey, program_id: Pubkey) -> tuple[Pubkey, int]:
        """Derives the associated bonding curve address for a given mint"""
        return Pubkey.find_program_address(
            [
                b"bonding-curve",
                bytes(mint)
            ],
            program_id
        )

    async def get_bonding_curve_state(self, curve_address: Pubkey) -> Optional[BondingCurveState]:
        """Get the bonding curve state for a given curve address"""
        try:
            response = await self.rpc_client.get_account_info(curve_address)
            if not response.value or not response.value.data:
                logger.warning(f"No data found for bonding curve: {curve_address}")
                return None

            data = response.value.data
            
            # Debug discriminator values
            received_discriminator = data[:8]
            received_discriminator_int = int.from_bytes(received_discriminator, "little")
            expected_discriminator_int = 6966180631402821399
            
            logger.debug(f"Received discriminator (hex): {received_discriminator.hex()}")
            logger.debug(f"Received discriminator (int): {received_discriminator_int}")
            logger.debug(f"Expected discriminator (int): {expected_discriminator_int}")
            
            # Try parsing regardless of discriminator to see structure
            try:
                state = BondingCurveState(data)
                logger.debug(f"Parsed state: virtual_token_reserves={state.virtual_token_reserves}, "
                        f"virtual_sol_reserves={state.virtual_sol_reserves}, "
                        f"real_token_reserves={state.real_token_reserves}, "
                        f"real_sol_reserves={state.real_sol_reserves}, "
                        f"token_total_supply={state.token_total_supply}, "
                        f"complete={state.complete}")
                return state
            except Exception as parse_error:
                logger.error(f"Error parsing state: {parse_error}")
                return None

        except Exception as e:
            logger.error(f"Error getting bonding curve state: {e}. Response data: {data.hex() if data else 'No data'}")
            return None

class PumpFunScanner:
    """Scanner for Pump.fun token operations"""

    def __init__(self):
        self.rpc_client = AsyncClient(settings['RPC_ENDPOINT'], commitment=Commitment("confirmed"),timeout=30.0)
        self.token_processor = TokenProcessor()
        self.pump_program = Pubkey.from_string(settings['PUMP_PROGRAM'])
        self.metadata_program = Pubkey.from_string(settings['TOKEN_METADATA_PROGRAM_ID'])
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
        self.TOKEN_DISCRIMINATOR = settings['TOKEN_DISCRIMINATOR']
        self.CURVE_DISCRIMINATOR = settings['CURVE_DISCRIMINATOR']

        logger.info("PumpFunScanner initialized with 2024 configurations")
        
        
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

            # Use proper error handling for the response
            try:
                response = await self.retry_with_backoff(
                    self.rpc_client.get_program_accounts,
                    self.pump_program,
                    encoding="base64",
                    commitment=Commitment("confirmed"),
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
        """Listen for new token creations"""
        logger.info("Starting new token listener...")
        
        subscription = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [str(self.pump_program)]},
                {"commitment": "confirmed"}  # Changed from processed
            ]
        }
        
        while True:
            try:
                ws_endpoint = settings['RPC_ENDPOINT'].replace('https://', 'wss://')
                async with websockets.connect(ws_endpoint) as websocket:
                    await websocket.send(json.dumps(subscription))
                    logger.info("WebSocket subscription established")
                    
                    while True:
                        response = await websocket.recv()
                        data = json.loads(response)
                        
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
                                            
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                await asyncio.sleep(5)


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