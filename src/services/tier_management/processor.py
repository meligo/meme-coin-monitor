import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db_session
from src.core.models.base import Base
from src.core.models.meme_coin import MemeCoin
from src.core.models.tier_models import TierTransition, TokenTier

from .monitoring import TierMonitor
from .tier_manager import TierLevel, TierManager

logger = logging.getLogger(__name__)

class JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle timedelta objects"""
    def default(self, obj):
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        return super().default(obj)

class TierProcessor:
    def __init__(self):
        self.tier_manager = TierManager()
        self.tier_monitor = TierMonitor()
        self.processing_queue = asyncio.Queue()
        self._running = False
        self._current_task = None
        self.processed_count = 0
        self.error_count = 0
        self.last_processed = None
        self.json_encoder = JSONEncoder()

    async def start(self) -> None:
        """Start the tier processor"""
        if self._running:
            logger.warning("Tier processor is already running")
            return
        await self.start_services()

        self._running = True
        self._current_task = asyncio.create_task(self._process_queue())
        logger.info("✅ Tier processor started")
        
    async def start_services(self):
        """Start all dependent services"""
        try:
            # Start RPC manager
            if hasattr(self.rpc_manager, 'start'):
                await self.rpc_manager.start()
                
            # Start tier services
            if hasattr(self.tier_processor, 'start'):
                await self.tier_processor.start()
            if hasattr(self.tier_monitor, 'start'):
                await self.tier_monitor.start()
                
            # Start analysis services
            if hasattr(self.wallet_analyzer, 'start'):
                await self.wallet_analyzer.start()
            if hasattr(self.rug_detector, 'start'):
                await self.rug_detector.start()
                
            # Start metrics services
            if hasattr(self.holder_metrics_updater, 'start'):
                await self.holder_metrics_updater.start()
            if hasattr(self.token_metrics, 'start'):
                await self.token_metrics.start()
                
            # Start main processor
            if not self._running:
                self._running = True
                self._processing_task = asyncio.create_task(self._process_queue())
                logger.info("Token processor and services started")
                
        except Exception as e:
            logger.error(f"Error starting services: {e}")
            raise

    async def stop(self) -> None:
        """Stop the tier processor"""
        if not self._running:
            return

        self._running = False
        if self._current_task:
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

        logger.info(f"🛑 Tier processor stopped. Processed: {self.processed_count}, Errors: {self.error_count}")

    def _serialize_json(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize data to JSON-compatible format"""
        return json.loads(self.json_encoder.encode(data))

    async def process_new_token(self, address: str, token_data: Dict[str, Any]) -> None:
        """Process a newly detected token"""
        try:
            async with get_db_session() as db:
                try:
                    # Calculate initial tier
                    initial_tier_data = await self._calculate_initial_tier(token_data)
                    
                    # Create tier entry
                    tier = await self._create_tier_entry(db, address, initial_tier_data)
                    
                    # Queue for monitoring
                    await self.queue_token(address, priority=True)
                    
                    logger.info(f"✨ New token {address} initialized in {initial_tier_data['tier'].value} tier")
                    
                except Exception as e:
                    logger.error(f"Error processing new token {address}: {e}")
                    raise
                
        except Exception as e:
            logger.error(f"Database error processing new token {address}: {e}")
            self.error_count += 1

    async def _calculate_initial_tier(self, token_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate initial tier and monitoring parameters"""
        try:
            # Get initial tier based on metrics
            initial_tier = await self.tier_manager.assign_initial_tier(token_data)
            
            # Get monitoring configuration for tier
            monitoring_config = self.tier_manager.get_monitoring_config(initial_tier)
            
            # Calculate initial metrics
            initial_metrics = {
                'market_cap_usd': token_data.get('market_cap_usd', 0),
                'liquidity': token_data.get('liquidity', 0),
                'holder_count': token_data.get('holder_count', 0),
                'volume_24h': token_data.get('volume_24h', 0),
                'age': token_data.get('age', 0)  # Convert timedelta to seconds
            }
            
            return {
                'tier': initial_tier,
                'config': self._serialize_json(monitoring_config),
                'metrics': self._serialize_json(initial_metrics)
            }
            
        except Exception as e:
            logger.error(f"Error calculating initial tier: {e}")
            raise

    async def _create_tier_entry(self, db: AsyncSession, address: str, tier_data: Dict[str, Any]) -> TokenTier:
        """Create new tier entry in database"""
        try:
            now = datetime.utcnow()
            check_frequency = tier_data['config']['check_frequency']
            
            tier = TokenTier(
                token_address=address,
                current_tier=tier_data['tier'],
                tier_updated_at=now,
                last_checked=now,
                check_frequency=check_frequency,
                next_check_at=now + timedelta(seconds=check_frequency),
                tier_metrics=tier_data['metrics'],
                monitoring_config=tier_data['config'],
                processing_priority=self._calculate_priority(tier_data),
                is_active=True,
                is_monitoring_paused=False,
                alert_count=0
            )
            
            db.add(tier)
            await db.commit()
            await db.refresh(tier)
            return tier
            
        except SQLAlchemyError as e:
            await db.rollback()
            logger.error(f"Database error creating tier entry: {e}")
            raise
        except Exception as e:
            logger.error(f"Error creating tier entry: {e}")
            raise

    def _calculate_priority(self, tier_data: Dict[str, Any]) -> int:
        """Calculate processing priority (1-100)"""
        try:
            base_priority = {
                TierLevel.SIGNAL: 90,
                TierLevel.HOT: 70,
                TierLevel.WARM: 50,
                TierLevel.COLD: 30,
                TierLevel.ARCHIVE: 10
            }[tier_data['tier']]
            
            metrics = tier_data['metrics']
            
            # Adjust based on market cap and liquidity
            mcap_score = min((metrics.get('market_cap_usd', 0) / 100000), 5)  # Max 5 points
            liq_score = min((metrics.get('liquidity', 0) / 50000), 5)  # Max 5 points
            
            return min(100, int(base_priority + mcap_score + liq_score))
            
        except Exception as e:
            logger.error(f"Error calculating priority: {e}")
            return 50  # Default middle priority

    async def queue_token(self, address: str, priority: bool = False) -> None:
        """Add token to processing queue"""
        try:
            queue_item = {
                'address': address,
                'priority': priority,
                'queued_at': datetime.utcnow()
            }
            
            await self.processing_queue.put(queue_item)
            logger.debug(f"Queued token {address} for processing (Priority: {priority})")
            
        except Exception as e:
            logger.error(f"Error queuing token {address}: {e}")

    async def _process_queue(self) -> None:
        """Process tokens from the queue"""
        while self._running:
            try:
                if self.processing_queue.empty():
                    await asyncio.sleep(1)
                    continue

                queue_item = await self.processing_queue.get()
                address = queue_item['address']
                
                async with get_db_session() as db:
                    try:
                        # Use SQLAlchemy async
                        result = await db.execute(
                            select(MemeCoin).where(MemeCoin.address == address)
                        )
                        token = result.scalar_one_or_none()

                        if token:
                            monitoring_result = await self.tier_monitor.monitor_token(db, token)
                            if monitoring_result:
                                await self._handle_monitoring_result(db, token, monitoring_result)
                                self.processed_count += 1
                                self.last_processed = datetime.utcnow()
                        
                    except Exception as e:
                        logger.error(f"Error processing token {address}: {e}")
                        self.error_count += 1
                    finally:
                        self.processing_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue processing: {e}")
                await asyncio.sleep(1)

    async def _handle_monitoring_result(
        self, 
        db: AsyncSession, 
        token: MemeCoin, 
        monitoring_result: Dict[str, Any]
    ) -> None:
        """Handle monitoring results and update tier if needed"""
        try:
            if not hasattr(token, 'tier') or token.tier is None:
                logger.error(f"Token {token.address} has no tier information")
                return

            # Check if tier update needed
            new_tier = await self.tier_manager.update_tier(
                token.tier.current_tier,
                monitoring_result['metrics']
            )
            
            if new_tier != token.tier.current_tier:
                # Create tier transition record
                transition = TierTransition(
                    token_tier_id=token.tier.id,
                    from_tier=token.tier.current_tier,
                    to_tier=new_tier,
                    reason=self._get_transition_reason(monitoring_result),
                    metrics_snapshot=self._serialize_json(monitoring_result['metrics'])
                )
                
                # Update tier configuration
                new_config = self.tier_manager.get_monitoring_config(new_tier)
                token.tier.current_tier = new_tier
                token.tier.tier_updated_at = datetime.utcnow()
                token.tier.monitoring_config = self._serialize_json(new_config)
                token.tier.check_frequency = new_config['check_frequency']
                token.tier.processing_priority = self._calculate_priority({
                    'tier': new_tier,
                    'metrics': monitoring_result['metrics']
                })
                
                db.add(transition)
                await db.commit()
                
                logger.info(f"Token {token.address} moved from {transition.from_tier.value} to {new_tier.value}")
                
        except Exception as e:
            await db.rollback()
            logger.error(f"Error handling monitoring result: {e}")
            raise

    def _get_transition_reason(self, monitoring_result: Dict[str, Any]) -> str:
        """Generate reason for tier transition"""
        if monitoring_result.get('alert_type'):
            return f"Alert triggered: {monitoring_result['alert_type']}"
        elif monitoring_result.get('analysis'):
            return f"Analysis result: {monitoring_result['analysis']}"
        return "Regular tier update based on metrics"