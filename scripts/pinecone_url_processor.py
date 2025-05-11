#!/usr/bin/env python3
"""
Pinecone URL Processing Pipeline

This script processes URLs and generates Pinecone-compatible vectors:
1. Fetches unprocessed URLs from the database
2. Processes content into chunks
3. Generates embeddings in Pinecone-compatible format
4. Saves vectors to Parquet files for batch import
5. Marks URLs as processed

Usage:
    python scripts/pinecone_url_processor.py [--batch-size N] [--workers N] [--namespace NAME]
"""

import os
import sys
import asyncio
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Add project root to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.holocron.wookieepedia_scraper import WookieepediaScraper
from src.holocron.data_processor import HolocronDataProcessor
from scripts.csv_to_parquet import convert_csv_to_parquet
from holocron.url_collector.url_store import URLStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PineconeURLProcessor:
    """Processes URLs and generates Pinecone-compatible vectors."""
    
    def __init__(
        self,
        batch_size: int = 50,
        workers: int = 3,
        namespace: str = 'default'
    ):
        """
        Initialize the URL processor.
        
        Args:
            batch_size: Number of URLs to process in each batch
            workers: Number of worker processes
            namespace: Pinecone namespace for the vectors
        """
        self.batch_size = batch_size
        self.workers = workers
        self.namespace = namespace
        
        # Load environment variables
        load_dotenv()
        
        # Initialize components
        self.url_store = URLStore()
        self.data_processor = HolocronDataProcessor()
        self.s3_bucket = os.getenv('S3_BUCKET')
        
        if not self.s3_bucket:
            raise ValueError("S3_BUCKET not found in environment")
            
    async def get_unprocessed_urls(self, limit: Optional[int] = None) -> List[str]:
        """Get unprocessed URLs from the database."""
        try:
            # Query for unprocessed URLs
            query = """
            SELECT url, priority
            FROM holocron_urls
            WHERE is_processed = false
            ORDER BY 
                CASE priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                discovered_at ASC
            """
            
            if limit:
                query += f" LIMIT {limit}"
                
            result = await self.url_store.execute_query(query)
            return [row['url'] for row in result]
            
        except Exception as e:
            logger.error(f"Error getting unprocessed URLs: {e}")
            return []
            
    async def process_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Process a single URL and generate Pinecone-compatible vectors.
        
        Args:
            url: URL to process
            
        Returns:
            Dictionary containing processed vectors and metadata
        """
        try:
            # Scrape content
            async with WookieepediaScraper() as scraper:
                content = await scraper.scrape_article(url)
                
            if not content:
                logger.error(f"Failed to scrape content from {url}")
                return None
                
            # Process content into chunks
            chunks = self.data_processor.chunk_article(content)
            
            # Generate embeddings for chunks
            vectors = []
            for chunk in chunks:
                embedding = await self.data_processor.generate_embedding(chunk['content'])
                
                # Create Pinecone-compatible vector
                vector = {
                    'id': f"{content['id']}_{chunk['chunk_id']}",
                    'values': embedding,
                    'metadata': {
                        'content': chunk['content'],
                        'url': url,
                        'title': content['title'],
                        'priority': content.get('priority', 'low')
                    }
                }
                vectors.append(vector)
                
            return {
                'url': url,
                'vectors': vectors,
                'metadata': {
                    'chunk_count': len(chunks),
                    'processed_at': datetime.now().isoformat()
                }
            }
            
        except Exception as e:
            logger.error(f"Error processing URL {url}: {e}")
            return None
            
    async def process_batch(
        self,
        urls: List[str],
        batch_num: int,
        total_batches: int
    ) -> List[Dict[str, Any]]:
        """
        Process a batch of URLs.
        
        Args:
            urls: List of URLs to process
            batch_num: Current batch number
            total_batches: Total number of batches
            
        Returns:
            List of processed results
        """
        logger.info(f"Processing batch {batch_num}/{total_batches} with {len(urls)} URLs")
        
        # Process URLs concurrently
        tasks = [self.process_url(url) for url in urls]
        results = await asyncio.gather(*tasks)
        
        # Filter out failed results
        successful_results = [r for r in results if r is not None]
        
        logger.info(
            f"Completed batch {batch_num}/{total_batches}: "
            f"{len(successful_results)}/{len(urls)} successful"
        )
        
        return successful_results
        
    def save_vectors_to_parquet(
        self,
        processed_results: List[Dict[str, Any]],
        batch_num: int
    ) -> str:
        """
        Save processed vectors to a Parquet file.
        
        Args:
            processed_results: List of processed URL results
            batch_num: Batch number for filename
            
        Returns:
            Path to the generated Parquet file
        """
        # Create output directory
        output_dir = f"data/vectors/{self.namespace}"
        os.makedirs(output_dir, exist_ok=True)
        
        # Flatten vectors into rows
        rows = []
        for result in processed_results:
            rows.extend(result['vectors'])
            
        # Convert to DataFrame
        df = pd.DataFrame(rows)
        
        # Save to Parquet
        output_file = os.path.join(
            output_dir,
            f"batch_{batch_num:04d}.parquet"
        )
        
        df.to_parquet(
            output_file,
            compression='snappy',
            index=False
        )
        
        logger.info(f"Saved {len(rows)} vectors to {output_file}")
        return output_file
        
    async def mark_urls_processed(self, urls: List[str]):
        """Mark URLs as processed in the database."""
        try:
            # Update URLs in batches
            for i in range(0, len(urls), 100):
                batch = urls[i:i + 100]
                query = """
                UPDATE holocron_urls
                SET 
                    is_processed = true,
                    processed_at = NOW()
                WHERE url = ANY($1)
                """
                await self.url_store.execute_query(query, [batch])
                
            logger.info(f"Marked {len(urls)} URLs as processed")
            
        except Exception as e:
            logger.error(f"Error marking URLs as processed: {e}")
            
    async def run(self, limit: Optional[int] = None):
        """
        Run the complete URL processing pipeline.
        
        Args:
            limit: Optional limit on number of URLs to process
        """
        try:
            # Get unprocessed URLs
            urls = await self.get_unprocessed_urls(limit)
            if not urls:
                logger.info("No unprocessed URLs found")
                return
                
            logger.info(f"Found {len(urls)} unprocessed URLs")
            
            # Process in batches
            batches = [
                urls[i:i + self.batch_size]
                for i in range(0, len(urls), self.batch_size)
            ]
            total_batches = len(batches)
            
            for i, batch_urls in enumerate(batches):
                # Process batch
                results = await self.process_batch(
                    batch_urls,
                    i + 1,
                    total_batches
                )
                
                if results:
                    # Save vectors
                    parquet_file = self.save_vectors_to_parquet(
                        results,
                        i + 1
                    )
                    
                    # Mark URLs as processed
                    processed_urls = [
                        result['url']
                        for result in results
                    ]
                    await self.mark_urls_processed(processed_urls)
                    
            logger.info("URL processing pipeline completed successfully")
            
        except Exception as e:
            logger.error(f"Error running URL processing pipeline: {e}")
            raise

async def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Process URLs and generate Pinecone-compatible vectors"
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=50,
        help='Number of URLs to process in each batch'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=3,
        help='Number of worker processes'
    )
    parser.add_argument(
        '--namespace',
        default='default',
        help='Pinecone namespace for the vectors'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Maximum number of URLs to process'
    )
    
    args = parser.parse_args()
    
    # Create and run processor
    processor = PineconeURLProcessor(
        batch_size=args.batch_size,
        workers=args.workers,
        namespace=args.namespace
    )
    
    await processor.run(args.limit)

if __name__ == '__main__':
    asyncio.run(main()) 