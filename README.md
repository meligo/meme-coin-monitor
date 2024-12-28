# Meme Coin Monitor

A Python application for monitoring and analyzing meme coins across different tiers with risk assessment capabilities.

## Features

- Multi-tier monitoring system (Signal, Hot, Warm, Cold, Archive)
- Real-time blockchain scanning
- Risk scoring based on rugpull patterns
- Redis caching for performance optimization
- PostgreSQL storage for historical data

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables:
- PostgreSQL connection settings
- Redis connection settings
- Blockchain node endpoints

3. Initialize database:
```bash
# Database initialization commands will be added
```

## Architecture

### Monitoring Tiers

1. Signal Tier ("Potential Pump")
   - Check Frequency: 15-30 seconds
   - Key Indicators: Whale accumulation, Smart money inflows
   - Redis Priority: Highest
   - Worker Ratio: 30% of resources

2. Hot Tier (New Tokens, 0-7 days)
   - Check Frequency: 1-5 minutes
   - Key Monitoring: Initial liquidity, Early trading patterns
   - Redis Priority: High
   - Worker Ratio: 40% of resources

3. Warm Tier (Established, 7-30 days)
   - Check Frequency: 30 minutes
   - Key Monitoring: Stable liquidity levels, Normal trading
   - Redis Priority: Medium
   - Worker Ratio: 20% of resources

4. Cold Tier (Mature, 30+ days)
   - Check Frequency: 6 hours
   - Key Monitoring: Basic activity check
   - Redis Priority: Low
   - Worker Ratio: 10% of resources

5. Archive Tier (Inactive/Rugged)
   - Storage: PostgreSQL only
   - Used for historical analysis and pattern recognition