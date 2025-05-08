#!/usr/bin/env python3
"""
Check sample URLs from the database to examine their structure.
"""

import os
import sys
import asyncio
import logging

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the URL store
from holocron.url_collector.url_store import URLStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

async def check_url_samples():
    """Query sample URLs from the database."""
    url_store = URLStore()
    
    try:
        # Get a few unprocessed URLs
        unprocessed = await url_store.get_unprocessed_urls(limit=3)
        print(f"\nSample unprocessed URLs ({len(unprocessed)}):")
        for url in unprocessed[:2]:  # Show only the first two
            print(f"- ID: {url.get('id')}")
            print(f"  URL: {url.get('url')}")
            print(f"  Priority: {url.get('priority')}")
            print(f"  Fields: {', '.join(url.keys())}")
            print(f"  Content Type exists: {'content_type' in url}")
            print(f"  Processed: {url.get('is_processed')}")
        
        # Get URL stats
        stats = await url_store.get_stats()
        print(f"\nURL Statistics:")
        print(f"- Total URLs: {stats.get('total', 0)}")
        print(f"- Processed: {stats.get('processed', 0)}")
        print(f"- Unprocessed: {stats.get('unprocessed', 0)}")
        
        return True
    except Exception as e:
        logger.error(f"Error querying URLs: {e}")
        return False

async def main():
    """Run the check."""
    print("Checking sample URLs from database...")
    await check_url_samples()

if __name__ == "__main__":
    asyncio.run(main()) 