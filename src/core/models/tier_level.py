from enum import Enum

class TierLevel(Enum):
    SIGNAL = "signal"  # Potential pump detection
    HOT = "hot"       # New tokens (0-7 days)
    WARM = "warm"     # Established tokens (7-30 days)
    COLD = "cold"     # Mature tokens (30+ days)
    ARCHIVE = "archive"  # Inactive/rugged tokens