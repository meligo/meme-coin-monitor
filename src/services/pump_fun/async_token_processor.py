import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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
from src.services.pump_fun.liquidity_checker import LiquidityChecker
from src.services.pump_fun.models import BondingCurveState
from src.services.tier_management import (  # Changed from processor
    TierLevel,
    TierProcessor,
)
from src.services.tier_management.monitoring import TierMonitor
from src.services.tier_management.tier_manager import (  # Fixed import
    TierLevel,
    TierManager,
)
from src.services.transaction_processing.processor import (
    TransactionProcessor,  # Fixed import
)
from src.services.wallet_analysis.analyzer import WalletPatternAnalyzer
from src.utils.rate_limiter import GlobalRateLimiter
from src.utils.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

class TokenProcessor:
    """Handles asynchronous processing of token data with integrated tier management"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenProcessor, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        # Queue and processing state
        self.queue = asyncio.Queue()
        self._running = False
        self.processing = True
        self._processing_task = None
        self.processed_count = 0
        self.error_count = 0
        self.last_processed = None

        # Service initialization
        self.rpc_manager = RPCManager()
        self.liquidity_checker = LiquidityChecker(self.rpc_manager)
        self.tier_processor = TierProcessor()
        self.tier_manager = TierManager()
        self.tier_monitor = TierMonitor()
        self.wallet_analyzer = WalletPatternAnalyzer(self.rpc_manager)
        self.rug_detector = RugDetector()
        self.holder_metrics_updater = HolderMetricsUpdater()
        self.distribution_calculator = DistributionCalculator()
        self.transaction_processor = TransactionProcessor(self.rpc_manager)
        self.token_metrics = TokenMetrics(self.rpc_manager)
        self.rate_limiter = GlobalRateLimiter()

        # Constants
        self.token_program_id = Pubkey.from_string(settings.system_token_program)
        self._initialized = True

        logger.info("Token processor initialized with tier management")

    async def start(self):
        """Start the token processor"""
        if self._running:
            return
            
        self._running = True
        self._processing_task = asyncio.create_task(self._process_queue())
        logger.info("Token processor started")

    async def stop(self):
        """Stop the token processor"""
        if not self._running:
            return
            
        self._running = False
        self.processing = False
        
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
                
        logger.info(f"Token processor stopped. Processed: {self.processed_count}, Errors: {self.error_count}")
    async def _process_token(self, db, token_data: Dict):
        """Process a single token with complete analysis and monitoring"""
        if not token_data:
            logger.warning("⚠️ Received empty token data")
            return

        try:
            address = token_data.get('address')
            if not address:
                logger.error("❌ Token data missing address")
                self.error_count += 1
                return

            logger.info(f"💫 Processing: {token_data.get('name')} ({token_data.get('symbol')}) - {address}")

            # Get real-time bonding curve data
            mint = Pubkey.from_string(address)
            logger.info("1. Getting bonding curve data...")
            bonding_curve_address, _ = self.get_associated_bonding_curve_address(
                mint, 
                Pubkey.from_string(settings.pump_program)
            )
            curve_state = await self.get_bonding_curve_state(bonding_curve_address)
            if not curve_state:
                logger.error(f"❌ Could not get curve state for {address}")
                return

            # Check if token exists
            result = await db.execute(
                select(MemeCoin).where(MemeCoin.address == address)
            )
            existing_token = result.scalar_one_or_none()

            if existing_token:
                logger.info(f"📋 Token {token_data.get('name')} already exists in database")
                await self._update_existing_token(existing_token, token_data, curve_state)
                return

            # Calculate metrics
            logger.info("2. Calculating metrics...")
            metrics = await self.token_metrics.get_all_metrics(
                address,
                curve_state
            )
            market_cap = metrics.get('market_cap_usd', 0)

            logger.info("3. Calculating token metrics...")
            tokens_left = self.calculate_tokens_left(
                curve_state.token_total_supply,
                curve_state.real_token_reserves
            )
            completion_progress = self.calculate_completion_progress(market_cap)
            koth_progress = self.calculate_koth_progress(market_cap)

            logger.info("4. Checking liquidity...")
            liquidity_info = await self.liquidity_checker.check_liquidity(
                address,
                str(bonding_curve_address)
            )

            # Current state for rug detection
            current_state = {
                'liquidity': liquidity_info.get('current_liquidity', 0.0),
                'volume_24h': token_data.get('volume_24h', 0),
                'holder_count': token_data.get('holder_count', 0),
                'smart_money_flow': token_data.get('smart_money_flow', 0),
                'market_cap_usd': market_cap,
                'tokens_left': tokens_left
            }

            # Initialize risk metrics
            risk_score = 0.0
            alerts = []
            risk_factors = {}

            logger.info("5. Creating base token...")
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
                liquidity=liquidity_info.get('current_liquidity', 0.0),
                price_usd=market_cap / (10**9) if market_cap > 0 else 0,
                volume_24h_usd=token_data.get('volume_24h', 0),
                holder_count=token_data.get('holder_count', 0),
                risk_score=risk_score,
                whale_holdings=token_data.get('whale_holdings', 0.0),
                smart_money_flow=token_data.get('smart_money_flow', 0.0)
            )

            if not meme_coin:
                logger.error("Failed to create MemeCoin instance")
                return

            db.add(meme_coin)
            await db.flush()

            logger.info("6. Initializing tier monitoring...")
            # Initialize tier monitoring
            tier_data = await self._initialize_token_tier(meme_coin, token_data)
            db.add(tier_data['tier_instance'])
            db.add(tier_data['transition'])

            logger.info("7. Processing wallet data...")
            # Process wallet data
            wallet_data = await self._get_wallet_data(token_data['address'])
            risk_score, analysis_data = await self.wallet_analyzer.analyze_wallet_patterns(
                token_address=token_data['address'],
                current_mcap=market_cap,
                holder_data=wallet_data['holders'],
                transaction_history=wallet_data['transactions']
            )

            # Create wallet analysis record
            wallet_analysis = WalletAnalysis(
                token_id=meme_coin.id,
                risk_score=risk_score,
                early_entries=analysis_data.get('early_entries', []),
                wallet_clusters=analysis_data.get('wallet_clusters', []),
                coordinated_actions=analysis_data.get('coordinated_actions', []),
                high_risk_wallets=analysis_data.get('high_risk_wallets', [])
            )
            db.add(wallet_analysis)

            logger.info("8. Updating holder metrics...")
            # Update holder metrics
            await self.holder_metrics_updater.update_holder_metrics(
                db=db,
                token_address=token_data['address'],
                holders=wallet_data['holders']
            )

            logger.info("9. Processing transactions...")
            # Process transactions
            await self.transaction_processor.process_transactions(
                token_address=token_data['address']
            )

            await db.commit()
            await db.refresh(meme_coin)

            logger.info("10. Starting tier monitoring...")
            # Start monitoring
            await self._start_tier_monitoring(meme_coin)

            self.processed_count += 1
            self.last_processed = datetime.now(timezone.utc)

            logger.info(f"✨ Added new token: {meme_coin.name} ({meme_coin.symbol}) - "
                    f"Market Cap: ${market_cap:.2f}, "
                    f"Completion: {completion_progress:.1f}%, "
                    f"KOTH: {koth_progress:.1f}%")

        except Exception as e:
            await db.rollback()
            self.error_count += 1
            logger.error(f"Error processing token {token_data.get('address')}: {e}")
            logger.error(f"Token data that caused error: {json.dumps(token_data, indent=2, default=str)}")
            logger.exception(e)
            raise
        
    # Add these methods from scanner directly to TokenProcessor
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
                if self.queue.empty():
                    await asyncio.sleep(1)
                    continue

                async with get_db_session() as db:
                    token_data = await self.queue.get()
                    try:
                        # Process token with full analysis and monitoring
                        await self._process_token(db, token_data)
                        
                        self.processed_count += 1
                        self.last_processed = datetime.now(timezone.utc)
                        
                    except Exception as e:
                        logger.error(f"Error processing token {token_data.get('address')}: {e}")
                        self.error_count += 1
                    finally:
                        self.queue.task_done()

            except asyncio.CancelledError:
                logger.info("Queue processing cancelled")
                break
            except Exception as e:
                logger.error(f"Error in queue processing: {e}")
                await asyncio.sleep(1)

    async def _create_token(self, db, token_data: Dict):
        """Create a new token with complete initialization"""
        try:
            # Get curve state and metrics
            bonding_curve_address, _ = self.get_associated_bonding_curve_address(
                Pubkey.from_string(token_data['address']), 
                Pubkey.from_string(settings.pump_program)
            )
            curve_state = await self.get_bonding_curve_state(bonding_curve_address)
            
            metrics = await self.token_metrics.get_all_metrics(
                token_data['address'],
                curve_state
            )
            
            market_cap = metrics.get('market_cap_usd', 0)
            tokens_left = self.calculate_tokens_left(
                curve_state.token_total_supply,
                curve_state.real_token_reserves
            )
            
            # Get liquidity info
            liquidity_info = await self.liquidity_checker.check_liquidity(
                token_data['address'],
                str(bonding_curve_address)
            )

            # Create base token
            meme_coin = MemeCoin(
                address=token_data['address'],
                name=token_data.get('name', f"Unknown Token {token_data['address'][:8]}"),
                symbol=token_data.get('symbol', 'UNKNOWN'),
                total_supply=curve_state.token_total_supply if curve_state else 0,
                contract_analysis=token_data.get('contract_analysis', {}),
                bonding_curve_address=str(bonding_curve_address),
                bonding_curve_state=self._get_curve_state_dict(curve_state),
                is_curve_complete=curve_state.complete if curve_state else False,
                launch_date=token_data.get('creation_date', datetime.now(timezone.utc)),
                tokens_left=tokens_left,
                market_cap_usd=market_cap,
                bonding_curve_completion=self.calculate_completion_progress(market_cap),
                king_of_hill_progress=self.calculate_koth_progress(market_cap),
                liquidity=liquidity_info.get('current_liquidity', 0.0),
                price_usd=market_cap / (10**9) if market_cap > 0 else 0,
                volume_24h_usd=token_data.get('volume_24h', 0),
                holder_count=token_data.get('holder_count', 0),
                risk_score=0.0,
                whale_holdings=token_data.get('whale_holdings', 0.0),
                smart_money_flow=token_data.get('smart_money_flow', 0.0)
            )
            
            db.add(meme_coin)
            await db.flush()

            # Initialize tier monitoring
            tier_data = await self._initialize_token_tier(meme_coin, token_data)
            db.add(tier_data['tier_instance'])
            db.add(tier_data['transition'])

            # Process wallet data
            wallet_data = await self._get_wallet_data(token_data['address'])
            risk_score, analysis_data = await self.wallet_analyzer.analyze_wallet_patterns(
                token_address=token_data['address'],
                current_mcap=market_cap,
                holder_data=wallet_data['holders'],
                transaction_history=wallet_data['transactions']
            )

            wallet_analysis = WalletAnalysis(
                token_id=meme_coin.id,
                risk_score=risk_score,
                early_entries=analysis_data.get('early_entries', []),
                wallet_clusters=analysis_data.get('wallet_clusters', []),
                coordinated_actions=analysis_data.get('coordinated_actions', []),
                high_risk_wallets=analysis_data.get('high_risk_wallets', [])
            )
            db.add(wallet_analysis)

            # Update holder metrics
            await self.holder_metrics_updater.update_holder_metrics(
                db=db,
                token_address=token_data['address'],
                holders=wallet_data['holders']
            )

            # Process transactions
            await self.transaction_processor.process_transactions(
                token_address=token_data['address']
            )

            await db.commit()
            
            # Start monitoring
            await self._start_tier_monitoring(meme_coin)
            
            logger.info(f"Created new token: {meme_coin.name} ({meme_coin.symbol})")
            return meme_coin

        except Exception as e:
            await db.rollback()
            logger.error(f"Error creating token: {e}")
            raise

    async def _update_token(self, db, token: MemeCoin, token_data: Dict):
        """Update an existing token with new data"""
        try:
            # Get updated metrics
            bonding_curve_address = Pubkey.from_string(token.bonding_curve_address)
            curve_state = await self.get_bonding_curve_state(bonding_curve_address)
            
            metrics = await self.token_metrics.get_all_metrics(
                token.address,
                curve_state
            )
            
            market_cap = metrics.get('market_cap_usd', 0)
            tokens_left = self.calculate_tokens_left(
                curve_state.token_total_supply,
                curve_state.real_token_reserves
            )

            # Get liquidity info
            liquidity_info = await self.liquidity_checker.check_liquidity(
                token.address,
                str(bonding_curve_address)
            )

            # Update token metrics
            token.bonding_curve_state = self._get_curve_state_dict(curve_state)
            token.is_curve_complete = curve_state.complete if curve_state else False
            token.tokens_left = tokens_left
            token.market_cap_usd = market_cap
            token.bonding_curve_completion = self.calculate_completion_progress(market_cap)
            token.king_of_hill_progress = self.calculate_koth_progress(market_cap)
            token.liquidity = liquidity_info.get('current_liquidity', 0.0)
            token.price_usd = market_cap / (10**9) if market_cap > 0 else 0
            token.volume_24h_usd = token_data.get('volume_24h', 0)
            token.holder_count = token_data.get('holder_count', 0)
            token.whale_holdings = token_data.get('whale_holdings', 0.0)
            token.smart_money_flow = token_data.get('smart_money_flow', 0.0)
            
            # Update holder metrics
            wallet_data = await self._get_wallet_data(token.address)
            await self.holder_metrics_updater.update_holder_metrics(
                db=db,
                token_address=token.address,
                holders=wallet_data['holders']
            )

            # Process any new transactions
            await self.transaction_processor.process_transactions(
                token_address=token.address
            )

            # Update risk analysis
            risk_score, analysis_data = await self.wallet_analyzer.analyze_wallet_patterns(
                token_address=token.address,
                current_mcap=token.market_cap_usd,
                holder_data=wallet_data['holders'],
                transaction_history=wallet_data['transactions']
            )
            token.risk_score = risk_score

            # Update tier monitoring
            if token.tier:
                await self._update_tier_monitoring(token, metrics)

            await db.commit()
            logger.info(f"Updated token: {token.name} ({token.symbol})")

        except Exception as e:
            await db.rollback()
            logger.error(f"Error updating token {token.address}: {e}")
            raise

    async def _update_tier_monitoring(self, token: MemeCoin, metrics: Dict):
        """Update tier monitoring with new metrics"""
        try:
            current_tier = token.tier.current_tier
            
            # Calculate new tier score based on updated metrics
            tier_data = {
                'market_cap_usd': metrics.get('market_cap_usd', 0),
                'liquidity': metrics.get('liquidity', 0),
                'holder_count': token.holder_count,
                'volume_24h': token.volume_24h_usd,
                'whale_holdings': token.whale_holdings,
                'smart_money_flow': token.smart_money_flow,
                'holder_growth_rate': getattr(token, 'holder_growth_rate', 0),
                'volume_growth_rate': getattr(token, 'volume_growth_rate', 0),
                'liquidity_level': token.liquidity / token.market_cap_usd if token.market_cap_usd > 0 else 0
            }

            new_tier = await self.tier_manager.evaluate_tier_change(
                token.address,
                current_tier,
                tier_data
            )

            if new_tier != current_tier:
                # Create tier transition record
                transition = TierTransition(
                    token_tier=token.tier,
                    from_tier=current_tier,
                    to_tier=new_tier,
                    reason="Metrics update",
                    metrics_snapshot=tier_data
                )
                
                # Update token tier
                token.tier.current_tier = new_tier
                token.tier.tier_updated_at = datetime.utcnow()
                token.tier.tier_metrics = tier_data
                token.tier.processing_priority = self._calculate_priority(new_tier, tier_data)
                token.tier.check_frequency = self.tier_manager.get_check_frequency(new_tier)
                token.tier.next_check_at = datetime.utcnow() + timedelta(
                    seconds=token.tier.check_frequency
                )
                
                # Add transition to history
                token.tier.tier_history.append({
                    'tier': new_tier.value,
                    'timestamp': datetime.utcnow().isoformat(),
                    'reason': 'Metrics update',
                    'metrics': tier_data
                })

                logger.info(f"Token {token.address} tier changed from {current_tier.value} to {new_tier.value}")

            # Update monitoring configuration
            monitoring_config = self.tier_manager.get_monitoring_config(new_tier)
            token.tier.monitoring_config = self.convert_timedelta_to_seconds(monitoring_config)

        except Exception as e:
            logger.error(f"Error updating tier monitoring for {token.address}: {e}")
            raise

    async def _get_wallet_data(self, token_address: str) -> Dict[str, List]:
        """Get wallet transaction and holder data for analysis"""
        try:
            # Get holder data
            holders = await self._get_token_holders(token_address)
            
            # Get transaction history
            async with get_db_session() as db:
                result = await db.execute(
                    select(WalletTransaction).where(
                        WalletTransaction.token_address == token_address
                    )
                )
                transactions = result.scalars().all()
                
            return {
                'holders': holders,
                'transactions': [tx.__dict__ for tx in transactions]
            }
        except Exception as e:
            logger.error(f"Error getting wallet data for {token_address}: {e}")
            return {'holders': [], 'transactions': []}

    async def _get_token_holders(self, token_address: str) -> List[Dict]:
        """Get token holders using batched RPC calls and efficient filtering"""
        try:
            # Get largest accounts
            largest_accounts = await self.rpc_manager.get_token_largest_accounts(
                Pubkey.from_string(token_address)
            )
            
            
            if not largest_accounts or not largest_accounts.value:
                return []
            logger.info("token_address: %s", token_address)
            # Get all accounts
            batch_accounts = await self.rpc_manager.get_program_accounts(
                str(self.token_program_id),  # Convert Pubkey to string if needed
                commitment="confirmed",
                encoding="jsonParsed",
                filters=[
                    {"memcmp": {"offset": 0, "bytes": token_address}},
                    {"dataSize": 165}
                ]
            )
            
            if not batch_accounts or not batch_accounts.value:
                return []
                
            # Process accounts
            all_holders = []
            for account in batch_accounts.value:
                if account.account.data.parsed:
                    info = account.account.data.parsed['info']
                    balance = int(info['tokenAmount']['amount'])
                    
                    if balance > 0:
                        all_holders.append({
                            'wallet': info['owner'],
                            'balance': balance,
                            'decimals': info['tokenAmount']['decimals']
                        })

            # Deduplicate and aggregate balances by wallet
            wallet_balances = {}
            for holder in all_holders:
                wallet = holder['wallet']
                if wallet in wallet_balances:
                    wallet_balances[wallet]['balance'] += holder['balance']
                else:
                    wallet_balances[wallet] = holder

            return list(wallet_balances.values())

        except Exception as e:
            logger.error(f"Error getting token holders: {e}")
            return []
            
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
            response = await self.rpc_manager.get_account_info(
                str(curve_address)
            )
            
            if not response.value or not response.value.data:
                logger.warning(f"No data found for bonding curve: {curve_address}")
                return None

            data = response.value.data
            
            try:
                state = BondingCurveState(data)
                logger.debug(f"Parsed state: {state}")
                return state
            except Exception as parse_error:
                logger.error(f"Error parsing state: {parse_error}")
                return None

        except Exception as e:
            logger.error(f"Error getting bonding curve state: {e}")
            return None
        
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
            
            # Adjust based on metrics
            mcap_score = min((metrics.get('market_cap_usd', 0) / 100000), 5)  # Max 5 points
            liq_score = min((metrics.get('liquidity', 0) / 50000), 5)  # Max 5 points
            volume_score = min((metrics.get('volume_24h', 0) / 10000), 5)  # Max 5 points
            holder_score = min((metrics.get('holder_count', 0) / 1000), 5)  # Max 5 points
            
            # Add growth rate bonuses
            growth_bonus = 0
            if metrics.get('holder_growth_rate', 0) > 0.1:  # 10% growth
                growth_bonus += 2
            if metrics.get('volume_growth_rate', 0) > 0.2:  # 20% growth
                growth_bonus += 2
                
            return min(100, int(base_priority + mcap_score + liq_score + volume_score + holder_score + growth_bonus))
            
        except Exception as e:
            logger.error(f"Error calculating priority: {e}")
            return 50  # Default middle priority

    async def _initialize_token_tier(self, meme_coin: MemeCoin, token_data: Dict) -> Dict:
        """Initialize tier monitoring for a new token with complete metrics integration"""
        try:
            # Calculate initial metrics
            initial_metrics = {
                'market_cap_usd': token_data.get('market_cap_usd', 0),
                'liquidity': token_data.get('liquidity', 0),
                'holder_count': token_data.get('holder_count', 0),
                'volume_24h': token_data.get('volume_24h', 0),
                'creation_date': token_data.get('creation_date', datetime.now(timezone.utc)),
                'whale_holdings': 0.0,
                'smart_money_flow': 0.0,
                'holder_growth_rate': 0.0,
                'volume_growth_rate': 0.0,
                'liquidity_level': token_data.get('liquidity', 0) / token_data.get('market_cap_usd', 1) if token_data.get('market_cap_usd', 0) > 0 else 0
            }

            # Process metrics through tier processor
            tier_result = await self.tier_processor.process_new_token(
                address=meme_coin.address,
                token_data=initial_metrics
            )

            # Initialize monitoring configuration
            monitoring_config = {
                'whale_threshold': 0.02,  # 2% of supply for whale detection
                'smart_money_threshold': 100000,  # $100k for smart money detection
                'min_liquidity_ratio': 0.1,  # 10% minimum liquidity ratio
                'max_holder_concentration': 0.25,  # 25% maximum holder concentration
                'alert_thresholds': {
                    'price_change': 0.2,  # 20% price change alert
                    'volume_spike': 3.0,  # 3x volume spike alert
                    'liquidity_drop': 0.3  # 30% liquidity drop alert
                },
                'check_frequency': self.tier_manager.get_check_frequency(tier_result.tier_level)
            }

            # Create token tier instance
            token_tier = TokenTier(
                token_id=meme_coin.id,
                token_address=meme_coin.address,
                current_tier=tier_result.tier_level,
                tier_updated_at=datetime.utcnow(),
                last_checked=datetime.utcnow(),
                check_frequency=monitoring_config['check_frequency'],
                next_check_at=datetime.utcnow() + timedelta(seconds=monitoring_config['check_frequency']),
                tier_score=tier_result.tier_score,
                tier_metrics=initial_metrics,
                monitoring_config=monitoring_config,
                processing_priority=self._calculate_priority(tier_result.tier_level, initial_metrics),
                whale_holdings=initial_metrics['whale_holdings'],
                smart_money_flow=initial_metrics['smart_money_flow'],
                liquidity_level=initial_metrics['liquidity_level'],
                holder_count=initial_metrics['holder_count'],
                holder_growth_rate=initial_metrics['holder_growth_rate'],
                volume_growth_rate=initial_metrics['volume_growth_rate'],
                previous_tier=None,
                tier_history=[{
                    'tier': tier_result.tier_level.value,
                    'timestamp': datetime.utcnow().isoformat(),
                    'reason': 'Initial classification',
                    'metrics': initial_metrics
                }],
                signal_history=[],
                memory_allocation=self._calculate_memory_allocation(tier_result.tier_level),
                is_active=True,
                is_monitoring_paused=False,
                last_alert_at=None,
                alert_count=0
            )

            # Create tier transition record
            tier_transition = TierTransition(
                token_tier=token_tier,
                from_tier=None,
                to_tier=tier_result.tier_level,
                reason="Initial token classification",
                metrics_snapshot=initial_metrics
            )

            return {
                'tier_instance': token_tier,
                'transition': tier_transition,
                'monitoring_config': monitoring_config
            }

        except Exception as e:
            logger.error(f"Error initializing tier for token {meme_coin.address}: {e}")
            raise

    async def _start_tier_monitoring(self, token: MemeCoin):
        """Start tier-specific monitoring for a token"""
        try:
            if not token.tier:
                logger.error(f"No tier information for token {token.address}")
                return

            # Prepare monitoring data
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
                'social_sentiment_score': getattr(token, 'social_sentiment_score', 0),
                'liquidity_level': token.liquidity / token.market_cap_usd if token.market_cap_usd > 0 else 0
            }

            # Select monitoring strategy based on tier
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
                    
                    # Handle detected changes
                    if monitoring_result.get('changes_detected'):
                        alerts = monitoring_result.get('alerts', [])
                        
                        async with get_db_session() as db:
                            # Update alert count
                            token.tier.alert_count += len(alerts)
                            token.tier.last_alert_at = datetime.utcnow()
                            
                            # Create alert records
                            for alert in alerts:
                                db.add(TierAlert(
                                    token_tier_id=token.tier.id,
                                    alert_type=alert.get('type'),
                                    severity=alert.get('severity', 'medium'),
                                    message=alert.get('message'),
                                    metrics_snapshot=alert.get('metrics', {})
                                ))
                            
                            await db.commit()
                            
                        logger.info(f"Created {len(alerts)} alerts for {token.address}")

        except Exception as e:
            logger.error(f"Error starting tier monitoring: {e}")

    def _calculate_memory_allocation(self, tier_level: TierLevel) -> float:
        """Calculate memory allocation percentage based on tier"""
        return {
            TierLevel.SIGNAL: 1.0,   # 100% of tier quota
            TierLevel.HOT: 0.8,      # 80% of tier quota
            TierLevel.WARM: 0.6,     # 60% of tier quota
            TierLevel.COLD: 0.4,     # 40% of tier quota
            TierLevel.ARCHIVE: 0.2   # 20% of tier quota
        }.get(tier_level, 0.2)

    def _calculate_tier_score(self, metrics: Dict) -> float:
        """Calculate a score to determine tier priority"""
        try:
            score = 0.0
            
            # Market cap weight (40%)
            if metrics.get('market_cap_usd', 0) > 0:
                score += min((metrics['market_cap_usd'] / 100000), 40)
                
            # Liquidity weight (30%)
            if metrics.get('liquidity', 0) > 0:
                score += min((metrics['liquidity'] / 50000), 30)
                
            # Growth metrics (15% each)
            if metrics.get('liquidity_growth', 0) > 0:
                score += min((metrics['liquidity_growth'] * 100), 15)
                
            if metrics.get('holder_growth', 0) > 0:
                score += min((metrics['holder_growth'] * 100), 15)
                
            return min(score, 100)  # Cap at 100
            
        except Exception as e:
            logger.error(f"Error calculating tier score: {e}")
            return 0.0