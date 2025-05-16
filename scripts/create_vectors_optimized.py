#!/usr/bin/env python3
"""
Optimized Vector Creation Script

This script creates vector embeddings for processed Wookieepedia articles.
It checks both the processing_status.csv and the vectors_optimized directory
to skip articles that have already been processed.

Usage:
    python scripts/create_vectors_optimized.py --input-dir data/processed_articles --output-dir data/vectors_optimized [options]
"""

import os
import sys
import json
import argparse
import logging
import time
import asyncio
import glob
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional, Set
import traceback
import signal

import pandas as pd
import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI
import tiktoken

# Add parent directory to path so we can import from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import ProcessStatusManager for URL tracking
from process_status_manager import ProcessStatusManager

# Configure argument parser
parser = argparse.ArgumentParser(description='Create optimized vector embeddings')
parser.add_argument('--input-dir', type=str, required=True,
                  help='Directory containing processed article JSON files')
parser.add_argument('--output-dir', type=str, required=True,
                  help='Directory to save vector parquet files')
parser.add_argument('--optimized-dir', type=str,
                  help='Directory containing optimized vectors (for resuming)')
parser.add_argument('--batch-size', type=int, default=100,
                  help='Number of articles to process in parallel')
parser.add_argument('--concurrent-requests', type=int, default=20,
                  help='Maximum number of concurrent API requests')
parser.add_argument('--embedding-batch-size', type=int, default=100,
                  help='Number of chunks per embedding API call')
parser.add_argument('--max-tokens-per-minute', type=int, default=1000000,
                  help='Target tokens per minute for rate limiting')
parser.add_argument('--rate-limit-delay', type=float, default=0.05,
                  help='Delay between embedding batches')
parser.add_argument('--start-file', type=str,
                  help='Start processing from this file')
parser.add_argument('--test-mode', action='store_true',
                  help='Run in test mode with limited articles')
parser.add_argument('--log-level', type=str, default='WARNING',
                  choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                  help='Set the logging level')
parser.add_argument('--progress-interval', type=int, default=50,
                  help='Number of batches between progress updates')
args = parser.parse_args()

# Configure logging with minimal handlers and controlled verbosity
log_file = f"logs/vector_creation_optimized_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=getattr(logging, args.log_level),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Suppress httpx logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Load environment variables
load_dotenv()

# Constants
CHUNK_SIZE = 256  # Maximum tokens per chunk
OVERLAP = 64  # Overlap between chunks
EMBEDDING_MODEL = "text-embedding-ada-002"
EMBEDDING_DIMENSIONS = 1536  # Dimensions for the OpenAI embedding model
PROCESSING_STATUS_FILE = "data/processing_status.csv"
MAX_RETRIES = 5  # Maximum number of retries for API calls
TOKEN_WINDOW_SIZE = 60  # Window size in seconds for token rate calculation
TOKENIZER = tiktoken.get_encoding("cl100k_base")  # OpenAI's tokenizer

# Global flag for graceful shutdown
shutdown_requested = False

# Use command line arguments for these values
BATCH_SIZE = args.batch_size
MAX_CONCURRENT_REQUESTS = args.concurrent_requests
EMBEDDING_BATCH_SIZE = args.embedding_batch_size
TOKEN_RATE_LIMIT = args.max_tokens_per_minute
RATE_LIMIT_DELAY = args.rate_limit_delay

# Base delay for exponential backoff (seconds)
BASE_BACKOFF_TIME = 1.0
MAX_BACKOFF_TIME = 30.0

class TokenRateLimiter:
    """Rate limiter based on token usage."""
    
    def __init__(self, tokens_per_minute):
        self.tokens_per_minute = tokens_per_minute
        self.tokens_per_second = tokens_per_minute / 60
        self.tokens_used = 0
        self.last_reset = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self, tokens):
        """Acquire permission to use tokens, waiting if necessary."""
        async with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_reset
            
            # Reset counter if a minute has passed
            if elapsed >= 60:
                self.tokens_used = 0
                self.last_reset = current_time
                elapsed = 0
            
            # Calculate how many tokens we've recharged
            recharged_tokens = elapsed * self.tokens_per_second
            self.tokens_used = max(0, self.tokens_used - recharged_tokens)
            
            # If we'd exceed our limit, calculate wait time
            if (self.tokens_used + tokens) > self.tokens_per_minute:
                tokens_needed = (self.tokens_used + tokens) - self.tokens_per_minute
                wait_time = tokens_needed / self.tokens_per_second
                wait_time = min(wait_time, 60)
                
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Rate limiting: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                
                self.tokens_used = tokens
                self.last_reset = time.time()
            else:
                self.tokens_used += tokens

class OptimizedVectorCreator:
    """Creates vectors from processed article files with efficient skipping of already processed URLs."""
    
    def __init__(self, input_dir: str, output_dir: str, optimized_dir: str, batch_size: int = BATCH_SIZE, 
                 max_workers: int = 5, start_file: Optional[str] = None, test_mode: bool = False,
                 skip_processed: bool = True, concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
                 embedding_batch_size: int = EMBEDDING_BATCH_SIZE,
                 token_rate_limit: int = TOKEN_RATE_LIMIT,
                 rate_limit_delay: float = RATE_LIMIT_DELAY):
        """
        Initialize the vector creator.
        
        Args:
            input_dir: Directory containing processed articles
            output_dir: Directory to output vector files
            optimized_dir: Directory containing existing optimized vectors
            batch_size: Number of articles to process in each batch
            max_workers: Maximum number of worker processes
            start_file: Optional file to start processing from
            test_mode: Whether to run in test mode
            skip_processed: Whether to skip already processed articles
            concurrent_requests: Number of concurrent API requests
            embedding_batch_size: Number of chunks in each embedding batch
            token_rate_limit: Maximum tokens per minute to use
            rate_limit_delay: Minimum delay between API calls
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.optimized_dir = Path(optimized_dir)
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.test_mode = test_mode
        self.start_file = start_file
        self.skip_processed = skip_processed
        self.concurrent_requests = concurrent_requests
        self.embedding_batch_size = embedding_batch_size
        self.token_rate_limit = token_rate_limit
        self.rate_limit_delay = rate_limit_delay
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize OpenAI client
        self.client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        # Initialize ProcessStatusManager for URL tracking
        self.status_manager = ProcessStatusManager(PROCESSING_STATUS_FILE)
        logger.info(f"Loaded {len(self.status_manager.get_all_statuses())} URL statuses for deduplication")
        
        # Load existing URLs from optimized vectors
        self.existing_urls = self._load_existing_urls()
        logger.info(f"Loaded {len(self.existing_urls)} URLs from optimized vectors directory")
        
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
        
        # Set up rate limiter
        self.token_rate_limiter = TokenRateLimiter(token_rate_limit)
    
    def _load_existing_urls(self) -> Set[str]:
        """Load URLs from existing optimized vectors."""
        urls = set()
        try:
            parquet_files = list(self.optimized_dir.glob("*.parquet"))
            total_files = len(parquet_files)
            
            if total_files == 0:
                return urls
            
            for i, file in enumerate(parquet_files, 1):
                try:
                    # Read parquet file
                    df = pd.read_parquet(file)
                    
                    # Check if 'url' column exists
                    if 'url' not in df.columns:
                        # Try metadata.url if available
                        if 'metadata' in df.columns and isinstance(df['metadata'].iloc[0], dict):
                            urls.update(df['metadata'].apply(lambda x: x.get('url', '')).unique())
                        else:
                            logger.warning(f"No URL column found in {file}, skipping")
                        continue
                    
                    # Get unique URLs
                    batch_urls = set(df['url'].unique())
                    urls.update(batch_urls)
                    
                    # Only log every 50 files or at the end
                    if i % 50 == 0 or i == total_files:
                        logger.info(f"Loaded URLs from {i}/{total_files} parquet files, {len(urls)} total URLs")
                except Exception as e:
                    logger.warning(f"Error loading URLs from {file}: {str(e)}")
                    continue
            
            # Remove empty strings or None values
            urls = {url for url in urls if url}
            return urls
        except Exception as e:
            logger.error(f"Error loading existing URLs: {e}")
            return urls
    
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
            try:
                # Try to find by full path
                start_index = json_files.index(str(Path(self.start_file)))
                logger.info(f"Starting from file {self.start_file} (index {start_index})")
                json_files = json_files[start_index:]
            except ValueError:
                # Try to find by partial match
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
        # Clean up old timestamps
        while self.token_timestamps and now - self.token_timestamps[0] > TOKEN_WINDOW_SIZE:
            self.token_timestamps.pop(0)
            self.token_counts.pop(0)
        
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
        
        # If we're below 90% of the limit, don't wait at all
        if current_rate < (self.token_rate_limit * 0.9):
            return
            
        # If we're over 98% of the limit, wait longer but not as long as before
        if current_rate >= (self.token_rate_limit * 0.98):
            wait_time = 0.5  # Reduced from 1.0
            logger.debug(f"Rate limiting: current rate {current_rate:.0f} approaching maximum {self.token_rate_limit}, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
            return
            
        # We're between 90% and 98% of the limit, add a tiny delay
        await asyncio.sleep(0.1)  # Reduced from 0.2
    
    async def get_embeddings_with_retry(self, texts, max_retries=5):
        """
        Get embeddings with exponential backoff retry logic.
        """
        for attempt in range(1, max_retries + 1):
            try:
                # Add a minimal delay between requests to avoid bursts
                await asyncio.sleep(0.05)  # Reduced from self.rate_limit_delay
                
                response = await self.client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=texts,
                    encoding_format="float"
                )
                return response
            except Exception as e:
                error_msg = str(e)
                if "rate limit" in error_msg.lower() or "429" in error_msg:
                    # Calculate backoff time with less aggressive exponential increase
                    backoff = min(MAX_BACKOFF_TIME, BASE_BACKOFF_TIME * (1.5 ** (attempt - 1)))  # Changed from 2 ** to 1.5 **
                    logger.warning(f"Rate limit hit, retrying in {backoff:.1f}s (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(backoff)
                else:
                    if attempt == max_retries:
                        logger.error(f"Failed after {max_retries} attempts: {e}")
                        raise
                    logger.warning(f"Error getting embeddings (attempt {attempt}/{max_retries}): {e}")
                    # Add a shorter delay for non-rate-limit errors
                    await asyncio.sleep(0.5)  # Reduced from 1.0
        
        logger.error("All retries failed for embeddings batch")
        return None
    
    async def create_embedding_batches(self, chunks):
        """Process chunks in smaller batches to avoid rate limits."""
        all_embeddings = []
        batch_size = min(16, self.embedding_batch_size)
        
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            texts = [chunk["content"] for chunk in batch]
            
            total_tokens = sum(len(text.split()) for text in texts) * 1.3
            await self.token_rate_limiter.acquire(total_tokens)
            
            try:
                response = await self.get_embeddings_with_retry(texts)
                if response:
                    for j, embedding_data in enumerate(response.data):
                        if i + j < len(chunks):
                            chunks[i + j]["embedding"] = embedding_data.embedding
                            all_embeddings.append(chunks[i + j])
                            
                    # Only log in debug mode
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"Processed batch of {len(batch)} chunks")
            except Exception as e:
                logger.error(f"Error processing embedding batch: {e}")
                await asyncio.sleep(5)
            
            # Minimal delay between batches
            await asyncio.sleep(0.1)
        
        return all_embeddings
    
    def tokenize_text(self, text: str) -> List[int]:
        """
        Tokenize text using the same tokenizer as the OpenAI API.
        
        Args:
            text: Text to tokenize
            
        Returns:
            List of token IDs
        """
        return TOKENIZER.encode(text)
    
    def chunk_text(self, text: str, title: str, url: str) -> List[Dict[str, Any]]:
        """
        Split text into overlapping chunks with tokens.
        
        Args:
            text: Text to chunk
            title: Title of the article
            url: URL of the article
            
        Returns:
            List of chunk dictionaries with content and metadata
        """
        # Prepare the text with title for better context
        if title and not text.startswith(title):
            full_text = f"{title}\n\n{text}"
        else:
            full_text = text
            
        # Tokenize the text
        tokens = self.tokenize_text(full_text)
        
        # Split into chunks
        chunks = []
        for i in range(0, len(tokens), CHUNK_SIZE - OVERLAP):
            # Get chunk tokens
            chunk_tokens = tokens[i:i + CHUNK_SIZE]
            
            # Decode tokens back to text
            chunk_text = TOKENIZER.decode(chunk_tokens)
            
            # Create unique ID for this chunk
            chunk_id = f"{url}_{i // (CHUNK_SIZE - OVERLAP)}"
            
            # Create chunk dictionary
            chunk = {
                "id": chunk_id,
                "url": url,
                "title": title,
                "content": chunk_text,
                "content_tokens": len(chunk_tokens),
                "chunk_index": i // (CHUNK_SIZE - OVERLAP),
                "chunk_of": len(tokens) // (CHUNK_SIZE - OVERLAP) + 1
            }
            
            chunks.append(chunk)
        
        return chunks
    
    def _extract_url_from_file_path(self, file_path: str) -> str:
        """
        Extract a URL from a file path.
        
        Args:
            file_path: Path to the article file
            
        Returns:
            URL of the article
        """
        try:
            # Extract filename without extension
            filename = Path(file_path).stem
            
            # For wiki articles, construct URL from filename
            return f"https://starwars.fandom.com/wiki/{filename.replace(' ', '_')}"
        except Exception as e:
            logger.error(f"Error extracting URL from {file_path}: {e}")
            # Fall back to using a normalized version of the path
            return f"file:{file_path.replace(' ', '_')}"
    
    async def process_article(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Process a single article file.
        
        Args:
            file_path: Path to the article file
            
        Returns:
            List of vector records
        """
        try:
            # Load article data
            with open(file_path, 'r', encoding='utf-8') as f:
                article_data = json.load(f)
            
            # Extract article data
            title = article_data.get('title', '')
            content = article_data.get('content', '')
            url = article_data.get('url', self._extract_url_from_file_path(file_path))
            
            # Skip empty content
            if not content.strip():
                logger.warning(f"Empty content in {file_path}, skipping")
                return []
            
            # Skip articles already processed in optimized vectors
            if url in self.existing_urls:
                logger.debug(f"Article {url} already in optimized vectors, skipping")
                self.skipped_articles += 1
                return []
            
            # Skip if already vectorized
            status = self.status_manager.get_status(url)
            if self.skip_processed and status and status.vectorized and not status.error:
                logger.debug(f"Article {url} already vectorized, skipping")
                self.skipped_articles += 1
                return []
            
            # Chunk text for embeddings
            chunks = self.chunk_text(content, title, url)
            
            # No chunks to process
            if not chunks:
                logger.warning(f"No chunks created for {file_path}")
                return []
            
            # Process in batches of embedding_batch_size
            all_records = []
            for i in range(0, len(chunks), self.embedding_batch_size):
                # Get batch of chunks
                batch_chunks = chunks[i:i + self.embedding_batch_size]
                
                # Get batch of texts
                batch_texts = [chunk["content"] for chunk in batch_chunks]
                
                # Create embeddings
                embeddings = await self.create_embedding_batches(batch_chunks)
                
                # Create records
                batch_records = []
                for chunk, embedding in zip(batch_chunks, embeddings):
                    record = {
                        "id": chunk["id"],
                        "url": url,
                        "vector": embedding,
                        "metadata": {
                            "title": title,
                            "content": chunk["content"],
                            "content_tokens": chunk["content_tokens"],
                            "chunk_index": chunk["chunk_index"],
                            "chunk_of": chunk["chunk_of"]
                        }
                    }
                    batch_records.append(record)
                
                all_records.extend(batch_records)
            
            # Update processing status
            self.status_manager.mark_vectorized([url], success=True)
            
            # Update stats
            self.successful_articles += 1
            self.total_chunks += len(chunks)
            
            return all_records
        
        except Exception as e:
            logger.error(f"Error processing article {file_path}: {e}")
            traceback.print_exc()
            
            # Try to extract URL from file path to mark as error
            try:
                url = self._extract_url_from_file_path(file_path)
                self.status_manager.mark_vectorized([url], success=False)
            except:
                pass
            
            self.failed_articles += 1
            return []
    
    async def process_batch(self, batch_num: int, file_batch: List[str]) -> None:
        """Process a batch of articles."""
        if batch_num % args.progress_interval == 0:
            logger.info(f"Processing batch {batch_num} with {len(file_batch)} articles")
        
        tasks = [self.process_article(file_path) for file_path in file_batch]
        batch_results = await asyncio.gather(*tasks)
        
        # Flatten results
        vectors = [vector for result in batch_results for vector in result]
        
        if not vectors:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"No records generated for batch {batch_num}")
            return
        
        # Create dataframe and save to parquet
        df = pd.DataFrame(vectors)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_file = self.output_dir / f"vectors_{timestamp}_{batch_num}.parquet"
        
        try:
            df.to_parquet(output_file)
            if batch_num % args.progress_interval == 0:
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
        
        logger.info(f"Starting vector creation for {self.total_articles} articles")
        logger.info(f"Configuration: {self.concurrent_requests} concurrent requests, "
                   f"{self.embedding_batch_size} chunks/batch, "
                   f"{self.rate_limit_delay}s delay")
        
        file_batches = [json_files[i:i+self.batch_size] for i in range(0, len(json_files), self.batch_size)]
        
        for batch_num, file_batch in enumerate(file_batches, 1):
            if shutdown_requested:
                self._handle_shutdown(batch_num)
                return
            
            await self.process_batch(batch_num, file_batch)
            
            if file_batch:
                self.last_processed_file = file_batch[-1]
            
            # Only log progress at specified intervals
            if batch_num % args.progress_interval == 0:
                processed = min(batch_num * self.batch_size, self.total_articles)
                elapsed = time.time() - self.start_time
                articles_per_second = processed / elapsed if elapsed > 0 else 0
                
                logger.info(f"Progress: {processed}/{self.total_articles} articles "
                           f"({processed/self.total_articles:.1%}) - "
                           f"Speed: {articles_per_second:.2f} articles/sec")

def signal_handler(sig, frame):
    """Handle SIGINT and SIGTERM signals."""
    global shutdown_requested
    if not shutdown_requested:
        logger.info("Shutdown signal received, finishing current batch...")
        shutdown_requested = True
    else:
        logger.info("Second shutdown signal received, exiting immediately...")
        sys.exit(1)

async def main():
    """Main entry point."""
    try:
        creator = OptimizedVectorCreator(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            optimized_dir=args.optimized_dir,
            batch_size=args.batch_size,
            concurrent_requests=args.concurrent_requests,
            embedding_batch_size=args.embedding_batch_size,
            token_rate_limit=args.max_tokens_per_minute,
            rate_limit_delay=args.rate_limit_delay,
            start_file=args.start_file,
            test_mode=args.test_mode
        )
        await creator.run()
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Error in main: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the async main
    asyncio.run(main()) 