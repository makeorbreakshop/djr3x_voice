#!/usr/bin/env python3
"""
Count the total URLs in the database.
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

async def count_urls():
    """Count URLs in the database."""
    url_store = URLStore()
    
    try:
        # Count total URLs
        result = await asyncio.to_thread(
            lambda: url_store.supabase.table('holocron_urls')
            .select('*', count='exact')
            .execute()
        )
        
        total_count = result.count if result.count is not None else 0
        print(f"Total URLs in database: {total_count}")
        
        # Count by priority
        print("\nURLs by priority:")
        for priority in ['high', 'medium-high', 'medium', 'medium-low', 'low']:
            try:
                priority_result = await asyncio.to_thread(
                    lambda: url_store.supabase.table('holocron_urls')
                    .select('*', count='exact')
                    .eq('priority', priority)
                    .execute()
                )
                
                priority_count = priority_result.count if priority_result.count is not None else 0
                print(f"- {priority}: {priority_count}")
            except Exception as e:
                logger.error(f"Error counting {priority} priority URLs: {e}")
        
        return True
    except Exception as e:
        logger.error(f"Error counting URLs: {e}")
        return False

async def main():
    """Run the count."""
    print("Counting URLs in database...")
    await count_urls()

if __name__ == "__main__":
    asyncio.run(main()) 