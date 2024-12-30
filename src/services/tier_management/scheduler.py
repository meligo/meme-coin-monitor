import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from src.core.models.tier_models import TokenTier
from src.core.models.tier_level import TierLevel
from src.services.tier_management.monitoring import TierMonitor

logger = logging.getLogger(__name__)

class TierScheduler:
    def __init__(self, monitor: TierMonitor):
        self.monitor = monitor
        self.check_intervals = {
            TierLevel.SIGNAL: 60,     # Check every 1 minute
            TierLevel.HOT: 300,       # Check every 5 minutes
            TierLevel.WARM: 900,      # Check every 15 minutes
            TierLevel.COLD: 3600      # Check every 1 hour
        }
        self.running_tasks: Dict[str, asyncio.Task] = {}

    async def start(self, db: Session):
        """Start the tier scheduler"""
        try:
            await self.schedule_all_tokens(db)
            logger.info("Tier scheduler started successfully")
        except Exception as e:
            logger.error(f"Error starting tier scheduler: {e}")

    async def stop(self):
        """Stop all running monitoring tasks"""
        for task in self.running_tasks.values():
            task.cancel()
        self.running_tasks.clear()
        logger.info("Tier scheduler stopped")

    async def schedule_all_tokens(self, db: Session):
        """Schedule monitoring for all active tokens"""
        try:
            # Get all active token tiers
            active_tiers = db.query(TokenTier).filter(
                TokenTier.is_active == True,
                TokenTier.is_monitoring_paused == False
            ).all()

            for tier in active_tiers:
                await self.schedule_token(db, tier)

        except Exception as e:
            logger.error(f"Error scheduling tokens: {e}")

    async def schedule_token(self, db: Session, tier: TokenTier):
        """Schedule monitoring for a single token"""
        if tier.token_address in self.running_tasks:
            logger.warning(f"Token {tier.token_address} already scheduled")
            return

        try:
            check_interval = self.check_intervals.get(tier.current_tier)
            if not check_interval:
                logger.error(f"Invalid tier level for token {tier.token_address}")
                return

            task = asyncio.create_task(
                self._monitor_token_loop(db, tier, check_interval)
            )
            self.running_tasks[tier.token_address] = task
            logger.info(f"Scheduled monitoring for token {tier.token_address}")

        except Exception as e:
            logger.error(f"Error scheduling token {tier.token_address}: {e}")

    async def unschedule_token(self, token_address: str):
        """Remove token from monitoring schedule"""
        if token_address in self.running_tasks:
            self.running_tasks[token_address].cancel()
            del self.running_tasks[token_address]
            logger.info(f"Unscheduled monitoring for token {token_address}")

    async def update_token_schedule(self, db: Session, tier: TokenTier):
        """Update monitoring schedule for a token"""
        await self.unschedule_token(tier.token_address)
        await self.schedule_token(db, tier)

    async def get_active_schedules(self) -> List[Dict]:
        """Get information about currently monitored tokens"""
        return [
            {
                'token_address': addr,
                'task_status': task._state if hasattr(task, '_state') else 'unknown'
            }
            for addr, task in self.running_tasks.items()
        ]

    async def _monitor_token_loop(self, db: Session, tier: TokenTier, interval: int):
        """Main monitoring loop for a token"""
        while True:
            try:
                # Check if monitoring should still be active
                db.refresh(tier)
                if not tier.is_active or tier.is_monitoring_paused:
                    logger.info(f"Stopping monitoring for inactive token {tier.token_address}")
                    break

                # Check if it's time for next check
                if tier.next_check_at and datetime.utcnow() < tier.next_check_at:
                    await asyncio.sleep(1)
                    continue

                # Perform monitoring
                await self.monitor.monitor_token(db, tier.token)

                # Update next check time
                tier.last_checked = datetime.utcnow()
                tier.next_check_at = tier.last_checked + timedelta(seconds=interval)
                db.commit()

                # Sleep until next check
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                logger.info(f"Monitoring cancelled for token {tier.token_address}")
                break
            except Exception as e:
                logger.error(f"Error monitoring token {tier.token_address}: {e}")
                await asyncio.sleep(interval)  # Still wait before retrying

        # Cleanup
        if tier.token_address in self.running_tasks:
            del self.running_tasks[tier.token_address]