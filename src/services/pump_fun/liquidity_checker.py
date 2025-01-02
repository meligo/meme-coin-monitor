import asyncio
import base64
import logging
import struct
from typing import Dict, List, Optional, Union

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

class LiquidityChecker:
    """Tracks token liquidity changes"""

    def __init__(self, rpc_client: AsyncClient):
        self.rpc_client = rpc_client
        
    async def check_liquidity(self, token_address: str, bonding_curve_address: str) -> Dict[str, float]:
        """
        Track liquidity changes for a token by analyzing its bonding curve
        
        Args:
            token_address (str): The token's address
            bonding_curve_address (str): The bonding curve contract address
            
        Returns:
            Dict containing:
            - current_liquidity (float): Current liquidity amount
            - peak_liquidity (float): Highest historical liquidity amount
            - liquidity_change_percent (float): Percentage change from peak
        """
        try:
            # Get current bonding curve state
            curve_pubkey = Pubkey.from_string(bonding_curve_address)
            response = await self.rpc_client.get_account_info(curve_pubkey)
            
            if not response.value or not response.value.data:
                logger.warning(f"No data found for bonding curve: {bonding_curve_address}")
                return self._create_liquidity_result(0.0, 0.0)

            try:
                # Get raw data
                current_data = response.value.data[0]
                
                # Handle different data types
                if isinstance(current_data, str):
                    decoded_data = base64.b64decode(current_data)
                elif isinstance(current_data, bytes):
                    decoded_data = current_data
                elif isinstance(current_data, int):
                    decoded_data = current_data.to_bytes(8, byteorder='little')
                else:
                    logger.warning(f"Unexpected data type: {type(current_data)}")
                    return self._create_liquidity_result(0.0, 0.0)

                # Skip discriminator (first 8 bytes)
                if len(decoded_data) < 8:
                    logger.warning("Data too short to contain discriminator")
                    return self._create_liquidity_result(0.0, 0.0)
                    
                curve_data = decoded_data[8:]
                
                # Now get liquidity
                current_liquidity = await self._get_current_liquidity(curve_data)
                if current_liquidity is None:
                    return self._create_liquidity_result(0.0, 0.0)
                    
            except Exception as e:
                logger.error(f"Error decoding current liquidity data: {e}")
                return self._create_liquidity_result(0.0, 0.0)

            # Get historical transactions to find peak liquidity
            signatures = await self.rpc_client.get_signatures_for_address(curve_pubkey)
            if not signatures.value:
                return self._create_liquidity_result(current_liquidity, current_liquidity)

            # Find peak liquidity
            peak_liquidity = current_liquidity
            
            # Process transactions in batches
            batch_size = 5
            signatures_list = signatures.value
            batches = [signatures_list[i:i + batch_size] for i in range(0, len(signatures_list), batch_size)]
            
            for batch in batches:
                tasks = []
                for sig in batch:
                    task = asyncio.create_task(self._process_transaction(sig.signature))
                    tasks.append(task)
                
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in batch_results:
                    if isinstance(result, Exception):
                        continue
                    if result > peak_liquidity:
                        peak_liquidity = result

            # Calculate percentage change
            liquidity_change = 0.0
            if peak_liquidity > 0:
                liquidity_change = ((peak_liquidity - current_liquidity) / peak_liquidity) * 100

            return {
                'current_liquidity': float(current_liquidity),
                'peak_liquidity': float(peak_liquidity),
                'liquidity_change_percent': float(liquidity_change)
            }

        except Exception as e:
            logger.error(f"Error checking liquidity for {token_address}: {e}")
            return self._create_liquidity_result(0.0, 0.0)

    async def _process_transaction(self, signature: str) -> float:
        """Process a single transaction to find liquidity value"""
        try:
            tx = await self.rate_limiter.call(
                self.rpc_client.get_transaction,
                signature,
                encoding="jsonParsed",
                commitment=Commitment("confirmed"),
                max_supported_transaction_version=0  # Changed to camelCase
            )
            if not tx.value or not tx.value.meta or not tx.value.meta.log_messages:
                return 0.0

            max_liquidity = 0.0
            for log in tx.value.meta.log_messages:
                if "real_sol_reserves" in log:
                    try:
                        liquidity_str = log.split("real_sol_reserves=")[1].split()[0]
                        liquidity = float(liquidity_str)
                        max_liquidity = max(max_liquidity, liquidity)
                    except (IndexError, ValueError) as e:
                        continue
            
            return max_liquidity
        except Exception as e:
            logger.debug(f"Error processing transaction {signature}: {e}")
            return 0.0

    def _create_liquidity_result(self, current: float, peak: float) -> Dict[str, float]:
        """Create a default liquidity result"""
        return {
            'current_liquidity': float(current),
            'peak_liquidity': float(peak),
            'liquidity_change_percent': 0.0
        }

    async def _get_current_liquidity(self, data: Union[bytes, bytearray, List[int]]) -> Optional[float]:
        try:
            if not data:
                logger.info("No liquidity data yet - new token")
                return 0.0
            # Ensure we have bytes
            if isinstance(data, (list, bytearray)):
                data = bytes(data)
            elif not isinstance(data, bytes):
                logger.error(f"Invalid data type for liquidity extraction: {type(data)}")
                return 0.0  # Return 0 instead of None for new tokens

            # Define the structure for real_sol_reserves
            STRUCT = struct.Struct("<QQQQQB")  # 5 uint64 and 1 uint8
            
            if len(data) < STRUCT.size:
                logger.warning(f"Data length ({len(data)}) is less than required ({STRUCT.size})")
                return 0.0  # Return 0 for new tokens
                
            try:
                unpacked = STRUCT.unpack(data[:STRUCT.size])
                real_sol_reserves = unpacked[3]  # Fourth field is real_sol_reserves
            except struct.error:
                logger.error("Failed to unpack data structure")
                return 0.0  # Return 0 instead of None
            
            # Convert lamports to SOL
            return float(real_sol_reserves) / 1e9

        except Exception as e:
            logger.error(f"Error extracting current liquidity: {e}")
            return 0.0  # Return 0 instead of None