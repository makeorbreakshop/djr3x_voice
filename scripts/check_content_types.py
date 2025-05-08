#!/usr/bin/env python3
"""
Check valid content_type values in the holocron_urls table.
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

async def check_content_types():
    """Query the database to check valid content_type values."""
    url_store = URLStore()
    
    try:
        # Get all unique content_type values
        result = await asyncio.to_thread(
            lambda: url_store.supabase.rpc('get_unique_content_types').execute()
        )
        
        if result.data:
            print("Valid content_type values:")
            for item in result.data:
                print(f"- {item}")
            return True
        else:
            # Try a different approach if the RPC doesn't exist
            result = await asyncio.to_thread(
                lambda: url_store.supabase.table('holocron_urls')
                .select('content_type')
                .limit(10)
                .execute()
            )
            
            if result.data:
                print("Sample content_type values:")
                content_types = set()
                for item in result.data:
                    content_types.add(item.get('content_type'))
                
                for ct in content_types:
                    print(f"- {ct}")
                return True
            else:
                print("No content_type values found in the database.")
                return False
            
    except Exception as e:
        logger.error(f"Error querying content_types: {e}")
        print("\nLet's check the structure of the table instead:")
        
        try:
            # Get the table structure using system tables
            result = await asyncio.to_thread(
                lambda: url_store.supabase.table('information_schema.columns')
                .select('column_name,data_type,udt_name')
                .eq('table_name', 'holocron_urls')
                .eq('column_name', 'content_type')
                .execute()
            )
            
            if result.data:
                print(f"Column structure: {result.data}")
            else:
                print("Could not retrieve column structure")
        except Exception as e2:
            logger.error(f"Error retrieving table structure: {e2}")
        
        return False

async def main():
    """Run the check."""
    print("Checking valid content_type values...")
    await check_content_types()

if __name__ == "__main__":
    asyncio.run(main()) 