#!/usr/bin/env python3
import sys
import os
import asyncio
from src.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)