"""
URL store for persisting discovered Wookieepedia URLs in Supabase.
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
import asyncio
from supabase import create_client, Client
from dotenv import load_dotenv
import os

logger = logging.getLogger(__name__)

class URLStore:
    """Manages storage and retrieval of discovered URLs in Supabase."""
    
    def __init__(self):
        load_dotenv()
        
        # Initialize Supabase client
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            raise ValueError("Missing Supabase credentials in environment")
            
        self.supabase: Client = create_client(supabase_url, supabase_key)
        
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
            result = self.supabase.table('holocron_urls').upsert(
                data,
                on_conflict='url'
            ).execute()
            
            return {
                "inserted": len(result.data) if result.data else 0,
                "updated": 0  # Supabase client doesn't provide update count
            }
        except Exception as e:
            logger.error(f"Error storing URLs: {e}")
            return {"inserted": 0, "updated": 0}
            
    async def update_url_metadata(self, url: str, metadata: Dict) -> bool:
        """Update metadata for a specific URL."""
        try:
            result = self.supabase.table('holocron_urls').update(
                {"metadata": metadata}
            ).eq("url", url).execute()
            
            return bool(result.data)
        except Exception as e:
            logger.error(f"Error updating URL metadata: {e}")
            return False
            
    async def mark_processed(self, url: str) -> bool:
        """Mark a URL as processed."""
        try:
            result = self.supabase.table('holocron_urls').update(
                {
                    "is_processed": True,
                    "last_checked": datetime.utcnow().isoformat()
                }
            ).eq("url", url).execute()
            
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
                result = self.supabase.table('holocron_urls').update(
                    {
                        "is_processed": True,
                        "last_checked": datetime.utcnow().isoformat()
                    }
                ).in_("id", batch_ids).execute()
                
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
            result = query.limit(limit).execute()
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Error fetching unprocessed URLs: {e}")
            return []
            
    async def get_stats(self) -> Dict[str, int]:
        """Get statistics about stored URLs."""
        try:
            stats = self.supabase.rpc('get_url_stats').execute()
            return stats.data if stats.data else {}
        except Exception as e:
            logger.error(f"Error fetching URL stats: {e}")
            return {}
            
    async def get_processed_count(self) -> int:
        """Get count of processed URLs."""
        try:
            result = self.supabase.table('holocron_urls').select('*', count='exact').eq('is_processed', True).execute()
            return result.count if result.count is not None else 0
        except Exception as e:
            logger.error(f"Error fetching processed URL count: {e}")
            return 0
            
    async def get_total_count(self) -> int:
        """Get total count of URLs."""
        try:
            result = self.supabase.table('holocron_urls').select('*', count='exact').execute()
            return result.count if result.count is not None else 0
        except Exception as e:
            logger.error(f"Error fetching total URL count: {e}")
            return 0 