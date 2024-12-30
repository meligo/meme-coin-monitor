# Meme Coin Monitor - Technical Overview

## Project Analysis

This document provides a comprehensive technical analysis of the Meme Coin Monitor project, based on codebase examination.

### Project Overview

Meme Coin Monitor is a sophisticated Python application designed for cryptocurrency token monitoring, with a particular focus on meme coins. The system implements a multi-tiered monitoring approach combined with risk assessment capabilities, utilizing both real-time and historical data analysis.

### Technical Architecture

#### Core Components

1. **Database Layer**
   - Primary storage: PostgreSQL
   - Caching layer: Redis
   - ORM: SQLAlchemy (version >=1.4.0)

2. **Runtime Environment**
   - Asynchronous architecture using Python's asyncio
   - Multiple concurrent scanners:
     - Live scanner for real-time monitoring
     - Backtest scanner for historical analysis

3. **Blockchain Integration**
   - Direct blockchain scanning capabilities
   - Support for Solana blockchain (via solana >=0.34.3 and solders >=0.21.0)
   - WebSocket connections for real-time updates

### Monitoring System Architecture

#### Tier System Implementation

1. **Signal Tier ("Potential Pump")**
   - Check Frequency: 15-30 seconds
   - Primary Focus: Whale accumulation and smart money inflows
   - Redis Priority: Highest
   - Resource Allocation: 30% of worker resources

2. **Hot Tier (New Tokens)**
   - Age Range: 0-7 days
   - Check Frequency: 1-5 minutes
   - Primary Focus: Initial liquidity and early trading patterns
   - Redis Priority: High
   - Resource Allocation: 40% of worker resources

3. **Warm Tier (Established Tokens)**
   - Age Range: 7-30 days
   - Check Frequency: 30 minutes
   - Primary Focus: Stable liquidity levels and normal trading patterns
   - Redis Priority: Medium
   - Resource Allocation: 20% of worker resources

4. **Cold Tier (Mature Tokens)**
   - Age Range: 30+ days
   - Check Frequency: 6 hours
   - Primary Focus: Basic activity monitoring
   - Redis Priority: Low
   - Resource Allocation: 10% of worker resources

5. **Archive Tier**
   - Storage: PostgreSQL only
   - Purpose: Historical analysis and pattern recognition
   - No active monitoring

### Key Dependencies

- **Database and Caching**
  - sqlalchemy >= 1.4.0
  - sqlalchemy-utils >= 0.41.1
  - redis >= 4.0.0
  - psycopg2-binary >= 2.9.0

- **Async and Web**
  - aiohttp >= 3.8.0
  - asyncio >= 3.4.3
  - websockets >= 10.4

- **Blockchain Integration**
  - solana >= 0.34.3
  - solders >= 0.21.0
  - base58 >= 2.1.1
  - borsh-construct >= 0.1.0

- **Utilities**
  - pydantic >= 1.8.0
  - python-dotenv >= 0.19.0
  - beautifulsoup4 >= 4.12.0
  - requests >= 2.28.2

### Implementation Details

#### Scanner Components

1. **Live Scanner (PumpFunScanner)**
   - Real-time token monitoring
   - Concurrent processing of:
     - Existing token scanning
     - New token detection
   - Integrated with token processor for data handling

2. **Backtest Scanner**
   - Historical token analysis
   - Configurable batch processing
   - Environment-based batch size configuration

#### Error Handling and Logging

- Comprehensive logging system implemented
- Structured error handling with detailed logging
- Separate logging levels for different components
- HTTP request logging minimized for clarity

### Setup Requirements

1. **Environment Configuration**
   - PostgreSQL connection settings
   - Redis connection settings
   - Blockchain node endpoints

2. **Database Initialization**
   - Automatic table creation via SQLAlchemy models
   - Base schema defined in project structure

3. **Dependency Installation**
   ```bash
   pip install -r requirements.txt
   ```

### Runtime Behavior

The application runs two main concurrent processes:
1. Live scanning for real-time token monitoring
2. Historical analysis for pattern recognition and risk assessment

Both processes are managed through asyncio tasks, with proper error handling and graceful shutdown capabilities.