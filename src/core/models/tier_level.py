from enum import Enum

class TierLevel(str, Enum):
    SIGNAL = "SIGNAL"     # High potential/risk tokens
    HOT = "HOT"          # New active tokens
    WARM = "WARM"        # Established tokens
    COLD = "COLD"        # Mature tokens
    ARCHIVE = "ARCHIVE"  # Inactive tokens
    RUGGED = "RUGGED"    # Rugged/compromised tokens
    
    def __str__(self):
        return self.value