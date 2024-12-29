from pydantic import BaseModel
from typing import Dict, Any
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseModel):
    # Program IDs and Important Addresses
    PUMP_PROGRAM: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    PUMP_GLOBAL: str = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
    PUMP_EVENT_AUTHORITY: str = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
    PUMP_FEE: str = "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"
    TOKEN_METADATA_PROGRAM_ID: str = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"

    # Solana System Programs
    SYSTEM_PROGRAM: str = "11111111111111111111111111111111"
    SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM: str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
    SYSTEM_TOKEN_PROGRAM: str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

    # RPC Configuration
    RPC_ENDPOINT: str = "https://solana-mainnet.core.chainstack.com/3b0ed795772b1898efd6ff013f7b764e"

    # Account discriminators
    TOKEN_DISCRIMINATOR: bytes = b'\x17\xd2\x10\xf5\xa3\x8a\xcd`'  # 6966180631402821399 in bytes
    CURVE_DISCRIMINATOR: bytes = b'\x0b\x84\x9d\xab\x1e\xe5\xab\x5e'  # Your curve discriminator

    # Constants
    LAMPORTS_PER_SOL: int = 1_000_000_000
    TOKEN_DECIMALS: int = 6

    def __init__(self, **data: Dict[str, Any]):
        # First load environment variables
        env_data = {}
        for field in self.model_fields:
            env_value = os.getenv(field)
            if env_value is not None:
                env_data[field] = env_value
        
        # Override with any passed data
        env_data.update(data)
        super().__init__(**env_data)

settings = Settings()

# Export all constants at module level for backward compatibility
PUMP_PROGRAM = settings.PUMP_PROGRAM
PUMP_GLOBAL = settings.PUMP_GLOBAL
PUMP_EVENT_AUTHORITY = settings.PUMP_EVENT_AUTHORITY
PUMP_FEE = settings.PUMP_FEE
TOKEN_METADATA_PROGRAM_ID = settings.TOKEN_METADATA_PROGRAM_ID
SYSTEM_PROGRAM = settings.SYSTEM_PROGRAM
SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM = settings.SYSTEM_ASSOCIATED_TOKEN_ACCOUNT_PROGRAM
SYSTEM_TOKEN_PROGRAM = settings.SYSTEM_TOKEN_PROGRAM
RPC_ENDPOINT = settings.RPC_ENDPOINT
TOKEN_DISCRIMINATOR = settings.TOKEN_DISCRIMINATOR
CURVE_DISCRIMINATOR = settings.CURVE_DISCRIMINATOR
LAMPORTS_PER_SOL = settings.LAMPORTS_PER_SOL
TOKEN_DECIMALS = settings.TOKEN_DECIMALS