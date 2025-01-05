import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List

from solders.pubkey import Pubkey
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db_session
from src.config.settings import settings
from src.core.models.meme_coin import HolderSnapshot, MemeCoin
from src.services.holder_analysis.distribution_calculator import DistributionCalculator
from src.utils.rpc_manager import RPCManager

logger = logging.getLogger(__name__)

class HolderMetricsUpdater:
    def __init__(self):
        self.distribution_calculator = DistributionCalculator()
        self.rpc_manager = RPCManager()
        self._running = False
        self._update_task = None
        self.update_interval = 300  # 5 minutes
        self.token_program_id = Pubkey.from_string(settings.system_token_program)


    async def start_updating(self) -> None:
        """Start the metrics updating loop"""
        if self._running:
            logger.warning("Metrics updater is already running")
            return

        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("Holder metrics updater started")

    async def stop_updating(self) -> None:
        """Stop the metrics updating loop"""
        if not self._running:
            return

        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        logger.info("Holder metrics updater stopped")

    async def _update_loop(self) -> None:
        """Main update loop"""
        while self._running:
            try:
                async with get_db_session() as db:
                    # Get all active tokens
                    result = await db.execute(
                        select(MemeCoin)
                        .where(MemeCoin.is_active == True)
                    )
                    tokens = result.scalars().all()

                    for token in tokens:
                        try:
                            # Get current holders (you'll need to implement this)
                            holders = await self._get_current_holders(token.address)
                            # Update metrics
                            await self.update_holder_metrics(db, token.address, holders)
                        except Exception as e:
                            logger.error(f"Error updating metrics for {token.address}: {e}")
                            continue

            except Exception as e:
                logger.error(f"Error in metrics update loop: {e}")

            await asyncio.sleep(self.update_interval)

    async def _get_current_holders(self, token_address: str) -> List[Dict]:
        """
        Get current holders for a token using Token Program accounts
        
        Args:
            token_address: The token's mint address
            
        Returns:
            List[Dict]: List of holder information with wallet addresses and balances
        """
        try:
            logger.info("bytes token_address: %s", token_address)
            # Create filters for token program accounts
            filters = [
                { 'memcmp': { 'offset': 0, 'bytes': token_address } },
                { 'dataSize': 165 } 
            ]

            # Get all token accounts for this mint
            response = await self.rpc_manager.get_program_accounts(
                str(self.token_program_id),  # Convert Pubkey to string if needed
                commitment="confirmed",
                encoding="jsonParsed",
                filters=filters
            )

            if not response or not response.value:
                logger.debug(f"No holders found for token {token_address}")
                return []

            holders = []
            for account in response.value:
                try:
                    parsed_data = account.account.data.parsed
                    if not parsed_data or not parsed_data.get('info'):
                        continue

                    info = parsed_data['info']
                    amount = int(info['tokenAmount']['amount'])
                    decimals = info['tokenAmount']['decimals']

                    # Only include non-zero balances
                    if amount > 0:
                        holders.append({
                            'wallet': info['owner'],
                            'balance': amount / (10 ** decimals),
                            'decimals': decimals
                        })

                except (KeyError, AttributeError, ValueError) as e:
                    logger.debug(f"Error parsing holder account {account.pubkey}: {e}")
                    continue

            return holders

        except Exception as e:
            logger.error(f"Error getting holders for {token_address}: {e}")
            return []

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