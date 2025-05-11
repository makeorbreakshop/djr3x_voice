#!/usr/bin/env python3
"""
XML Vector Processor

This script combines XML processing with vector generation and Pinecone upload:
1. Processes XML dump content
2. Generates embeddings (OpenAI + E5)
3. Uploads vectors to Pinecone
4. Tracks processing status

Usage:
    python scripts/xml_vector_processor.py [--batch-size N] [--workers N]
"""

import os
import sys
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import json
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from tqdm import tqdm

# Add project root to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from process_wiki_dump import WikiDumpProcessor
from scripts.upload_to_pinecone import PineconeUploader
from holocron.knowledge.local_processor import LocalDataProcessor
from process_status_manager import ProcessStatusManager
from processing_dashboard import ProcessingDashboard

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/xml_processing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

class XMLVectorProcessor:
    """Processes XML content into vectors and uploads to Pinecone."""
    
    def __init__(
        self,
        xml_file: str,
        batch_size: int = 100,
        workers: int = 3,
        vectors_dir: str = "data/vectors"
    ):
        """
        Initialize the processor.
        
        Args:
            xml_file: Path to XML dump file
            batch_size: Size of processing batches
            workers: Number of worker processes
            vectors_dir: Directory for vector storage
        """
        self.xml_file = Path(xml_file)
        self.batch_size = batch_size
        self.workers = workers
        self.vectors_dir = Path(vectors_dir)
        
        # Create output directories
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.wiki_processor = WikiDumpProcessor(str(self.xml_file), batch_size)
        self.data_processor = LocalDataProcessor(vectors_dir=str(self.vectors_dir))
        self.pinecone_uploader = PineconeUploader(batch_size=batch_size)
        self.dashboard = ProcessingDashboard(self.wiki_processor.status_manager)
        
        # Load environment variables
        load_dotenv()
        
    async def process_batch(
        self,
        urls: List[str],
        batch_num: int,
        total_batches: int
    ) -> Optional[str]:
        """
        Process a batch of URLs into vectors.
        
        Args:
            urls: List of URLs to process
            batch_num: Current batch number
            total_batches: Total number of batches
            
        Returns:
            Path to the generated Parquet file, or None on failure
        """
        try:
            # Extract content from XML
            contents = []
            for url in urls:
                content = await self.wiki_processor._extract_article_content(url)
                if content and content['is_canonical']:
                    contents.append({
                        'url': url,
                        'title': content['title'],
                        'content': content['plain_text'],
                        'metadata': {
                            'title': content['title'],
                            'url': url,
                            'categories': content['categories'],
                            'revision_id': content['revision_id']
                        }
                    })
            
            if not contents:
                logger.warning(f"No valid content found in batch {batch_num}")
                return None
            
            # Generate vectors
            success = await self.data_processor.process_and_upload(contents)
            if not success:
                logger.error(f"Failed to process batch {batch_num}")
                return None
            
            # Get the latest Parquet file (just generated)
            parquet_files = sorted(self.vectors_dir.glob("*.parquet"))
            if not parquet_files:
                logger.error(f"No Parquet file found for batch {batch_num}")
                return None
            
            latest_parquet = str(parquet_files[-1])
            logger.info(f"Generated vectors saved to {latest_parquet}")
            return latest_parquet
            
        except Exception as e:
            logger.error(f"Error processing batch {batch_num}: {e}")
            return None
            
    async def upload_vectors(self, parquet_file: str) -> bool:
        """
        Upload vectors from a Parquet file to Pinecone.
        
        Args:
            parquet_file: Path to Parquet file containing vectors
            
        Returns:
            True if upload successful, False otherwise
        """
        try:
            await self.pinecone_uploader.upload_file_async(parquet_file)
            return True
        except Exception as e:
            logger.error(f"Error uploading vectors from {parquet_file}: {e}")
            return False
            
    async def run(self):
        """Run the complete processing pipeline."""
        try:
            # Start the dashboard
            dashboard_task = asyncio.create_task(self.dashboard.start_cli())
            
            # Initialize Pinecone
            self.pinecone_uploader.init_index()
            
            # Get URLs to process
            xml_urls = await self.wiki_processor._collect_urls()
            logger.info(f"Found {len(xml_urls)} articles in XML dump")
            
            # Compare with existing processed URLs
            new_urls, update_urls, deleted_urls = self.wiki_processor.status_manager.compare_urls(xml_urls)
            urls_to_process = new_urls | update_urls
            
            if not urls_to_process:
                logger.info("No new or updated articles to process")
                return
            
            # Process in batches
            batches = [
                list(urls_to_process)[i:i + self.batch_size]
                for i in range(0, len(urls_to_process), self.batch_size)
            ]
            
            total_batches = len(batches)
            logger.info(f"Processing {len(urls_to_process)} articles in {total_batches} batches")
            
            for i, batch_urls in enumerate(batches):
                batch_num = i + 1
                
                # Process batch into vectors
                parquet_file = await self.process_batch(
                    batch_urls,
                    batch_num,
                    total_batches
                )
                
                if parquet_file:
                    # Upload vectors to Pinecone
                    if await self.upload_vectors(parquet_file):
                        # Update processing status
                        for url in batch_urls:
                            self.wiki_processor.status_manager.update_status(
                                url=url,
                                title=self.wiki_processor._get_title_from_url(url),
                                processed=True,
                                vectorized=True,
                                uploaded=True
                            )
                    else:
                        logger.error(f"Failed to upload vectors for batch {batch_num}")
                        
                # Save status after each batch
                self.wiki_processor.status_manager.save_status()
                
            logger.info("Processing pipeline completed successfully")
            
            # Save final metrics
            self.dashboard.save_metrics(
                f"logs/processing_metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            
        except Exception as e:
            logger.error(f"Error running processing pipeline: {e}")
            raise
            
        finally:
            # Clean up dashboard
            if 'dashboard_task' in locals():
                dashboard_task.cancel()
                try:
                    await dashboard_task
                except asyncio.CancelledError:
                    pass

async def main():
    """Parse arguments and run the processor."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Process XML dump into vectors and upload to Pinecone"
    )
    parser.add_argument(
        'xml_file',
        help='Path to XML dump file'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Number of articles to process in each batch'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=3,
        help='Number of worker processes'
    )
    parser.add_argument(
        '--vectors-dir',
        default='data/vectors',
        help='Directory for vector storage'
    )
    
    args = parser.parse_args()
    
    processor = XMLVectorProcessor(
        xml_file=args.xml_file,
        batch_size=args.batch_size,
        workers=args.workers,
        vectors_dir=args.vectors_dir
    )
    
    await processor.run()

if __name__ == '__main__':
    asyncio.run(main()) 