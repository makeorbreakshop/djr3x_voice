#!/usr/bin/env python3
"""
Robust Vector Creation Script

This script is a more robust version of create_vectors.py that better handles missing files
and errors during processing. It creates vector embeddings for processed Wookieepedia articles.

Usage:
    python scripts/create_vectors_robust.py --input-dir data/processed_articles --output-dir data/vectors [options]
"""

import os
import sys
import json
import argparse
import logging
import time
import asyncio
import concurrent.futures
import glob
import signal
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
import traceback

import pandas as pd
import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Add parent directory to path so we can import from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import ProcessStatusManager for URL tracking
from process_status_manager import ProcessStatusManager
# Alternative import if needed:
# from src.holocron.wiki_processing.process_status_manager import ProcessStatusManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/vector_creation_robust_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Constants
CHUNK_SIZE = 500  # Maximum tokens per chunk
OVERLAP = 100  # Overlap between chunks
BATCH_SIZE = 10  # Number of articles to process in parallel
MAX_CONCURRENT_REQUESTS = 2  # Maximum concurrent API requests - REDUCED to avoid rate limits
EMBEDDING_MODEL = "text-embedding-ada-002"
EMBEDDING_DIMENSIONS = 1536  # Dimensions for the OpenAI embedding model
PROCESSING_STATUS_FILE = "data/processing_status.csv"
EMBEDDING_BATCH_SIZE = 10  # Number of chunks per embedding API call - REDUCED to avoid rate limits
MAX_RETRIES = 5  # Increased maximum number of retries for API calls
RATE_LIMIT_DELAY = 2.0  # Longer delay between embedding batches to avoid rate limits (seconds)
# OpenAI rate limits are approximately 300K tokens per minute for embeddings
MAX_TOKENS_PER_MINUTE = 150000  # Target 150K to stay safely under the 300K limit
TOKEN_WINDOW_SIZE = 60  # Window size in seconds for token rate calculation

# Global flag for graceful shutdown
shutdown_requested = False

class RobustVectorCreator:
    """Creates vectors from processed article files with robust error handling."""
    
    def __init__(self, input_dir: str, output_dir: str, batch_size: int = BATCH_SIZE, 
                 max_workers: int = 5, start_file: Optional[str] = None, test_mode: bool = False,
                 skip_processed: bool = True, concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
                 embedding_batch_size: int = EMBEDDING_BATCH_SIZE):
        """
        Initialize the vector creator.
        
        Args:
            input_dir: Directory containing processed articles
            output_dir: Directory to output vector files
            batch_size: Number of articles to process in parallel
            max_workers: Maximum number of worker processes
            start_file: Optional starting file to resume processing
            test_mode: Run in test mode (no actual API calls)
            skip_processed: Skip articles that have already been processed
            concurrent_requests: Maximum concurrent API requests
            embedding_batch_size: Number of chunks per embedding API call
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.test_mode = test_mode
        self.start_file = start_file
        self.skip_processed = skip_processed
        self.concurrent_requests = concurrent_requests
        self.embedding_batch_size = embedding_batch_size
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize OpenAI client
        self.client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        # Initialize ProcessStatusManager for URL tracking
        self.status_manager = ProcessStatusManager(PROCESSING_STATUS_FILE)
        logger.info(f"Loaded {len(self.status_manager.get_all_statuses())} URL statuses for deduplication")
        
        # Semaphore to limit concurrent API requests
        self.semaphore = asyncio.Semaphore(self.concurrent_requests)
        
        # Stats
        self.total_articles = 0
        self.total_chunks = 0
        self.successful_articles = 0
        self.failed_articles = 0
        self.skipped_articles = 0
        self.start_time = time.time()
        self.rate_limited_count = 0
        self.token_count = 0
        
        # Token rate limiting
        self.token_timestamps = []
        self.token_counts = []
        
        # Tracking the last processed file for resumability
        self.last_processed_file = None
    
    def find_json_files(self) -> List[str]:
        """
        Find all JSON files in the input directory.
        
        Returns:
            List of JSON file paths
        """
        logger.info(f"Finding JSON files in {self.input_dir}...")
        
        # Using glob pattern to find all JSON files recursively
        json_files = []
        batch_dirs = sorted(self.input_dir.glob("batch_*"))
        
        for batch_dir in batch_dirs:
            if not batch_dir.is_dir():
                continue
            
            batch_files = list(batch_dir.glob("*.json"))
            for file_path in batch_files:
                json_files.append(str(file_path))
        
        if not json_files:
            logger.warning(f"No JSON files found in {self.input_dir}")
            return []
        
        # Start from a specific file if specified
        if self.start_file:
            start_index = next((i for i, f in enumerate(json_files) if self.start_file in f), None)
            if start_index is not None:
                logger.info(f"Starting from file {self.start_file} (index {start_index})")
                json_files = json_files[start_index:]
            else:
                logger.warning(f"Start file {self.start_file} not found, processing all files")
        
        logger.info(f"Found {len(json_files)} JSON files")
        return json_files
    
    async def get_current_token_rate(self) -> float:
        """
        Calculate the current token rate (tokens per minute) based on recent history.
        
        Returns:
            Current token rate in tokens per minute
        """
        now = time.time()
        # The cleanup is now done in record_token_usage
        
        if not self.token_timestamps:
            return 0.0
        
        # Calculate tokens per minute based on recent history
        total_tokens = sum(self.token_counts)
        window_duration = now - self.token_timestamps[0]
        # Avoid division by zero and ensure we're calculating per minute
        if window_duration <= 0:
            return 0.0
        
        # Convert to tokens per minute
        return (total_tokens / window_duration) * 60
    
    async def record_token_usage(self, tokens: int) -> None:
        """
        Record token usage for rate limiting.
        
        Args:
            tokens: Number of tokens used
        """
        now = time.time()
        # Clean up old timestamps before adding new ones
        while self.token_timestamps and now - self.token_timestamps[0] > TOKEN_WINDOW_SIZE:
            self.token_timestamps.pop(0)
            self.token_counts.pop(0)
            
        self.token_timestamps.append(now)
        self.token_counts.append(tokens)
        self.token_count += tokens
    
    async def wait_for_token_budget(self, requested_tokens: int) -> None:
        """
        Wait until there's enough token budget available.
        
        Args:
            requested_tokens: Number of tokens to be used
        """
        current_rate = await self.get_current_token_rate()
        
        # If we're below 60% of the limit, don't wait at all
        if current_rate < (MAX_TOKENS_PER_MINUTE * 0.6):
            return
            
        # If we're over 90% of the limit, wait longer
        if current_rate >= (MAX_TOKENS_PER_MINUTE * 0.9):
            # Calculate a sensible wait time based on how far we are over the limit
            over_limit_factor = current_rate / MAX_TOKENS_PER_MINUTE
            wait_time = min(over_limit_factor * 1.0, 3.0)  # Cap at 3 seconds
            logger.debug(f"Rate limiting: current rate {current_rate:.0f} approaching maximum {MAX_TOKENS_PER_MINUTE}, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
            return
            
        # We're between 60% and 90% of the limit, add a small delay
        await asyncio.sleep(0.5)
    
    async def create_embedding(self, text: str) -> List[float]:
        """
        Create an embedding for the given text.
        
        Args:
            text: Text to create an embedding for
            
        Returns:
            Embedding vector
        """
        if self.test_mode:
            # Return a dummy embedding for testing
            return [0.0] * EMBEDDING_DIMENSIONS
        
        for retry in range(MAX_RETRIES):
            try:
                async with self.semaphore:
                    response = await self.client.embeddings.create(
                        model=EMBEDDING_MODEL,
                        input=text
                    )
                    self.token_count += response.usage.total_tokens
                    return response.data[0].embedding
            except Exception as e:
                if "rate limit" in str(e).lower():
                    self.rate_limited_count += 1
                    wait_time = (2 ** retry) * 1  # Exponential backoff starting at 1 second
                    logger.warning(f"Rate limit hit, retrying in {wait_time}s (attempt {retry+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Error creating embedding: {e}")
                    if retry < MAX_RETRIES - 1:
                        await asyncio.sleep(1)  # Brief pause before retry
                    else:
                        # Return zero vector as fallback after max retries
                        return [0.0] * EMBEDDING_DIMENSIONS
        
        # Return zero vector if all retries failed
        return [0.0] * EMBEDDING_DIMENSIONS
    
    async def create_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Create embeddings for a batch of texts.
        
        Args:
            texts: List of texts to create embeddings for
            
        Returns:
            List of embedding vectors
        """
        if self.test_mode:
            # Return dummy embeddings for testing
            return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]
        
        # Estimate token count before making API call
        # Each token is roughly 4 chars for English text, but be conservative
        estimated_tokens = min(sum(len(text) // 3 for text in texts), 25000)
        
        # Wait for token budget availability
        await self.wait_for_token_budget(estimated_tokens)
        
        for retry in range(MAX_RETRIES):
            try:
                async with self.semaphore:
                    # Do not add fixed delay here - we now control this dynamically
                    # via the wait_for_token_budget method
                    
                    start_time = time.time()
                    response = await self.client.embeddings.create(
                        model=EMBEDDING_MODEL,
                        input=texts
                    )
                    duration = time.time() - start_time
                    
                    # Record actual token usage with proper rate calculation
                    # Cap the tokens to a more realistic value to avoid token calculation issues
                    actual_tokens = min(response.usage.total_tokens, 5000 * len(texts))
                    await self.record_token_usage(actual_tokens)
                    
                    # Fix token rate calculation to use actual tokens instead of calculated per-minute rate
                    tokens_per_second = actual_tokens / duration if duration > 0 else 0
                    current_rate = await self.get_current_token_rate()
                    
                    # Only log the actual rate, not a calculated value
                    logger.debug(f"Processed {len(texts)} chunks, {actual_tokens} tokens in {duration:.2f}s " 
                                f"({tokens_per_second:.1f} tokens/s, rate: {current_rate:.0f} tokens/min)")
                    
                    return [item.embedding for item in response.data]
            except Exception as e:
                if "rate limit" in str(e).lower():
                    self.rate_limited_count += 1
                    # Use a more aggressive exponential backoff
                    wait_time = (2 ** retry) * 2 + 1  # Exponential backoff starting at 3 seconds (increased from 2)
                    logger.warning(f"Rate limit hit, retrying in {wait_time}s (attempt {retry+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Error creating batch embeddings: {e}")
                    if retry < MAX_RETRIES - 1:
                        await asyncio.sleep(3)  # Longer pause before retry (increased from 2)
                    else:
                        # Return zero vectors as fallback after max retries
                        return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]
        
        # Return zero vectors if all retries failed
        return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]
    
    def chunk_text(self, text: str, title: str, url: str) -> List[Dict[str, Any]]:
        """
        Split text into chunks with overlap.
        
        Args:
            text: Text to chunk
            title: Article title
            url: Article URL
            
        Returns:
            List of chunk dictionaries
        """
        # Simple word-based chunking
        words = text.split()
        chunks = []
        
        if len(words) <= CHUNK_SIZE:
            # If text is small enough, just use it as a single chunk
            return [{
                "id": f"{url}_chunk_1",
                "content": text,
                "title": title,
                "url": url,
                "chunk_index": 1,
                "total_chunks": 1
            }]
        
        # Split into overlapping chunks
        chunk_index = 1
        start = 0
        
        while start < len(words):
            end = min(start + CHUNK_SIZE, len(words))
            chunk_text = " ".join(words[start:end])
            
            # Create chunk with metadata
            chunks.append({
                "id": f"{url}_chunk_{chunk_index}",
                "content": chunk_text,
                "title": title,
                "url": url,
                "chunk_index": chunk_index,
                "total_chunks": (len(words) - 1) // (CHUNK_SIZE - OVERLAP) + 1
            })
            
            start += CHUNK_SIZE - OVERLAP
            chunk_index += 1
        
        return chunks
    
    def _extract_url_from_file_path(self, file_path: str) -> str:
        """
        Extract a Wookieepedia URL from a file path.
        
        Args:
            file_path: Path to article JSON file
            
        Returns:
            URL for the article
        """
        # Extract the filename without extension
        file_name = Path(file_path).stem
        
        # Generate Wookieepedia URL
        url = f"https://starwars.fandom.com/wiki/{file_name}"
        
        return url
    
    async def process_article(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Process a single article.
        
        Args:
            file_path: Path to article JSON file
            
        Returns:
            List of vectors with metadata
        """
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                logger.warning(f"File does not exist: {file_path}")
                return []
            
            # Extract URL from file path
            url = self._extract_url_from_file_path(file_path)
            
            # Check if URL has already been processed and uploaded
            if self.skip_processed and url:
                status = self.status_manager.get_status(url)
                if status and (status.uploaded or status.vectorized):
                    logger.info(f"Skipping already processed URL: {url}")
                    self.skipped_articles += 1
                    return []
            
            # Load article
            with open(file_path, 'r', encoding='utf-8') as f:
                try:
                    article = json.load(f)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in {file_path}")
                    return []
            
            # Extract data
            title = article.get('title', 'Unknown')
            content = article.get('content', '')
            
            # Skip empty content
            if not content:
                logger.warning(f"Empty content in {file_path}")
                return []
            
            # Use URL from article if available, otherwise use the one from file path
            article_url = article.get('url', '')
            if article_url:
                url = article_url
            
            # Chunk the content
            chunks = self.chunk_text(content, title, url)
            self.total_chunks += len(chunks)
            
            # Create vectors for chunks in batches - use smaller batches to reduce rate limit issues
            vectors = []
            # Use smaller batch size than the one specified in the constructor to help with rate limiting
            actual_batch_size = min(5, self.embedding_batch_size)
            chunk_batches = [chunks[i:i+actual_batch_size] for i in range(0, len(chunks), actual_batch_size)]
            
            for batch in chunk_batches:
                # Prepare batch texts
                batch_texts = [chunk['content'] for chunk in batch]
                
                # Get embeddings for batch
                batch_embeddings = await self.create_embeddings_batch(batch_texts)
                
                # Match embeddings with chunks
                for i, embedding in enumerate(batch_embeddings):
                    chunk = batch[i]
                    vectors.append({
                        "id": chunk["id"],
                        "values": embedding,
                        "metadata": json.dumps({
                            "content": chunk["content"],
                            "title": chunk["title"],
                            "url": chunk["url"],
                            "chunk_index": chunk["chunk_index"],
                            "total_chunks": chunk["total_chunks"]
                        })
                    })
                
                # Add a small delay between sub-batches to avoid hitting rate limits
                if len(chunk_batches) > 1:
                    await asyncio.sleep(0.5)
            
            # Mark URL as vectorized
            if url and not self.test_mode:
                self.status_manager.mark_vectorized(url)
            
            self.successful_articles += 1
            return vectors
            
        except Exception as e:
            logger.error(f"Error processing {file_path}: {e}")
            logger.error(traceback.format_exc())
            self.failed_articles += 1
            return []
    
    async def process_batch(self, batch_num: int, file_batch: List[str]) -> None:
        """
        Process a batch of articles.
        
        Args:
            batch_num: Batch number
            file_batch: List of file paths
        """
        logger.info(f"Worker processing batch {batch_num} with {len(file_batch)} articles")
        
        # Process articles in parallel
        tasks = [self.process_article(file_path) for file_path in file_batch]
        batch_results = await asyncio.gather(*tasks)
        
        # Flatten results
        vectors = [vector for result in batch_results for vector in result]
        
        if not vectors:
            logger.warning(f"No records generated for batch {batch_num}")
            return
        
        # Create dataframe and save to parquet
        df = pd.DataFrame(vectors)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_file = self.output_dir / f"vectors_{timestamp}_{batch_num}.parquet"
        
        try:
            df.to_parquet(output_file)
            logger.info(f"Saved {len(vectors)} vectors to {output_file}")
        except Exception as e:
            logger.error(f"Error saving vectors to {output_file}: {e}")
    
    async def run(self) -> None:
        """Run the vector creation process."""
        json_files = self.find_json_files()
        self.total_articles = len(json_files)
        
        if not json_files:
            logger.error("No JSON files found, aborting")
            return
        
        logger.info(f"Starting vector creation for {self.total_articles} articles...")
        logger.info(f"Using {self.concurrent_requests} concurrent API requests and {self.embedding_batch_size} chunks per batch")
        logger.info(f"Using {RATE_LIMIT_DELAY}s delay between embedding batches to avoid rate limits")
        logger.info(f"Target token rate: {MAX_TOKENS_PER_MINUTE} tokens/minute (OpenAI limit ~300K)")
        
        # Process in batches
        file_batches = [json_files[i:i+self.batch_size] for i in range(0, len(json_files), self.batch_size)]
        
        for batch_num, file_batch in enumerate(file_batches, 1):
            # Check if shutdown was requested
            if shutdown_requested:
                logger.info("Shutdown requested, saving progress and exiting...")
                # Save the last processed file for resumption
                if self.last_processed_file:
                    last_file = os.path.basename(self.last_processed_file)
                    logger.info(f"Last processed file: {last_file}")
                    logger.info(f"To resume, run with: --start-file {last_file}")
                
                # Save status
                self.status_manager.save()
                
                # Print progress summary
                processed = min((batch_num-1) * self.batch_size, self.total_articles)
                logger.info(f"Progress summary:")
                logger.info(f"- Processed {processed}/{self.total_articles} articles ({processed/self.total_articles:.1%})")
                logger.info(f"- Created {self.total_chunks} chunks")
                logger.info(f"- Skipped {self.skipped_articles} articles")
                return
            
            # Add delay between batches to avoid hitting rate limits
            if batch_num > 1:
                # Only add minimal delay between batches if rate limit delay is enabled
                current_rate = await self.get_current_token_rate()
                if current_rate > MAX_TOKENS_PER_MINUTE * 0.7:  # If we're at 70% or more of our target rate
                    # Use a longer delay to avoid hitting the rate limit
                    await asyncio.sleep(min(RATE_LIMIT_DELAY * 3, 2.0))
                elif current_rate > MAX_TOKENS_PER_MINUTE * 0.5:  # If we're at 50-70% of our target rate
                    # Use a minimal delay
                    await asyncio.sleep(min(RATE_LIMIT_DELAY * 2, 1.0))
                else:
                    # Very minimal delay when well under limit
                    await asyncio.sleep(0.5)
            
            await self.process_batch(batch_num, file_batch)
            
            # Update last processed file
            if file_batch:
                self.last_processed_file = file_batch[-1]
            
            # Print progress
            processed = min(batch_num * self.batch_size, self.total_articles)
            elapsed = time.time() - self.start_time
            articles_per_second = processed / elapsed if elapsed > 0 else 0
            chunks_per_second = self.total_chunks / elapsed if elapsed > 0 else 0
            current_token_rate = await self.get_current_token_rate()
            
            remaining = self.total_articles - processed
            eta_seconds = remaining / articles_per_second if articles_per_second > 0 else 0
            eta = datetime.fromtimestamp(time.time() + eta_seconds).strftime('%Y-%m-%d %H:%M:%S')
            
            logger.info(f"Progress: {processed}/{self.total_articles} articles "
                       f"({processed/self.total_articles:.1%}) - "
                       f"Skipped: {self.skipped_articles} articles - "
                       f"Speed: {articles_per_second:.2f} articles/sec, {chunks_per_second:.2f} chunks/sec - "
                       f"Token rate: {current_token_rate:.0f} tokens/min - "
                       f"Rate limits hit: {self.rate_limited_count} - "
                       f"ETA: {eta}")
        
        # Print summary
        elapsed = time.time() - self.start_time
        tokens_per_minute = (self.token_count / elapsed) * 60 if elapsed > 0 else 0
        logger.info(f"Vector creation completed in {elapsed:.2f}s")
        logger.info(f"Total articles: {self.total_articles}")
        logger.info(f"Successful articles: {self.successful_articles}")
        logger.info(f"Failed articles: {self.failed_articles}")
        logger.info(f"Skipped articles: {self.skipped_articles}")
        logger.info(f"Total chunks: {self.total_chunks}")
        logger.info(f"Total tokens: {self.token_count}")
        logger.info(f"Average token rate: {tokens_per_minute:.0f} tokens/minute")
        logger.info(f"Rate limits hit: {self.rate_limited_count}")

def signal_handler(sig, frame):
    """Handle interrupt signals for graceful shutdown."""
    global shutdown_requested
    if not shutdown_requested:
        logger.info("Interrupt received, will save progress and exit after current batch completes...")
        shutdown_requested = True
    else:
        logger.info("Second interrupt received, forcing exit...")
        sys.exit(1)

def main():
    """Main function."""
    # Global variable declarations must appear at the beginning of the function
    global MAX_TOKENS_PER_MINUTE, RATE_LIMIT_DELAY
    
    parser = argparse.ArgumentParser(description='Create vectors from processed articles with robust error handling')
    parser.add_argument('--input-dir', type=str, default='data/processed_articles', help='Input directory')
    parser.add_argument('--output-dir', type=str, default='data/vectors', help='Output directory')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='Batch size')
    parser.add_argument('--workers', type=int, default=5, help='Number of worker processes')
    parser.add_argument('--start-file', type=str, help='File to start processing from')
    parser.add_argument('--test', action='store_true', help='Run in test mode')
    parser.add_argument('--no-skip', action='store_true', help='Do not skip already processed URLs')
    parser.add_argument('--concurrent-requests', type=int, default=MAX_CONCURRENT_REQUESTS, 
                        help=f'Maximum concurrent API requests (default: {MAX_CONCURRENT_REQUESTS})')
    parser.add_argument('--embedding-batch-size', type=int, default=EMBEDDING_BATCH_SIZE,
                        help=f'Number of chunks per embedding API call (default: {EMBEDDING_BATCH_SIZE})')
    parser.add_argument('--rate-limit-delay', type=float, default=RATE_LIMIT_DELAY,
                        help=f'Delay between embedding batches (default: {RATE_LIMIT_DELAY}s)')
    parser.add_argument('--max-tokens-per-minute', type=int, default=MAX_TOKENS_PER_MINUTE,
                        help=f'Maximum tokens per minute (default: {MAX_TOKENS_PER_MINUTE})')
    args = parser.parse_args()
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Use local variables for overridden constants
    concurrent_requests = args.concurrent_requests if args.concurrent_requests else MAX_CONCURRENT_REQUESTS
    embedding_batch_size = args.embedding_batch_size if args.embedding_batch_size else EMBEDDING_BATCH_SIZE
    
    # Set global constants if provided via command line
    if args.max_tokens_per_minute:
        MAX_TOKENS_PER_MINUTE = args.max_tokens_per_minute
        
    if args.rate_limit_delay is not None:
        RATE_LIMIT_DELAY = args.rate_limit_delay
    
    creator = RobustVectorCreator(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        max_workers=args.workers,
        start_file=args.start_file,
        test_mode=args.test,
        skip_processed=not args.no_skip,
        concurrent_requests=concurrent_requests,
        embedding_batch_size=embedding_batch_size
    )
    
    asyncio.run(creator.run())

if __name__ == "__main__":
    main() 