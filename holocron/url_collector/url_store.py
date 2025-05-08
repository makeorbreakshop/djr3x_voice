"""
URL store for persisting discovered Wookieepedia URLs in Supabase.
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
import asyncio
import httpx

# Import patches for httpx
try:
    # Try to import from holocron package first
    from holocron import patches
    logging.info("Using global patches from holocron package in URLStore")
except ImportError:
    # Fallback to direct patching if import fails
    logging.warning("Could not import holocron.patches in URLStore - applying patch locally")
    original_httpx_init = httpx.Client.__init__
    def patched_httpx_init(self, *args, **kwargs):
        if 'proxy' in kwargs:
            del kwargs['proxy']
        return original_httpx_init(self, *args, **kwargs)
    httpx.Client.__init__ = patched_httpx_init

from supabase import create_client, Client
from dotenv import load_dotenv
import os

from config.app_settings import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

class URLStore:
    """Manages storage and retrieval of discovered URLs in Supabase."""
    
    def __init__(self):
        """Initialize the URL store."""
        # Load environment variables
        load_dotenv()
        
        # Initialize Supabase client using app settings
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("Missing Supabase credentials in environment")
            
        # Create client using app settings
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
    async def initialize_tables(self):
        """Create necessary tables if they don't exist."""
        # Tables are already created via SQL migration
        logger.info("Tables already initialized via SQL migration")
        return True
            
    async def store_urls(self, urls: List[str], category: Optional[str] = None, 
                        priority: str = 'low') -> Dict[str, int]:
        """Store discovered URLs in the database."""
        if not urls:
            return {"inserted": 0, "updated": 0}
            
        # Prepare data for insertion
        now = datetime.utcnow().isoformat()
        data = [
            {
                "url": url,
                "priority": priority,
                "categories": [category] if category else [],
                "discovered_at": now,
                "metadata": {"source": "url_collector"}
            }
            for url in urls
        ]
        
        try:
            # Use upsert to handle duplicates
            result = await asyncio.to_thread(
                lambda: self.supabase.table('holocron_urls')
                .upsert(data, on_conflict='url')
                .execute()
            )
            
            return {
                "inserted": len(result.data) if result.data else 0,
                "updated": 0  # Supabase client doesn't provide update count
            }
        except Exception as e:
            logger.error(f"Error storing URLs: {e}")
            return {"inserted": 0, "updated": 0}
            
    async def store_batch(self, batch: List[Dict[str, any]]) -> Dict[str, int]:
        """Store a batch of URLs with full metadata."""
        if not batch:
            logger.warning("Empty batch provided to store_batch()")
            return {"inserted": 0, "updated": 0}
            
        try:
            # Log the beginning of the batch storage process with more details
            batch_size = len(batch)
            logger.info(f"Starting batch storage of {batch_size} URLs")
            
            # Log a sample of URLs being stored for debugging
            if batch_size > 0:
                sample_size = min(2, batch_size)
                sample = batch[:sample_size]
                logger.debug(f"Sample URLs for batch: {[url.get('url') for url in sample]}")
            
            # Check batch data format to ensure it's valid
            for i, url_data in enumerate(batch[:5]):  # Check first 5 items
                if not isinstance(url_data, dict):
                    logger.error(f"Invalid URL data format at index {i}: {type(url_data)}")
                elif 'url' not in url_data:
                    logger.error(f"Missing 'url' field in data at index {i}: {url_data.keys()}")
            
            # Log the exact fields being sent
            if batch_size > 0:
                logger.debug(f"Fields being sent: {list(batch[0].keys())}")
            
            # Detailed logging around the actual database call
            logger.debug("Calling Supabase upsert operation")
            start_time = datetime.utcnow()
            
            # Use upsert to handle duplicates
            result = await asyncio.to_thread(
                lambda: self.supabase.table('holocron_urls')
                .upsert(batch, on_conflict='url')
                .execute()
            )
            
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            logger.debug(f"Supabase call completed in {duration:.2f} seconds")
            
            # More detailed result analysis
            if hasattr(result, 'data') and result.data:
                inserted_count = len(result.data)
                logger.info(f"Successfully stored batch of {inserted_count} URLs (of {batch_size} sent)")
                
                # Log the first few inserted IDs
                if inserted_count > 0:
                    sample_ids = [item.get('id') for item in result.data[:3]]
                    logger.debug(f"Sample IDs inserted: {sample_ids}")
            else:
                inserted_count = 0
                logger.warning(f"No data returned from Supabase (sent {batch_size} URLs)")
                
                # Try to parse more details from the result
                if hasattr(result, 'status_code'):
                    logger.debug(f"Response status code: {result.status_code}")
            
            # Verify the result
            if not result.data and len(batch) > 0:
                logger.warning(f"Potential issue: {len(batch)} URLs submitted, but no data returned in result")
                if hasattr(result, 'error'):
                    logger.error(f"Error information: {result.error}")
            
            return {
                "inserted": inserted_count,
                "updated": 0  # Supabase client doesn't provide update count
            }
        except Exception as e:
            error_msg = f"Error storing URL batch: {str(e)}"
            logger.error(error_msg)
            
            # Log a few URLs from the batch for debugging
            if batch and len(batch) > 0:
                sample = batch[0] if len(batch) == 1 else [batch[0], batch[-1]]
                logger.error(f"Sample URLs from failed batch: {sample}")
            
            # Re-raise the exception to allow caller to handle it
            raise Exception(f"Database error during batch storage: {str(e)}") from e
            
    async def update_url_metadata(self, url: str, metadata: Dict) -> bool:
        """Update metadata for a specific URL."""
        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.table('holocron_urls')
                .update({"metadata": metadata})
                .eq("url", url)
                .execute()
            )
            
            return bool(result.data)
        except Exception as e:
            logger.error(f"Error updating URL metadata: {e}")
            return False
            
    async def mark_processed(self, url: str) -> bool:
        """Mark a URL as processed."""
        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.table('holocron_urls')
                .update({
                    "is_processed": True,
                    "last_checked": datetime.utcnow().isoformat()
                })
                .eq("url", url)
                .execute()
            )
            
            return bool(result.data)
        except Exception as e:
            logger.error(f"Error marking URL as processed: {e}")
            return False
            
    async def mark_as_processed(self, ids: List[int]) -> bool:
        """Mark multiple URLs as processed using their database IDs."""
        if not ids:
            return True
            
        try:
            # Update in batches of 100 to avoid any limitations
            batch_size = 100
            for i in range(0, len(ids), batch_size):
                batch_ids = ids[i:i+batch_size]
                
                # Use the "in" filter to match multiple IDs
                result = await asyncio.to_thread(
                    lambda: self.supabase.table('holocron_urls')
                    .update({
                        "is_processed": True,
                        "last_checked": datetime.utcnow().isoformat()
                    })
                    .in_("id", batch_ids)
                    .execute()
                )
                
                logger.info(f"Marked {len(batch_ids)} URLs as processed (batch {i//batch_size + 1})")
                
            return True
        except Exception as e:
            logger.error(f"Error marking URLs as processed: {e}")
            return False
            
    async def get_unprocessed_urls(self, limit: int = 100, 
                                 priority: Optional[str] = None) -> List[Dict]:
        """Get unprocessed URLs for processing."""
        query = self.supabase.table('holocron_urls').select('*').eq('is_processed', False)
        
        if priority:
            query = query.eq('priority', priority)
            
        try:
            result = await asyncio.to_thread(
                lambda: query.limit(limit).execute()
            )
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Error fetching unprocessed URLs: {e}")
            return []
            
    async def get_stats(self) -> Dict[str, int]:
        """Get statistics about stored URLs."""
        try:
            stats = await asyncio.to_thread(
                lambda: self.supabase.rpc('get_url_stats').execute()
            )
            return stats.data if stats.data else {}
        except Exception as e:
            logger.error(f"Error fetching URL stats: {e}")
            return {}
            
    async def get_processed_count(self) -> int:
        """Get count of processed URLs."""
        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.table('holocron_urls')
                .select('*', count='exact')
                .eq('is_processed', True)
                .execute()
            )
            return result.count if result.count is not None else 0
        except Exception as e:
            logger.error(f"Error fetching processed URL count: {e}")
            return 0
            
    async def get_total_count(self) -> int:
        """Get total count of URLs."""
        try:
            result = await asyncio.to_thread(
                lambda: self.supabase.table('holocron_urls')
                .select('*', count='exact')
                .execute()
            )
            return result.count if result.count is not None else 0
        except Exception as e:
            logger.error(f"Error fetching total URL count: {e}")
            return 0 