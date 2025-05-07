#!/usr/bin/env python3
"""
Holocron RAG Pipeline Runner

This script runs the complete pipeline for the Holocron knowledge system:
1. Scrape canonical Star Wars content from Wookieepedia
2. Process content into appropriate chunks
3. Generate embeddings for the chunks
4. Upload to the Supabase vector database

Usage:
    python scripts/run_holocron_pipeline.py [--limit N] [--output FILE] [--skip-scrape]
    [--workers N] [--batch-size N] [--requests-per-minute N]
"""

import os
import sys
import json
import argparse
import asyncio
import logging
from datetime import datetime

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.holocron.wookieepedia_scraper import WookieepediaScraper
from src.holocron.data_processor import HolocronDataProcessor
from src.holocron.batch_processor import BatchProcessor

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
    """Run the complete Holocron pipeline."""
    # Create output directories if they don't exist
    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data/checkpoints", exist_ok=True)
    
    urls = []
    
    # Step 1: Get URLs (either from scraping or existing file)
    if not args.skip_scrape:
        logger.info("Starting content scraping from Wookieepedia")
        async with WookieepediaScraper() as scraper:
            urls = await scraper.get_article_urls(limit_per_category=args.limit)
            
            # Save URLs to JSON
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(urls, f, indent=2)
            logger.info(f"Saved {len(urls)} URLs to {args.output}")
    else:
        # Load previously scraped URLs
        logger.info(f"Loading URLs from {args.output}")
        try:
            with open(args.output, 'r', encoding='utf-8') as f:
                urls = json.load(f)
            logger.info(f"Loaded {len(urls)} URLs from {args.output}")
        except FileNotFoundError:
            logger.error(f"File not found: {args.output}")
            logger.error("Cannot skip scraping without existing output file")
            return False
    
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
            # Log final statistics
            logger.info(f"Total URLs processed: {processor.progress.processed_urls}")
            logger.info(f"Failed URLs: {len(processor.progress.failed_urls)}")
            if processor.progress.failed_urls:
                logger.info("Failed URLs:")
                for url in processor.progress.failed_urls:
                    logger.info(f"  - {url}")
            return True
        else:
            logger.error("Holocron pipeline failed during processing phase")
            return False
    else:
        logger.error("No URLs to process")
        return False

def main():
    """Parse arguments and run the pipeline."""
    parser = argparse.ArgumentParser(description="Run the Holocron knowledge pipeline")
    parser.add_argument('--limit', type=int, default=20,
                      help="Maximum articles per category to scrape (default: 20)")
    parser.add_argument('--output', type=str, default="data/wookieepedia_urls.json",
                      help="Path to save/load the URLs")
    parser.add_argument('--skip-scrape', action='store_true',
                      help="Skip the scraping step and use existing output file")
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