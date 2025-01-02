import logging
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.meme_coin import HolderSnapshot, MemeCoin
from src.services.holder_analysis.distribution_calculator import DistributionCalculator

logger = logging.getLogger(__name__)

class HolderMetricsUpdater:
    def __init__(self):
        self.distribution_calculator = DistributionCalculator()

    async def update_holder_metrics(self, db: AsyncSession, token_address: str, holders: List[Dict]) -> None:
        try:
            if not holders:
                return

            # First get the token ID from the MemeCoin table
            result = await db.execute(
                select(MemeCoin).where(MemeCoin.address == token_address)
            )
            token = result.scalar_one_or_none()
            
            if not token:
                logger.error(f"Token {token_address} not found")
                return

            timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

            # Calculate metrics
            holder_metrics = {
                'total_holders': len(holders),
                'unique_wallets': len({h['wallet'] for h in holders}),
                'distribution': await self.distribution_calculator.calculate_distribution_metrics(holders),
                'concentration_metrics': await self.distribution_calculator.calculate_concentration_metrics(
                    holders, 
                    sum(h['balance'] for h in holders)
                )
            }

            # Update token metrics
            stmt = update(MemeCoin).where(
                MemeCoin.id == token.id
            ).values(
                holder_count=holder_metrics['total_holders'],
                holder_distribution=holder_metrics['distribution'],
                updated_at=timestamp
            )
            await db.execute(stmt)

            # Create snapshot
            snapshot_stmt = insert(HolderSnapshot).values(
                token_id=token.id,
                timestamp=timestamp,
                holder_count=holder_metrics['total_holders'],
                holder_distribution=holder_metrics['distribution'],
                concentration_metrics=holder_metrics['concentration_metrics']
            ).on_conflict_do_update(
                index_elements=['token_id', 'timestamp'],
                set_={
                    'holder_count': holder_metrics['total_holders'],
                    'holder_distribution': holder_metrics['distribution'],
                    'concentration_metrics': holder_metrics['concentration_metrics']
                }
            )
            await db.execute(snapshot_stmt)
            await db.commit()

        except Exception as e:
            logger.error(f"Error updating holder metrics: {e}")
            await db.rollback()
            raise