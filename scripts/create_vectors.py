#!/usr/bin/env python3
"""
Vector Creator for Wookieepedia Content

This script generates vector embeddings for processed Wookieepedia content:
1. Reads processed article JSON files
2. Generates embeddings using OpenAI
3. Saves vectors to Parquet files for upload

Usage:
    python scripts/create_vectors.py --input-dir DIR --output-dir DIR [--batch-size N] [--workers N]
"""

import os
import sys
import json
import argparse
import logging
import time
import hashlib
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import uuid

import pandas as pd
import numpy as np
from tqdm.asyncio import tqdm
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/vector_creation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Constants
MAX_CONCURRENT_REQUESTS = 5  # Limit concurrent API calls
EMBEDDING_MODEL = "text-embedding-ada-002"  # OpenAI embedding model
EMBEDDING_DIMENSIONS = 1536  # Dimensions for OpenAI embeddings
CHUNK_SIZE = 1000  # Characters per chunk for text splitting
CHUNK_OVERLAP = 200  # Overlap between chunks

class VectorCreator:
    """Creates vector embeddings from processed article content."""
    
    def __init__(self, input_dir: str, output_dir: str, batch_size: int = 100):
        """
        Initialize the vector creator.
        
        Args:
            input_dir: Directory containing processed article JSON files
            output_dir: Directory for output vector parquet files
            batch_size: Number of articles to process per batch
        """
        # Load environment variables
        load_dotenv()
        
        # Set up OpenAI client
        self.client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.batch_size = batch_size
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up API request semaphore
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        
        # Metrics
        self.processed_count = 0
        self.chunk_count = 0
        self.error_count = 0
        
    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a text using OpenAI API.
        
        Args:
            text: Text to embed
            
        Returns:
            List of embedding values
        """
        async with self.semaphore:
            try:
                response = await self.client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=text
                )
                return response.data[0].embedding
            except Exception as e:
                logger.error(f"Error generating embedding: {e}")
                self.error_count += 1
                # Return zero vector as fallback
                return [0.0] * EMBEDDING_DIMENSIONS
    
    def chunk_text(self, text: str, title: str) -> List[str]:
        """
        Split text into overlapping chunks.
        
        Args:
            text: Text to split
            title: Article title to include in each chunk
            
        Returns:
            List of text chunks
        """
        if not text:
            return []
            
        # Add title as context to each chunk
        chunks = []
        chunk_prefix = f"# {title}\n\n"
        
        # Simple character-based chunking with overlap
        start = 0
        text_len = len(text)
        
        while start < text_len:
            end = min(start + CHUNK_SIZE, text_len)
            
            # If not at the beginning, include overlap
            if start > 0:
                start = max(0, start - CHUNK_OVERLAP)
            
            # Extract chunk and add prefix
            chunk = chunk_prefix + text[start:end]
            chunks.append(chunk)
            
            # Move to next chunk
            start = end
            
        return chunks
    
    def generate_chunk_id(self, url: str, chunk_index: int) -> str:
        """
        Generate a unique ID for a chunk.
        
        Args:
            url: Source URL
            chunk_index: Index of the chunk
            
        Returns:
            Unique ID string
        """
        # Generate a hash from URL and chunk index
        hash_base = f"{url}_{chunk_index}"
        hash_id = hashlib.md5(hash_base.encode('utf-8')).hexdigest()
        
        # Use a UUID-like format with hash
        return f"chunk_{hash_id}"
    
    async def process_article(self, article_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Process a single article to generate vector chunks.
        
        Args:
            article_data: Article data dictionary
            
        Returns:
            List of vector dictionaries
        """
        title = article_data.get('title', '')
        content = article_data.get('plain_text', '')
        url = article_data.get('url', '')
        
        if not content:
            logger.warning(f"Article '{title}' missing content, skipping")
            return []
        
        if not url:
            logger.warning(f"Article '{title}' missing URL, skipping")
            return []
        
        # Create chunks
        chunks = self.chunk_text(content, title)
        
        # Log successful article processing
        logger.info(f"Processing article: '{title}' with URL: {url} into {len(chunks)} chunks")
        
        # Process each chunk
        vector_records = []
        
        for i, chunk_text in enumerate(chunks):
            # Generate a unique ID
            chunk_id = self.generate_chunk_id(url, i)
            
            # Generate embedding
            embedding = await self.generate_embedding(chunk_text)
            
            # Create metadata
            metadata = {
                'title': title,
                'url': url,
                'chunk_index': i,
                'total_chunks': len(chunks),
                'categories': article_data.get('categories', []),
                'is_canonical': article_data.get('is_canonical', True),
                'content': chunk_text,
                'source': 'wookieepedia'
            }
            
            # Add to records
            vector_records.append({
                'id': chunk_id,
                'values': embedding,
                'metadata': json.dumps(metadata)  # Convert metadata to JSON string
            })
            
            self.chunk_count += 1
            
        return vector_records
    
    async def process_batch(self, file_paths: List[Path], batch_num: int) -> Optional[str]:
        """
        Process a batch of articles.
        
        Args:
            file_paths: List of JSON file paths
            batch_num: Batch number
            
        Returns:
            Path to the generated Parquet file, or None on failure
        """
        all_records = []
        
        for file_path in file_paths:
            try:
                # Load article data
                with open(file_path, 'r') as f:
                    article_data = json.load(f)
                
                # Process article
                records = await self.process_article(article_data)
                all_records.extend(records)
                self.processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")
                self.error_count += 1
        
        if not all_records:
            logger.warning(f"No records generated for batch {batch_num}")
            return None
            
        # Create DataFrame
        df = pd.DataFrame(all_records)
        
        # Generate output file name
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        output_path = self.output_dir / f"vectors_{timestamp}_{batch_num}.parquet"
        
        # Save to Parquet
        df.to_parquet(output_path)
        
        logger.info(f"Saved {len(all_records)} vectors to {output_path}")
        return str(output_path)
    
    async def process_all(self, workers: int = 3) -> bool:
        """
        Process all articles in the input directory.
        
        Args:
            workers: Number of worker tasks to use
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Find all JSON files recursively in the input directory
            json_files = list(self.input_dir.glob("**/*.json"))
            logger.info(f"Found {len(json_files)} article files for processing")
            
            if not json_files:
                logger.error(f"No JSON files found in {self.input_dir}")
                return False
            
            # Split into batches
            batches = [json_files[i:i + self.batch_size] for i in range(0, len(json_files), self.batch_size)]
            logger.info(f"Split into {len(batches)} batches of up to {self.batch_size} articles each")
            
            # Process batches with worker tasks
            batch_results = []
            
            # Create queue
            queue = asyncio.Queue()
            
            # Add all batches to queue
            for i, batch in enumerate(batches):
                await queue.put((i, batch))
            
            # Create worker tasks
            tasks = []
            for i in range(workers):
                tasks.append(asyncio.create_task(self._worker(queue)))
            
            # Wait for all workers to complete
            batch_results = await asyncio.gather(*tasks)
            
            # Flatten list of results
            flat_results = [r for sublist in batch_results for r in sublist if r]
            
            logger.info(f"Processed {self.processed_count} articles into {self.chunk_count} chunks")
            logger.info(f"Generated {len(flat_results)} vector files")
            
            if self.error_count > 0:
                logger.warning(f"Encountered {self.error_count} errors during processing")
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing articles: {e}")
            return False

    async def _worker(self, queue: asyncio.Queue) -> List[str]:
        """
        Worker to process batches from queue.
        
        Args:
            queue: Queue of batch tuples (batch_num, file_paths)
            
        Returns:
            List of output file paths
        """
        results = []
        while not queue.empty():
            try:
                batch_num, batch = await queue.get()
                logger.info(f"Worker processing batch {batch_num+1} with {len(batch)} articles")
                
                output_file = await self.process_batch(batch, batch_num+1)
                if output_file:
                    results.append(output_file)
                
                # Sleep briefly between batches to avoid API rate limits
                await asyncio.sleep(0.1)
                
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f"Worker error: {e}")
            finally:
                queue.task_done()
                
        return results

async def main_async():
    parser = argparse.ArgumentParser(description="Generate vector embeddings for processed Wookieepedia content")
    parser.add_argument("--input-dir", required=True, help="Directory containing processed article JSON files")
    parser.add_argument("--output-dir", required=True, help="Directory for output vector Parquet files")
    parser.add_argument("--batch-size", type=int, default=100, help="Number of articles to process per batch")
    parser.add_argument("--workers", type=int, default=3, help="Number of worker processes")
    args = parser.parse_args()
    
    creator = VectorCreator(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        batch_size=args.batch_size
    )
    
    success = await creator.process_all(workers=args.workers)
    
    if not success:
        logger.error("Vector creation failed")
        sys.exit(1)
        
    logger.info("Vector creation completed successfully")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main() 