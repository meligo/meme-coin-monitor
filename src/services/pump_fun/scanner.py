import asyncio
import json
import logging
import websockets
import base58
import base64
import struct
from datetime import datetime
from typing import Dict, Optional, List, Any
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from construct import Struct, Int64ul, Flag

# Local imports
from config.settings import settings
from config.database import get_db
from core.models.meme_coin import MemeCoin
from services.analysis.website_analyzer import website_analyzer

logger = logging.getLogger(__name__)

class PumpFunScanner:
    def __init__(self):
        self.rpc_client = AsyncClient(settings.RPC_ENDPOINT)
        
        # Bonding curve state structure
        self.BONDING_CURVE_STATE = Struct(
            "virtual_token_reserves" / Int64ul,
            "virtual_sol_reserves" / Int64ul,
            "real_token_reserves" / Int64ul,
            "real_sol_reserves" / Int64ul,
            "token_total_supply" / Int64ul,
            "complete" / Flag
        )
        
        # Create instruction discriminator
        self.CREATE_DISCRIMINATOR = struct.pack("<Q", 8576854823835016728)
        
        # Discriminator for bonding curve accounts
        self.CURVE_DISCRIMINATOR = struct.pack("<Q", 6966180631402821399)
        logger.info("PumpFunScanner initialized")

    async def parse_token_creation(self, log_data: Dict) -> Optional[Dict]:
        """Parse token creation data from program logs"""
        try:
            logs = log_data.get('logs', [])
            for log in logs:
                if "Program data:" in log:
                    # Extract and decode program data
                    program_data = log.split("Program data: ")[1]
                    decoded_data = base64.b64decode(program_data)
                    
                    # Check if this is a create instruction
                    if len(decoded_data) > 8 and decoded_data[:8] == self.CREATE_DISCRIMINATOR:
                        return await self._parse_create_instruction(decoded_data[8:], log_data)
            
            return None
        except Exception as e:
            logger.error(f"Error parsing token creation: {str(e)}")
            return None

    async def _parse_create_instruction(self, data: bytes, log_data: Dict) -> Optional[Dict]:
        """Parse create instruction data"""
        try:
            # Parse string lengths and values
            offset = 0
            
            # Parse name length and value
            name_len = int.from_bytes(data[offset:offset+4], 'little')
            offset += 4
            name = data[offset:offset+name_len].decode('utf-8')
            offset += name_len
            
            # Parse symbol length and value
            symbol_len = int.from_bytes(data[offset:offset+4], 'little')
            offset += 4
            symbol = data[offset:offset+symbol_len].decode('utf-8')
            offset += symbol_len
            
            # Parse URI length and value
            uri_len = int.from_bytes(data[offset:offset+4], 'little')
            offset += 4
            uri = data[offset:offset+uri_len].decode('utf-8')
            offset += uri_len
            
            # Parse mint address (32 bytes)
            mint = base58.b58encode(data[offset:offset+32]).decode('utf-8')
            offset += 32
            
            # Parse bonding curve address (32 bytes)
            bonding_curve = base58.b58encode(data[offset:offset+32]).decode('utf-8')
            offset += 32
            
            # Parse creator address (32 bytes)
            creator = base58.b58encode(data[offset:offset+32]).decode('utf-8')
            
            # Get transaction signature
            signature = log_data.get('signature')
            
            # Analyze the contract
            contract_analysis = await self._analyze_contract(Pubkey.from_string(mint))
            
            return {
                'address': mint,
                'name': name,
                'symbol': symbol,
                'uri': uri,
                'bonding_curve': bonding_curve,
                'creator': creator,
                'signature': signature,
                'contract_analysis': contract_analysis,
                'creation_date': datetime.utcnow()
            }
            
        except Exception as e:
            logger.error(f"Error parsing create instruction: {str(e)}", exc_info=True)
            return None
        
    async def start_monitoring(self):
        """Start monitoring both new tokens and existing ones"""
        logger.info("Starting token monitoring...")
        try:
            await asyncio.gather(
                self.listen_for_new_tokens(),
                self.scan_existing_tokens()
            )
        except Exception as e:
            logger.error(f"Error in monitoring: {str(e)}", exc_info=True)
            raise
        
    async def listen_for_new_tokens(self):
        """Listen for new token creations on pump.fun"""
        logger.info("Starting new token listener...")
        while True:
            try:
                async with websockets.connect(settings.WSS_ENDPOINT) as websocket:
                    subscription = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [str(settings.PUMP_PROGRAM)]},
                            {"commitment": "processed"}
                        ]
                    }
                    await websocket.send(json.dumps(subscription))
                    logger.info("WebSocket subscription established")
                    
                    while True:
                        try:
                            response = await websocket.recv()
                            data = json.loads(response)
                            
                            if 'method' in data and data['method'] == 'logsNotification':
                                log_data = data['params']['result']['value']
                                
                                if any("Program log: Instruction: Create" in log for log in log_data.get('logs', [])):
                                    logger.info("New token creation detected")
                                    token_data = await self.parse_token_creation(log_data)
                                    if token_data:
                                        await self.process_new_token(token_data)
                                        
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")
                            continue
                            
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}")
                await asyncio.sleep(5)
                
    async def scan_existing_tokens(self):
        """Scan and analyze existing tokens on pump.fun"""
        logger.info("Starting scan of existing tokens...")
        try:
            # Get all token accounts for the program
            response = await self.rpc_client.get_program_accounts(
                settings.PUMP_PROGRAM,
                encoding="base64"
            )
            
            logger.info(f"Found {len(response.value)} program accounts")
            
            for account in response.value:
                try:
                    # Check if this is a bonding curve account
                    if len(account.account.data) > 8 and account.account.data[:8] == self.CURVE_DISCRIMINATOR:
                        token_data = await self.analyze_token_account(account.pubkey)
                        if token_data:
                            await self.process_new_token(token_data)
                except Exception as e:
                    logger.error(f"Error processing account {account.pubkey}: {e}")
                    
        except Exception as e:
            logger.error(f"Error scanning existing tokens: {e}")

    async def analyze_token_account(self, token_address: Pubkey) -> Optional[Dict]:
        """Analyze a token account and gather all relevant information"""
        try:
            logger.info(f"Analyzing token: {token_address}")
            # Get token metadata
            metadata = await self._get_token_metadata(token_address)
            if not metadata:
                return None
                
            # Get bonding curve data
            curve_data = await self._get_bonding_curve_data(token_address)
            if not curve_data:
                return None
                
            # Check if mint authority is renounced
            mint_authority = await self._check_mint_authority(token_address)
            
            return {
                'address': str(token_address),
                'name': metadata.get('name'),
                'symbol': metadata.get('symbol'),
                'mint_authority_renounced': mint_authority.get('renounced', False),
                'mint_authority': mint_authority.get('authority'),
                'total_supply': curve_data.get('token_total_supply'),
                'liquidity': curve_data.get('real_sol_reserves'),
                'curve_complete': curve_data.get('complete', False),
                'contract_analysis': await self._analyze_contract(token_address),
                'creation_date': datetime.utcnow()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing token {token_address}: {e}")
            return None

    async def process_new_token(self, token_data: Dict):
        """Process and store new token data"""
        logger.info(f"Processing new token: {token_data.get('name')} ({token_data.get('symbol')})")
        db = next(get_db())
        try:
            # Check if token already exists
            existing_token = db.query(MemeCoin).filter(MemeCoin.address == token_data['address']).first()
            if existing_token:
                logger.info(f"Token {token_data['address']} already exists in database")
                return

            # Create new MemeCoin instance
            meme_coin = MemeCoin(
                address=token_data['address'],
                name=token_data['name'],
                symbol=token_data['symbol'],
                total_supply=token_data.get('total_supply', 0),
                contract_analysis=token_data.get('contract_analysis', {}),
                launch_date=token_data['creation_date']
            )
            
            # Add to database
            db.add(meme_coin)
            db.commit()
            
            logger.info(f"Successfully added token: {token_data['name']} ({token_data['symbol']})")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error storing token data: {e}")
        finally:
            db.close()

    async def _check_mint_authority(self, token_address: Pubkey) -> Dict:
        """Check if token's mint authority is renounced"""
        try:
            # Get mint info from token program
            mint_info = await self.rpc_client.get_account_info(token_address)
            
            if not mint_info.value or not mint_info.value.data:
                return {'renounced': False, 'authority': None}

            # Parse mint authority from token account data
            # Mint authority is at offset 0 in token account data
            mint_authority_data = mint_info.value.data[:32]
            mint_authority = str(Pubkey(mint_authority_data))
            
            # Check if authority is the zero address
            is_renounced = mint_authority == str(Pubkey.default())
            
            return {
                'renounced': is_renounced,
                'authority': None if is_renounced else mint_authority
            }
            
        except Exception as e:
            logger.error(f"Error checking mint authority: {e}")
            return {'renounced': False, 'authority': None}

    async def _analyze_contract(self, token_address: Pubkey) -> Dict:
        """Analyze token contract for potential risks/features"""
        try:
            # Get program data
            program_info = await self.rpc_client.get_account_info(token_address)
            
            if not program_info.value or not program_info.value.data:
                return {}
            
            analysis = {
                'has_mint_function': False,
                'has_freeze_authority': False,
                'has_blacklist': False,
                'is_upgradeable': False,
                'special_features': [],
                'risk_factors': []
            }
            
            # Parse token account data
            data = program_info.value.data
            
            # Check for freeze authority
            if len(data) >= 44:  # Freeze authority is at offset 36
                freeze_authority_data = data[36:44]
                has_freeze = not all(b == 0 for b in freeze_authority_data)
                analysis['has_freeze_authority'] = has_freeze
                if has_freeze:
                    analysis['risk_factors'].append('Has freeze authority')
            
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing contract: {e}")
            return {}

    def _get_bonding_curve_address(self, mint: Pubkey) -> tuple[Pubkey, int]:
        """Get the bonding curve address for a token"""
        return Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint)],
            settings.PUMP_PROGRAM
        )

scanner = PumpFunScanner()