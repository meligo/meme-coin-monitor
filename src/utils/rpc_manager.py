import asyncio
import json
import logging
import random
from typing import Any, Dict, List, Optional

import aiohttp

from src.config.settings import settings

logger = logging.getLogger(__name__)

class RPCManager:
    def __init__(self):
        self.endpoints = settings.rpc_config.get("endpoints", [])
        if not self.endpoints:
            raise ValueError("No RPC endpoints configured")
        
        self.active_endpoint_index = 0
        self.health_status: List[bool] = []
        self.error_counts: List[int] = []
        self._health_check_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._initialized = False
        
        # Configuration
        self.health_check_interval = settings.rpc_config["health_check_interval"]
        self.error_threshold = settings.rpc_config["error_threshold"]
        self.max_retries = settings.rpc_config["max_retries"]
        self.retry_delay = settings.rpc_config["retry_delay"]
        
    async def initialize(self):
        """Initialize RPC clients and start health checks"""
        if self._initialized:
            return
            
        try:
            # Initialize health tracking
            self.health_status = [True for _ in self.endpoints]
            self.error_counts = [0 for _ in self.endpoints]

            # Start health check task
            self._health_check_task = asyncio.create_task(
                self._run_health_checks()
            )
            
            self._initialized = True
            logger.info("RPC Manager initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing RPC Manager: {e}")
            raise

    async def cleanup(self):
        """Cleanup RPC manager resources"""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            
        self._initialized = False

    async def execute(self, method: str, *args, **kwargs) -> Any:
        """Execute RPC method with automatic failover"""
        if not self._initialized:
            await self.initialize()
            
        last_error = None
        retries = 0
        
        while retries <= self.max_retries:
            try:
                async with self._lock:
                    # Ensure index is valid
                    self.active_endpoint_index = self.active_endpoint_index % len(self.endpoints)
                    endpoint = self.endpoints[self.active_endpoint_index]['url']

                # Create RPC request payload
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": list(args)  # Convert args to list for JSON
                }
                
                if kwargs:
                    # If there are kwargs, add them as the last parameter
                    if payload["params"]:
                        payload["params"].append(kwargs)
                    else:
                        payload["params"] = [kwargs]

                # Make the request using aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        endpoint,
                        headers={"Content-Type": "application/json"},
                        json=payload
                    ) as response:
                        response_data = await response.json()
                        
                        if "error" in response_data:
                            raise Exception(response_data["error"])
                            
                        return response_data["result"]

            except Exception as e:
                last_error = e
                logger.error(
                    f"RPC call failed:\n"
                    f"Method: {method}\n"
                    f"Args: {args}\n"
                    f"Kwargs: {kwargs}\n"
                    f"Endpoint: {self.endpoints[self.active_endpoint_index]['url']}\n"
                    f"Error: {str(e)}\n"
                    f"Error Type: {type(e)}\n"
                    f"Retry: {retries + 1}/{self.max_retries}"
                )
                
                # Update error count and check health
                self.error_counts[self.active_endpoint_index] += 1
                if self.error_counts[self.active_endpoint_index] >= self.error_threshold:
                    self.health_status[self.active_endpoint_index] = False
                    await self._switch_endpoint()

                retries += 1
                if retries < self.max_retries:
                    await asyncio.sleep(self.retry_delay * (2 ** retries))

        logger.error(f"All RPC attempts failed for method: {method}")
        raise last_error if last_error else ValueError("RPC execution failed")

    async def _switch_endpoint(self) -> None:
        """Switch to next healthy endpoint"""
        async with self._lock:
            original_index = self.active_endpoint_index
            
            # Try each endpoint in order
            for _ in range(len(self.endpoints)):
                self.active_endpoint_index = (self.active_endpoint_index + 1) % len(self.endpoints)
                if self.health_status[self.active_endpoint_index]:
                    logger.info(f"Switched to RPC endpoint: {self.endpoints[self.active_endpoint_index]['url']}")
                    return
                    
            # If no healthy endpoints, reset health status and try again
            logger.warning("No healthy RPC endpoints available, resetting health status")
            self.health_status = [True] * len(self.endpoints)
            self.error_counts = [0] * len(self.endpoints)
            
            # Pick a random endpoint to try
            self.active_endpoint_index = random.randint(0, len(self.endpoints) - 1)
            logger.info(f"Reset health status and switched to endpoint: {self.endpoints[self.active_endpoint_index]['url']}")

    async def _run_health_checks(self):
        """Run periodic health checks on all endpoints"""
        while True:
            try:
                for i, endpoint in enumerate(self.endpoints):
                    # Only check endpoints that are currently marked unhealthy
                    if not self.health_status[i]:
                        try:
                            response = await self.execute("getHealth")
                            if response == "ok":
                                self.health_status[i] = True
                                self.error_counts[i] = 0
                                logger.debug(f"Endpoint {endpoint['url']} recovered")
                        except Exception as e:
                            if self.health_status[i]:  # Only log if status changed
                                logger.warning(f"Health check failed for {endpoint['url']}: {e}")
                            self.health_status[i] = False
                            self.error_counts[i] += 1

                await asyncio.sleep(self.health_check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health check loop: {e}")
                await asyncio.sleep(self.health_check_interval)

    def get_status(self) -> Dict:
        """Get current RPC manager status"""
        return {
            "active_endpoint": self.endpoints[self.active_endpoint_index]['url'],
            "endpoints": [
                {
                    "url": endpoint['url'],
                    "healthy": self.health_status[i],
                    "error_count": self.error_counts[i]
                }
                for i, endpoint in enumerate(self.endpoints)
            ],
            "healthy_endpoints": sum(self.health_status)
        }
    async def get_program_accounts(
        self,
        program_id: str,
        commitment: Optional[str] = "confirmed",
        encoding: str = "base64",
        filters: Optional[List[Dict]] = None,
        data_slice: Optional[Dict] = None
    ) -> List[Dict]:
        logger.info(f"Getting program accounts for {program_id}")
        logger.info(f"Commitment: {commitment}")
        logger.info(f"Encoding: {encoding}")
        logger.info(f"Filters: {filters}")
        logger.info(f"Data slice: {data_slice}")
        """Get all accounts owned by a program"""
        config = {
            "encoding": encoding,
            "commitment": commitment
        }
        
        if filters:
            config["filters"] = filters
        if data_slice:
            config["dataSlice"] = data_slice
        result = await self.execute("getProgramAccounts", program_id, config)
        logger.info(f"result: {result}")
        return result

    async def get_multiple_accounts(
        self,
        pubkeys: List[str],
        commitment: Optional[str] = "confirmed",
        encoding: str = "base64"
    ) -> List[Dict]:
        logger.info(f"Getting multiple accounts for {pubkeys}")
        """Get information about multiple accounts"""
        config = {
            "encoding": encoding,
            "commitment": commitment
        }
        return await self.execute("getMultipleAccounts", pubkeys, config)

    async def get_account_info(
        self,
        pubkey: str,
        commitment: Optional[str] = "confirmed",
        encoding: str = "base64"
    ) -> Dict:
        logger.info(f"get account info for pubkey {pubkey}")
        """Get information about an account"""
        config = {
            "encoding": encoding,
            "commitment": commitment
        }
        return await self.execute("getAccountInfo", pubkey, config)

    async def get_token_accounts_by_owner(
        self,
        owner: str,
        program_id: str,
        commitment: Optional[str] = "confirmed",
        encoding: str = "base64"
    ) -> List[Dict]:
        logger.info(f"Getting token accounts by owner for {owner}")
        """Get token accounts by owner address"""
        config = {
            "encoding": encoding,
            "commitment": commitment
        }
        return await self.execute(
            "getTokenAccountsByOwner",
            owner,
            {"programId": program_id},
            config
        )

    async def get_signatures_for_address(
        self,
        address: str,
        before: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
        commitment: Optional[str] = "confirmed"
    ) -> List[Dict]:
        logger.info("Getting signatures for address %s", address)
        """Get signatures for transactions involving an address"""
        config = {"commitment": commitment}
        if before:
            config["before"] = before
        if until:
            config["until"] = until
        if limit:
            config["limit"] = limit

        return await self.execute("getSignaturesForAddress", address, config)

    async def get_token_supply(
        self,
        mint: str,
        commitment: Optional[str] = "confirmed"
    ) -> Dict:
        logger.info("get token supply for mint %s", mint)
        """Get total supply of a token"""
        config = {"commitment": commitment}
        return await self.execute("getTokenSupply", mint, config)

    async def get_transaction(
        self,
        signature: str,
        encoding: str = "json",
        commitment: Optional[str] = "confirmed",
        max_supported_transaction_version: Optional[int] = None
    ) -> Dict:
        logger.info("get transaction for signature %s", signature)
        """Get transaction details"""
        config = {
            "encoding": encoding,
            "commitment": commitment
        }
        if max_supported_transaction_version is not None:
            config["maxSupportedTransactionVersion"] = max_supported_transaction_version

        return await self.execute("getTransaction", signature, config)

    async def get_latest_blockhash(
        self,
        commitment: Optional[str] = "confirmed"
    ) -> Dict:
        logger.info("get latest blockhash")
        """Get latest blockhash"""
        config = {"commitment": commitment}
        return await self.execute("getLatestBlockhash", [config])

    async def get_health(self) -> str:
        logger.info("get health")
        """Get node health status"""
        return await self.execute("getHealth")

    async def get_cluster_nodes(self) -> List[Dict]:
        logger.info("get cluster nodes")
        """Get information about nodes participating in the cluster"""
        return await self.execute("getClusterNodes")

    async def get_token_largest_accounts(
        self,
        mint: str,
        commitment: Optional[str] = "confirmed"
    ) -> List[Dict]:
        logger.info("get token largest accounts for mint %s", mint)
        """Get largest token accounts for a mint"""
        config = {"commitment": commitment}
        return await self.execute("getTokenLargestAccounts", mint, config)

    async def get_balance(
        self,
        pubkey: str,
        commitment: Optional[str] = "confirmed"
    ) -> int:
        logger.info("get balance for pubkey %s", pubkey)
        """Get balance of an account"""
        config = {"commitment": commitment}
        return await self.execute("getBalance", pubkey, config)

    async def is_blockhash_valid(
        self,
        blockhash: str,
        commitment: Optional[str] = "confirmed"
    ) -> bool:
        logger.info("is blockhash valid %s", blockhash)
        """Check if a blockhash is still valid"""
        config = {"commitment": commitment}
        return await self.execute("isBlockhashValid", blockhash, config)

    async def batch_request(
        self,
        requests: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> List[Any]:
        logger.info("batch requests")
        """Execute multiple RPC requests in batches"""
        results = []
        for i in range(0, len(requests), batch_size):
            batch = requests[i:i + batch_size]
            batch_payloads = []
            for j, req in enumerate(batch):
                payload = {
                    "jsonrpc": "2.0",
                    "id": i + j,
                    "method": req["method"],
                    "params": req.get("params", [])
                }
                batch_payloads.append(payload)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.endpoints[self.active_endpoint_index]['url'],
                    headers={"Content-Type": "application/json"},
                    json=batch_payloads
                ) as response:
                    batch_results = await response.json()
                    # Sort results by ID to maintain order
                    batch_results.sort(key=lambda x: x.get("id", 0))
                    results.extend([r.get("result") for r in batch_results])

            await asyncio.sleep(0.1)  # Small delay between batches
        return results