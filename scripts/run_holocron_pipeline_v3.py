#!/usr/bin/env python3
"""
Holocron RAG Pipeline Runner (Version 3)

This script runs the optimized pipeline for the Holocron knowledge system:
1. Pulls unprocessed URLs directly from the database (no scraping step)
2. Process content into appropriate chunks
3. Generate embeddings for the chunks
4. Upload to the Supabase vector database
5. Mark URLs as processed in the database

Version 3 improves on Version 2 by using the existing URLs in the database
rather than collecting URLs from scratch each time.

Usage:
    python scripts/run_holocron_pipeline_v3.py [--limit N] [--workers N] [--batch-size N] 
    [--requests-per-minute N] [--priority high|medium|low]
"""

import os
import sys
import json
import argparse
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Set

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.holocron.wookieepedia_scraper import WookieepediaScraper
from src.holocron.data_processor import HolocronDataProcessor
from src.holocron.batch_processor import BatchProcessor
from holocron.url_collector.url_store import URLStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/holocron_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

async def run_pipeline(args):
    """Run the optimized Holocron pipeline, getting URLs from the database."""
    # Create output directories if they don't exist
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data/checkpoints", exist_ok=True)
    
    # Initialize URL Store
    url_store = URLStore()
    
    # Step 1: Get unprocessed URLs directly from the database
    logger.info(f"Getting unprocessed URLs from database (limit: {args.limit}, priority: {args.priority})")
    unprocessed_urls_data = await url_store.get_unprocessed_urls(args.limit, args.priority)
    
    if not unprocessed_urls_data:
        logger.error("No unprocessed URLs found in the database")
        return False
    
    # Extract URLs and create ID mapping
    urls = [item['url'] for item in unprocessed_urls_data]
    # Create a mapping of URLs to their database IDs for later use
    url_to_id = {item['url']: item['id'] for item in unprocessed_urls_data}
    
    logger.info(f"Found {len(urls)} unprocessed URLs in the database")
    
    # Get current counts to show progress
    total_urls = await url_store.get_total_count()
    processed_urls = await url_store.get_processed_count()
    logger.info(f"Database status before processing: {processed_urls} of {total_urls} URLs processed ({(processed_urls/total_urls)*100:.2f}%)")
    
    # Step 2-4: Process URLs using BatchProcessor
    if urls:
        logger.info("Starting batch processing of URLs")
        processor = BatchProcessor(
            urls=urls,
            num_workers=args.workers,
            batch_size=args.batch_size,
            checkpoint_dir="data/checkpoints",
            requests_per_minute=args.requests_per_minute
        )
        
        # Initialize data processor for actual content processing
        data_processor = HolocronDataProcessor()
        
        # Override the process_url method to use our actual processing logic
        async def process_url(url: str) -> bool:
            try:
                # Scrape content
                async with WookieepediaScraper() as scraper:
                    content = await scraper.scrape_article(url)
                
                if content:
                    # Process and upload content
                    success = await data_processor.process_and_upload([content])
                    return success
                return False
            except Exception as e:
                logger.error(f"Failed to process URL {url}: {e}")
                return False
        
        # Set the processing function
        processor.process_url = process_url
        
        # Run the batch processor
        success = await processor.run()
        
        if success:
            logger.info("Holocron pipeline completed successfully")
            # Log final statistics - FIXED: Handle both int and collection types
            if hasattr(processor.progress, 'processed_urls'):
                if isinstance(processor.progress.processed_urls, (list, set, tuple)):
                    processed_count = len(processor.progress.processed_urls)
                else:
                    # Handle case when processed_urls is an integer counter
                    processed_count = processor.progress.processed_urls
                logger.info(f"Total URLs processed in this run: {processed_count}")
            else:
                logger.info(f"Total URLs processed in this run: Unknown")
                
            logger.info(f"Failed URLs in this run: {len(processor.progress.failed_urls)}")
            if processor.progress.failed_urls:
                logger.info("Failed URLs:")
                for url in processor.progress.failed_urls:
                    logger.info(f"  - {url}")
            
            # Mark ALL URLs as processed, regardless of success or failure
            # This includes URLs that failed with errors and URLs that didn't generate any chunks
            all_urls_to_mark = set(urls)  # Mark all URLs that were attempted
            
            if all_urls_to_mark:
                # Get IDs for all URLs to mark as processed
                ids_to_mark = [url_to_id[url] for url in all_urls_to_mark if url in url_to_id]
                if ids_to_mark:
                    logger.info(f"Marking {len(ids_to_mark)} URLs as processed in the database")
                    await url_store.mark_as_processed(ids_to_mark)
                                    
            # Verify database status
            try:
                # Log processing status from database
                processed_result = await url_store.get_processed_count()
                total_result = await url_store.get_total_count()
                logger.info(f"Database status after processing: {processed_result} of {total_result} URLs processed ({(processed_result/total_result)*100:.2f}%)")
            except Exception as e:
                logger.error(f"Error checking database status: {e}")
                
            return True
        else:
            logger.error("Holocron pipeline failed during processing phase")
            return False
    else:
        logger.error("No URLs to process")
        return False

def main():
    """Parse arguments and run the pipeline."""
    parser = argparse.ArgumentParser(description="Run the optimized Holocron knowledge pipeline")
    parser.add_argument('--limit', type=int, default=50,
                      help="Maximum number of URLs to process in this run (default: 50)")
    parser.add_argument('--priority', type=str, choices=['high', 'medium', 'low'], default=None,
                      help="Filter URLs by priority (optional)")
    parser.add_argument('--workers', type=int, default=3,
                      help="Number of worker processes (default: 3)")
    parser.add_argument('--batch-size', type=int, default=10,
                      help="Number of URLs to process before checkpointing (default: 10)")
    parser.add_argument('--requests-per-minute', type=int, default=30,
                      help="Maximum requests per minute for rate limiting (default: 30)")
    
    args = parser.parse_args()
    
    # Run the pipeline
    success = asyncio.run(run_pipeline(args))
    
    # Exit with appropriate status code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 