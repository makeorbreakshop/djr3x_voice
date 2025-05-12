#!/usr/bin/env python3
"""
XML Vector Processor

This script combines XML processing with vector generation and Pinecone upload:
1. Processes XML dump content
2. Generates embeddings (OpenAI + E5)
3. Uploads vectors to Pinecone with deduplication
4. Tracks processing status

Usage:
    python scripts/xml_vector_processor.py [--batch-size N] [--workers N] [--skip-deduplication]
"""

import os
import sys
import asyncio
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import json
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from tqdm import tqdm

# Add project root to path for imports (necessary for all imports)
root_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, root_dir)
# Add src directory to path for imports (necessary for wiki_markup_converter)
sys.path.insert(0, os.path.join(root_dir, 'src'))

# Import from scripts directory
from scripts.process_wiki_dump import WikiDumpProcessor
from scripts.upload_to_pinecone import PineconeUploader

# Import LocalDataProcessor directly
sys.path.append(os.path.join(root_dir, 'holocron'))
from knowledge.local_processor import LocalDataProcessor

# Import from project root
from process_status_manager import ProcessStatusManager, ProcessingStatus
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
        vectors_dir: str = "data/vectors",
        skip_deduplication: bool = False
    ):
        """
        Initialize the processor.
        
        Args:
            xml_file: Path to XML dump file
            batch_size: Size of processing batches
            workers: Number of worker processes
            vectors_dir: Directory for vector storage
            skip_deduplication: Whether to skip content deduplication
        """
        self.xml_file = Path(xml_file)
        self.batch_size = batch_size
        self.workers = workers
        self.vectors_dir = Path(vectors_dir)
        self.skip_deduplication = skip_deduplication
        self.content_fingerprints = {}
        self.fingerprint_file = Path("data/content_fingerprints.json")
        
        # Create output directories
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.wiki_processor = WikiDumpProcessor(str(self.xml_file), batch_size)
        self.data_processor = LocalDataProcessor(vectors_dir=str(self.vectors_dir))
        self.pinecone_uploader = PineconeUploader(batch_size=batch_size, skip_deduplication=skip_deduplication)
        self.dashboard = ProcessingDashboard(self.wiki_processor.status_manager)
        
        # Load existing fingerprints
        self._load_content_fingerprints()
        
        # Load environment variables
        load_dotenv()
        
    def _load_content_fingerprints(self):
        """Load content fingerprints from file."""
        if self.fingerprint_file.exists():
            try:
                with open(self.fingerprint_file, 'r') as f:
                    self.content_fingerprints = json.load(f)
                logger.info(f"Loaded {len(self.content_fingerprints)} content fingerprints")
            except Exception as e:
                logger.error(f"Error loading content fingerprints: {e}")
                self.content_fingerprints = {}
        
    def _save_content_fingerprints(self):
        """Save content fingerprints to file."""
        try:
            with open(self.fingerprint_file, 'w') as f:
                json.dump(self.content_fingerprints, f, indent=2)
            logger.info(f"Saved {len(self.content_fingerprints)} content fingerprints")
        except Exception as e:
            logger.error(f"Error saving content fingerprints: {e}")
    
    def _generate_content_fingerprint(self, content: Dict) -> str:
        """Generate a fingerprint for article content."""
        if not content:
            return ""
        
        # Create fingerprint from key content fields
        title = content.get('title', '')
        text = content.get('plain_text', '')
        revision = content.get('revision_id', '')
        
        fingerprint_data = f"{title}|{text[:1000]}|{revision}"  # Use first 1000 chars for efficiency
        return hashlib.md5(fingerprint_data.encode('utf-8')).hexdigest()
    
    def _content_changed(self, url: str, content: Dict) -> bool:
        """Check if content has changed based on fingerprint."""
        if self.skip_deduplication:
            return True
            
        new_fingerprint = self._generate_content_fingerprint(content)
        
        # If no fingerprint or fingerprint changed, content has changed
        if not new_fingerprint or url not in self.content_fingerprints:
            self.content_fingerprints[url] = new_fingerprint
            return True
            
        changed = self.content_fingerprints[url] != new_fingerprint
        
        # Update fingerprint if changed
        if changed:
            self.content_fingerprints[url] = new_fingerprint
            
        return changed
        
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
            unchanged_count = 0
            processed_count = 0
            
            for url in urls:
                # Skip already processed content
                status = self.wiki_processor.status_manager.get_status(url)
                if status and status.processed and status.vectorized and status.uploaded and not status.error:
                    # Check if content has changed before reprocessing
                    content = await self.wiki_processor._extract_article_content(url)
                    if not self._content_changed(url, content):
                        unchanged_count += 1
                        continue
                
                # Process changed or new content
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
                    processed_count += 1
            
            if unchanged_count > 0:
                logger.info(f"Skipped {unchanged_count} unchanged articles in batch {batch_num}")
                
            if not contents:
                logger.info(f"No new or changed content to process in batch {batch_num}")
                return None
            
            # Generate vectors
            logger.info(f"Processing {len(contents)} articles in batch {batch_num}")
            success = await self.data_processor.process_and_upload(contents)
            if not success:
                logger.error(f"Failed to process batch {batch_num}")
                return None
            
            # Save fingerprints after successful processing
            self._save_content_fingerprints()
            
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
            
            # Prioritize processing by groups (new first, then updates)
            high_priority_new = {url for url in new_urls if self._is_high_priority(url)}
            medium_priority_new = {url for url in new_urls if self._is_medium_priority(url)}
            low_priority_new = new_urls - high_priority_new - medium_priority_new
            
            # Combine into priority-ordered list
            urls_to_process = list(high_priority_new) + list(medium_priority_new) + list(low_priority_new) + list(update_urls)
            
            if not urls_to_process:
                logger.info("No new or updated articles to process")
                return
            
            # Process in batches
            batches = [
                urls_to_process[i:i + self.batch_size]
                for i in range(0, len(urls_to_process), self.batch_size)
            ]
            
            total_batches = len(batches)
            logger.info(f"Processing {len(urls_to_process)} articles in {total_batches} batches")
            logger.info(f"Priority breakdown: {len(high_priority_new)} high, {len(medium_priority_new)} medium, {len(low_priority_new)} low, {len(update_urls)} updates")
            
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
                
                # Save fingerprints periodically
                if batch_num % 10 == 0:
                    self._save_content_fingerprints()
                
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
                    
    def _is_high_priority(self, url: str) -> bool:
        """Check if URL is high priority."""
        # Example high priority keywords
        high_priority_terms = ["galaxy's_edge", "batuu", "oga", "droid", "r3x", "rex", "dj"]
        return any(term in url.lower() for term in high_priority_terms)
    
    def _is_medium_priority(self, url: str) -> bool:
        """Check if URL is medium priority."""
        # Example medium priority keywords
        medium_priority_terms = ["cantina", "entertainment", "music", "star_wars", "disney"]
        return any(term in url.lower() for term in medium_priority_terms)

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
        help='Size of processing batches (default: 100)'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=3,
        help='Number of worker processes (default: 3)'
    )
    parser.add_argument(
        '--vectors-dir',
        type=str,
        default="data/vectors",
        help='Directory for vector storage (default: data/vectors)'
    )
    parser.add_argument(
        '--skip-deduplication',
        action='store_true',
        help='Skip content deduplication checks'
    )
    args = parser.parse_args()
    
    processor = XMLVectorProcessor(
        xml_file=args.xml_file,
        batch_size=args.batch_size,
        workers=args.workers,
        vectors_dir=args.vectors_dir,
        skip_deduplication=args.skip_deduplication
    )
    await processor.run()

if __name__ == "__main__":
    asyncio.run(main()) 