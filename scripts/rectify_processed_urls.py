#!/usr/bin/env python3
"""
URL Rectification Script for Holocron Knowledge System

This script identifies URLs that were marked as processed but have no corresponding
chunks in the knowledge base, and resets their processing status to allow reprocessing 
with the fixed chunking algorithm.

Usage:
    python scripts/rectify_processed_urls.py [--dry-run] [--limit N] [--reset-all]
"""

import os
import sys
import argparse
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Set

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
from supabase import create_client

from config.app_settings import (
    SUPABASE_URL,
    SUPABASE_KEY,
    HOLOCRON_TABLE_NAME
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/url_rectification_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

async def identify_urls_without_chunks(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Identify URLs that were marked as processed but have no corresponding chunks.
    
    Args:
        limit: Optional limit on the number of URLs to return
        
    Returns:
        List of URL data dictionaries
    """
    try:
        # Create Supabase client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Get all processed URLs
        processed_urls_response = await asyncio.to_thread(
            lambda: supabase.table("holocron_urls")
            .select("id", "url", "title", "priority")
            .eq("is_processed", True)
            .execute()
        )
        
        if not hasattr(processed_urls_response, 'data'):
            logger.error("Failed to retrieve processed URLs from database")
            return []
            
        processed_urls = processed_urls_response.data
        logger.info(f"Found {len(processed_urls)} processed URLs in the database")
        
        # Get all URLs with chunks in the knowledge base
        chunks_response = await asyncio.to_thread(
            lambda: supabase.table(HOLOCRON_TABLE_NAME)
            .select("metadata->source")
            .execute()
        )
        
        if not hasattr(chunks_response, 'data'):
            logger.error("Failed to retrieve chunks from database")
            return []
            
        # Extract unique URLs from chunks
        urls_with_chunks = set()
        for chunk in chunks_response.data:
            try:
                source_url = chunk['metadata']['source']
                if source_url:
                    urls_with_chunks.add(source_url)
            except (KeyError, TypeError):
                continue
                
        logger.info(f"Found {len(urls_with_chunks)} URLs with chunks in the knowledge base")
        
        # Find URLs that are marked as processed but have no chunks
        urls_without_chunks = []
        for url_data in processed_urls:
            if url_data['url'] not in urls_with_chunks:
                urls_without_chunks.append(url_data)
                
        logger.info(f"Found {len(urls_without_chunks)} URLs marked as processed but with no chunks")
        
        # Apply limit if specified
        if limit and limit < len(urls_without_chunks):
            urls_without_chunks = urls_without_chunks[:limit]
            logger.info(f"Limited to {limit} URLs")
            
        return urls_without_chunks
    
    except Exception as e:
        logger.error(f"Error identifying URLs without chunks: {e}")
        return []

async def reset_url_processing_status(url_ids: List[str], dry_run: bool = False) -> int:
    """
    Reset the processing status of URLs.
    
    Args:
        url_ids: List of URL IDs to reset
        dry_run: If True, don't actually update the database
        
    Returns:
        Number of URLs reset
    """
    if not url_ids:
        logger.warning("No URL IDs provided to reset")
        return 0
    
    if dry_run:
        logger.info(f"DRY RUN: Would reset processing status for {len(url_ids)} URLs")
        return len(url_ids)
    
    try:
        # Create Supabase client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Update URLs in batches to avoid rate limits
        batch_size = 50
        reset_count = 0
        
        for i in range(0, len(url_ids), batch_size):
            batch = url_ids[i:i+batch_size]
            
            # Update processing status
            result = await asyncio.to_thread(
                lambda: supabase.table("holocron_urls").update(
                    {"is_processed": False}
                ).in_("id", batch).execute()
            )
            
            # Check result
            if hasattr(result, 'data') and result.data:
                reset_count += len(result.data)
                logger.info(f"Reset processing status for batch of {len(result.data)} URLs")
            else:
                logger.warning(f"Failed to reset processing status for batch {i//batch_size + 1}")
            
            # Avoid rate limiting
            if i + batch_size < len(url_ids):
                await asyncio.sleep(1)
        
        logger.info(f"Successfully reset processing status for {reset_count}/{len(url_ids)} URLs")
        return reset_count
    
    except Exception as e:
        logger.error(f"Error resetting URL processing status: {e}")
        return 0

async def get_chunk_counts_by_url() -> Dict[str, int]:
    """
    Get the number of chunks for each URL in the knowledge base.
    
    Returns:
        Dictionary mapping URLs to chunk counts
    """
    try:
        # Create Supabase client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Get all chunks
        response = await asyncio.to_thread(
            lambda: supabase.table(HOLOCRON_TABLE_NAME)
            .select("metadata->source")
            .execute()
        )
        
        if not hasattr(response, 'data'):
            logger.error("Failed to retrieve chunks from database")
            return {}
            
        # Count chunks per URL
        url_chunk_counts = {}
        for chunk in response.data:
            try:
                source_url = chunk['metadata']['source']
                if source_url:
                    url_chunk_counts[source_url] = url_chunk_counts.get(source_url, 0) + 1
            except (KeyError, TypeError):
                continue
                
        logger.info(f"Found {len(url_chunk_counts)} URLs with chunks")
        
        return url_chunk_counts
    
    except Exception as e:
        logger.error(f"Error getting chunk counts by URL: {e}")
        return {}

async def generate_report(urls_without_chunks: List[Dict[str, Any]], url_chunk_counts: Dict[str, int]):
    """
    Generate a report on URLs and their chunk counts.
    
    Args:
        urls_without_chunks: List of URLs without chunks
        url_chunk_counts: Dictionary mapping URLs to chunk counts
    """
    total_urls = len(urls_without_chunks) + len(url_chunk_counts)
    
    # Calculate statistics
    urls_with_chunks = len(url_chunk_counts)
    urls_without_chunks_count = len(urls_without_chunks)
    
    # URLs by priority
    high_priority = sum(1 for u in urls_without_chunks if u.get('priority') == 'high')
    medium_priority = sum(1 for u in urls_without_chunks if u.get('priority') == 'medium')
    low_priority = sum(1 for u in urls_without_chunks if u.get('priority') == 'low')
    
    # Generate report
    report = [
        "=== Holocron Knowledge Base URL Status Report ===",
        f"Total URLs: {total_urls}",
        f"URLs with chunks: {urls_with_chunks} ({urls_with_chunks/total_urls*100:.2f}%)",
        f"URLs without chunks: {urls_without_chunks_count} ({urls_without_chunks_count/total_urls*100:.2f}%)",
        "",
        "Priority breakdown of URLs without chunks:",
        f"  High priority: {high_priority}",
        f"  Medium priority: {medium_priority}",
        f"  Low priority: {low_priority}",
        "",
        "Top 10 URLs without chunks (sample):"
    ]
    
    # Add sample of URLs without chunks
    for i, url_data in enumerate(urls_without_chunks[:10]):
        report.append(f"  {i+1}. {url_data.get('title', 'Unknown')} - {url_data.get('url')}")
    
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    # Generate report file
    report_path = f"logs/url_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_path, 'w') as f:
        f.write('\n'.join(report))
    
    logger.info(f"Generated report: {report_path}")
    
    # Log summary
    for line in report[:7]:
        logger.info(line)

async def main():
    """Parse arguments and run the script."""
    parser = argparse.ArgumentParser(description="Rectify URLs marked as processed but with no chunks")
    parser.add_argument('--dry-run', action='store_true',
                      help="Don't actually update the database")
    parser.add_argument('--limit', type=int, default=None,
                      help="Maximum number of URLs to process")
    parser.add_argument('--reset-all', action='store_true',
                      help="Reset all URLs without chunk verification")
    
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    if args.reset_all:
        logger.warning("--reset-all flag is set - this will reset processing status for ALL URLs")
        # This would require a different implementation to reset all URLs
        # Not implemented in this script
        return
    
    # Create output directory
    os.makedirs("logs", exist_ok=True)
    
    # Identify URLs without chunks
    urls_without_chunks = await identify_urls_without_chunks(args.limit)
    
    if not urls_without_chunks:
        logger.info("No URLs found that need rectification")
        return
    
    # Get URL IDs
    url_ids = [url_data["id"] for url_data in urls_without_chunks]
    
    # Reset processing status
    reset_count = await reset_url_processing_status(url_ids, args.dry_run)
    
    # Get chunk counts by URL for reporting
    url_chunk_counts = await get_chunk_counts_by_url()
    
    # Generate report
    await generate_report(urls_without_chunks, url_chunk_counts)
    
    # Summary
    if args.dry_run:
        logger.info(f"DRY RUN: Would have reset {reset_count} URLs")
    else:
        logger.info(f"Reset {reset_count} URLs for reprocessing")
    
    logger.info("URL rectification complete")

if __name__ == "__main__":
    asyncio.run(main()) 