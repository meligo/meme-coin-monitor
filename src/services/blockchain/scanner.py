import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from solders.pubkey import Pubkey
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from web3 import Web3
from web3.contract import Contract

from src.config.database import Base, engine, get_db_session
from src.core.models.meme_coin import MemeCoin
from src.enums.monitoring_tier import MonitoringTier

from ...config.database import get_db
from ...config.redis_config import redis_manager
from ...config.settings import MonitoringTier, settings
from ...core.models.meme_coin import MemeCoin

logger = logging.getLogger(__name__)


class BlockchainScanner:
    def __init__(self):
        self.redis = redis_manager
        # Initialize Web3 connection (you'll need to replace with your node URL)
        self.w3 = Web3(Web3.HTTPProvider('YOUR_NODE_URL'))
        # Standard ERC20 ABI for token interaction
        self.erc20_abi = json.loads('[{"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"}]')
        
    async def scan_new_tokens(self) -> List[Dict]:
        """Scan blockchain for newly created tokens"""
        # Get the latest block number
        latest_block = self.w3.eth.block_number
        start_block = latest_block - 1000  # Scan last 1000 blocks
        
        tokens = []
        # Look for Token Creation events
        event_filter = self.w3.eth.filter({
            'fromBlock': start_block,
            'toBlock': 'latest',
            'topics': [
                # Token creation event signature
                self.w3.keccak(text='Transfer(address,address,uint256)').hex(),
                '0x0000000000000000000000000000000000000000000000000000000000000000'  # From zero address (creation)
            ]
        })
        
        events = event_filter.get_all_entries()
        
        for event in events:
            token_address = event['address']
            if await self._is_valid_token(token_address):
                token_info = await self._get_token_info(token_address)
                tokens.append(token_info)
        
        return tokens
        
    async def analyze_contract(self, address: str) -> Dict:
        """Analyze smart contract for potential risks"""
        contract_code = self.w3.eth.get_code(address).hex()
        
        analysis = {
            'risk_factors': [],
            'security_score': 100,
            'has_mint_function': False,
            'has_blacklist': False,
            'modifiable': False,
            'proxy_contract': False
        }
        
        # Check for mint function
        if 'mint' in contract_code:
            analysis['has_mint_function'] = True
            analysis['risk_factors'].append('Has minting capability')
            analysis['security_score'] -= 20
            
        # Check for blacklist functionality
        if 'blacklist' in contract_code or 'exclude' in contract_code:
            analysis['has_blacklist'] = True
            analysis['risk_factors'].append('Has blacklist functionality')
            analysis['security_score'] -= 15
            
        # Check if contract is modifiable
        if 'upgradeable' in contract_code or 'proxy' in contract_code:
            analysis['modifiable'] = True
            analysis['risk_factors'].append('Contract is modifiable')
            analysis['security_score'] -= 25
            
        # Check for proxy patterns
        if 'delegatecall' in contract_code:
            analysis['proxy_contract'] = True
            analysis['risk_factors'].append('Uses proxy pattern')
            analysis['security_score'] -= 10
            
        return analysis
        
    async def get_token_metrics(self, address: str) -> Dict:
        """Get current token metrics from blockchain"""
        try:
            contract = self.w3.eth.contract(address=address, abi=self.erc20_abi)
            
            # Get basic token info
            total_supply = contract.functions.totalSupply().call()
            
            # Get holder count (this would need indexer support in production)
            transfer_events = contract.events.Transfer.get_all_entries()
            unique_holders = set()
            for event in transfer_events:
                unique_holders.add(event['args']['to'])
                unique_holders.add(event['args']['from'])
            holder_count = len(unique_holders)
            
            # Get liquidity information (example using Uniswap V2 pair)
            # In production, you'd need to implement proper DEX liquidity checking
            liquidity = await self._get_liquidity(address)
            
            # Calculate market metrics
            price = await self._get_token_price(address)
            market_cap = price * total_supply
            volume_24h = await self._get_24h_volume(address)
            
            return {
                'holder_count': holder_count,
                'liquidity': liquidity,
                'market_cap': market_cap,
                'volume_24h': volume_24h,
                'price': price,
                'total_supply': total_supply
            }
        except Exception as e:
            print(f"Error getting metrics for {address}: {str(e)}")
            return {
                'holder_count': 0,
                'liquidity': 0.0,
                'market_cap': 0.0,
                'volume_24h': 0.0,
                'price': 0.0,
                'total_supply': 0
            }
            
    async def get_tier_tokens(self, tier: MonitoringTier) -> List[MemeCoin]:
        """
        Get all tokens in specified monitoring tier
        
        Args:
            tier (MonitoringTier): The monitoring tier to query for
            
        Returns:
            List[MemeCoin]: List of tokens in the specified tier
            
        Raises:
            Exception: If database query fails
        """
        try:
            async with get_db_session() as db:
                try:
                    # Query tokens in the specified tier
                    result = await db.execute(
                        select(MemeCoin).where(
                            and_(
                                MemeCoin.tier == tier,
                                or_(
                                    MemeCoin.risk_score < 80,  # Exclude extremely high-risk tokens
                                    MemeCoin.risk_score.is_(None)
                                ),
                                MemeCoin.is_active == True  # Only get active tokens
                            )
                        ).order_by(
                            MemeCoin.market_cap_usd.desc()  # Order by market cap
                        )
                    )
                    
                    tokens = result.scalars().all()
                    logger.info(
                        f"Found {len(tokens)} active tokens in {tier.name} tier"
                    )
                    return tokens
                    
                except Exception as db_error:
                    logger.error(
                        f"Database error querying {tier.name} tier tokens: {str(db_error)}"
                    )
                    await db.rollback()
                    raise
                    
        except Exception as e:
            logger.error(f"Error in get_tier_tokens for tier {tier.name}: {str(e)}")
            return []  # Return empty list on error rather than raising
            
    async def transition_tier(self, token: MemeCoin, new_tier: MonitoringTier):
        """Handle token transition between monitoring tiers"""
        async with get_db_session() as db:

            try:
                # Update token tier
                token.tier = new_tier
                token.updated_at = datetime.utcnow()
                
                # Clear old cache
                old_key = self.redis.get_tier_key(token.address, token.tier.value)
                self.redis.client.delete(old_key)
                
                # Update database
                db.add(token)
                await db.commit()
                
                # Initialize new tier monitoring
                metrics = await self.get_token_metrics(token.address)
                self.redis.cache_coin_data(
                    token.address,
                    new_tier.value,
                    metrics,
                    settings.CACHE_TTLS[new_tier]
                )
                
            except Exception as e:
                db.rollback()
                raise e
            
    async def _is_valid_token(self, address: str) -> bool:
        """Check if address is a valid token contract"""
        try:
            contract = self.w3.eth.contract(address=address, abi=self.erc20_abi)
            # Try to call basic ERC20 functions
            name = contract.functions.name().call()
            symbol = contract.functions.symbol().call()
            total_supply = contract.functions.totalSupply().call()
            return True
        except:
            return False
            
    async def _get_token_info(self, address: str) -> Dict:
        """Get basic token information"""
        contract = self.w3.eth.contract(address=address, abi=self.erc20_abi)
        return {
            'address': address,
            'name': contract.functions.name().call(),
            'symbol': contract.functions.symbol().call(),
            'total_supply': contract.functions.totalSupply().call()
        }
        
    async def _get_liquidity(self, token_address: str) -> float:
        """Get token liquidity (implement for specific DEX)"""
        # This would need to be implemented based on the specific DEX you're monitoring
        # Example: Check Uniswap V2 pair liquidity
        return 0.0
        
    async def _get_token_price(self, token_address: str) -> float:
        """Get current token price"""
        # Implement price fetching from DEX or price feed
        return 0.0
        
    async def _get_24h_volume(self, token_address: str) -> float:
        """Get 24h trading volume"""
        # Implement volume calculation from DEX events
        return 0.0