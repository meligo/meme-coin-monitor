import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.rpc.responses import GetProgramAccountsJsonParsedResp
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.config.settings import settings
from src.core.models import MemeCoin, TokenTier
from src.core.models.tier_level import TierLevel
from src.services.analysis.rug_detector import RugDetector
from src.services.pump_fun.liquidity_checker import LiquidityChecker

logger = logging.getLogger(__name__)

class TokenMonitor:
    def __init__(self):
        self.rpc_client = AsyncClient(settings['PUMP_PROGRAM'])
        self.rug_detector = RugDetector()
        self.liquidity_checker = LiquidityChecker(self.rpc_client)
        self.is_running = False
        self.monitoring_tasks = {}
        self.task_lock = asyncio.Lock()

    async def start_monitoring(self):
        """Start the token monitoring system"""
        self.is_running = True
        asyncio.create_task(self._run_monitoring_loop())
        logger.info("Token monitoring system started")

    async def _run_monitoring_loop(self):
        """Main monitoring loop that runs continuously"""
        while self.is_running:
            try:
                # Get tokens that need checking
                db = next(get_db())
                current_time = datetime.now(timezone.utc)
                
                # Query tokens due for checking
                tokens_to_check = db.query(TokenTier).filter(
                    TokenTier.is_active == True,
                    TokenTier.is_monitoring_paused == False,
                    TokenTier.next_check_at <= current_time
                ).all()

                for token in tokens_to_check:
                    await self._ensure_token_task(token.token_address)

                # Clean up completed tasks
                await self._cleanup_tasks()

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)
            finally:
                db.close()

    async def _ensure_token_task(self, token_address: str):
        """Ensure a monitoring task exists for the token"""
        async with self.task_lock:
            if (token_address not in self.monitoring_tasks or 
                self.monitoring_tasks[token_address].done()):
                task = asyncio.create_task(
                    self._monitor_token(token_address),
                    name=f"monitor_{token_address}"
                )
                self.monitoring_tasks[token_address] = task

    async def _cleanup_tasks(self):
        """Clean up completed monitoring tasks"""
        async with self.task_lock:
            completed_tasks = [
                addr for addr, task in self.monitoring_tasks.items()
                if task.done()
            ]
            for addr in completed_tasks:
                # Handle any exceptions from the task
                try:
                    await self.monitoring_tasks[addr]
                except Exception as e:
                    logger.error(f"Task for {addr} failed: {e}")
                del self.monitoring_tasks[addr]

    async def _monitor_token(self, token_address: str):
        """Monitor a specific token for changes and potential rug pulls"""
        try:
            db = next(get_db())
            
            # Get token data
            token = db.query(TokenTier).filter(
                TokenTier.token_address == token_address
            ).first()
            
            if not token:
                logger.error(f"Token {token_address} not found")
                return

            # Get meme coin data
            meme_coin = db.query(MemeCoin).filter(
                MemeCoin.address == token_address
            ).first()

            if not meme_coin:
                logger.error(f"MemeCoin {token_address} not found")
                return

            # Get bonding curve data
            mint = Pubkey.from_string(token_address)
            bonding_curve_address, _ = self._get_associated_bonding_curve_address(
                mint,
                Pubkey.from_string(settings['PUMP_PROGRAM'])
            )

            # Get current metrics with bonding curve data
            current_metrics = await self._get_current_metrics(token_address, bonding_curve_address)
            
            # Get historical metrics from the database
            historical_metrics = {
                'liquidity': meme_coin.liquidity,
                'volume_24h': meme_coin.volume_24h_usd,
                'holder_count': meme_coin.holder_count,
                'market_cap_usd': meme_coin.market_cap_usd,
                'smart_money_flow': meme_coin.smart_money_flow
            }

            # Run rug detection analysis
            risk_score, alerts, risk_factors = await self.rug_detector.analyze_token(
                current_metrics,
                historical_metrics,
                {'tier_history': token.tier_history}
            )

            # Update token status based on analysis
            await self._update_token_status(
                db,
                token,
                meme_coin,
                current_metrics,
                risk_score,
                alerts
            )

            db.commit()

        except Exception as e:
            logger.error(f"Error monitoring token {token_address}: {e}")
            db.rollback()
        finally:
            db.close()

    async def stop_monitoring(self):
        """Stop the monitoring system"""
        self.is_running = False
        
        # Cancel all running tasks
        async with self.task_lock:
            for task in self.monitoring_tasks.values():
                if not task.done():
                    task.cancel()
            
            # Wait for all tasks to complete
            await asyncio.gather(*self.monitoring_tasks.values(), return_exceptions=True)
        
        logger.info("Token monitoring system stopped")

    # [Rest of the methods remain the same...]