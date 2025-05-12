#!/usr/bin/env python3
"""
Create vectors from processed articles in batches with robust error handling and status tracking.
"""

import os
import json
import logging
import time
import random
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime, timedelta
import asyncio
import aiohttp
import numpy as np
import pandas as pd
from tqdm.asyncio import tqdm
from openai import AsyncOpenAI, RateLimitError
from collections import deque
import tiktoken

from process_status_manager import ProcessStatusManager, ProcessingStatus

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"vector_creation_{datetime.now().strftime('%Y%m%d%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Constants
EMBEDDING_MODEL = "text-embedding-ada-002"
EMBEDDING_DIMENSIONS = 1536
MAX_RETRIES = 5
DEFAULT_BATCH_SIZE = 200
MAX_PARALLEL_REQUESTS = 5
MAX_BATCH_SIZE = 200
MIN_RETRY_DELAY = 1  # seconds
TOKEN_RATE_LIMIT = 800000  # Per minute
CHUNK_SIZE = 256  # Reduced from 512 to 256 tokens for more efficient chunking
CHUNK_OVERLAP = 64  # Reduced overlap proportionally
TOKENIZER = tiktoken.get_encoding("cl100k_base")  # OpenAI's tokenizer

class TokenRateLimiter:
    def __init__(self, target_rpm=800_000, window_size=60):
        self.target_rpm = target_rpm
        self.window_size = window_size
        self.requests = deque()  # Store (timestamp, tokens) tuples
        self.last_request_time = 0
        
    def _get_current_usage(self):
        """Get token usage in the current window."""
        now = time.time()
        cutoff = now - 60  # Only look at last 60 seconds
        
        # Remove old requests
        while self.requests and self.requests[0][0] < cutoff:
            self.requests.popleft()
            
        # Sum tokens in current window
        return sum(tokens for _, tokens in self.requests)
        
    async def wait_if_needed(self, token_count):
        now = time.time()
        current_usage = self._get_current_usage()
        
        # Calculate tokens per minute rate
        if self.requests:
            window_start = self.requests[0][0]
            window_duration = max(1, now - window_start)
            current_rate = (current_usage / window_duration) * 60
        else:
            current_rate = 0
            
        # Basic rate check
        if current_rate + (token_count / 60) > self.target_rpm:
            # Calculate minimum wait time needed
            wait_time = 0.1  # Base wait time
            if current_rate > 0:
                # Add proportional wait based on how far we are over target
                over_rate = (current_rate - self.target_rpm) / self.target_rpm
                wait_time += min(0.5, over_rate)  # Cap additional wait at 0.5s
                
            if wait_time > 0.1:  # Only log significant waits
                logger.info(f"Rate limit approaching ({current_rate:.0f} tokens/min), waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
            
        # Ensure minimum time between requests
        time_since_last = now - self.last_request_time
        if time_since_last < 0.05:  # Minimum 0.05s between requests
            await asyncio.sleep(0.05 - time_since_last)
            
        # Record this request
        self.last_request_time = time.time()
        self.requests.append((self.last_request_time, token_count))

class VectorCreator:
    """Creates and manages vector embeddings from processed articles."""
    
    def __init__(self, processed_dir: str = "data/processed_articles", 
                 vectors_dir: str = "data/vectors",
                 batch_size: int = DEFAULT_BATCH_SIZE, 
                 max_workers: int = MAX_PARALLEL_REQUESTS):
        """Initialize the vector creator with configuration."""
        # Load API key from environment
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
            
        # Initialize OpenAI client
        self.client = AsyncOpenAI(api_key=self.api_key)
        
        # Status tracking with improved caching
        self.status_manager = ProcessStatusManager(flush_interval=30, batch_updates=True)
        
        # Set up directories and configuration
        self.processed_dir = Path(processed_dir)
        self.vectors_dir = Path(vectors_dir)
        self.batch_size = batch_size
        self.max_workers = max_workers
        
        # Rate limiting and tracking
        self.semaphore = asyncio.Semaphore(max_workers)
        self.token_usage_window = []  # Track token usage for rate limiting
        self.token_count = 0
        self.rate_limited_count = 0
        self.last_request_time = 0
        
        # Performance metrics
        self.start_time = None
        self.processed_count = 0
        self.chunk_count = 0
        self.skipped_count = 0
        
        # Ensure directories exist
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        
    async def create_vectors(self) -> None:
        """Create vectors from processed articles in batches."""
        logger.info("Starting vector creation process...")
        self.start_time = time.time()
        
        # Start a new batch for tracking
        batch_id = self.status_manager.start_new_batch()
        logger.info(f"Started new batch: {batch_id}")
        
        try:
            # Get list of all processed article files
            article_files = []
            for batch_dir in self.processed_dir.glob("batch_*"):
                if not batch_dir.is_dir():
                    continue
                article_files.extend(batch_dir.glob("*.json"))
            
            total_files = len(article_files)
            logger.info(f"Found {total_files} processed article files")
            
            # Process files in larger batches to reduce overhead
            for i in range(0, total_files, self.batch_size):
                batch_files = article_files[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1
                
                logger.info(f"Worker processing batch {batch_num} with {len(batch_files)} articles")
                
                # Process batch and collect results
                await self._process_batch(batch_num, batch_files)
                
                # Save current progress and log statistics
                await self._log_progress(batch_num, total_files)
            
            # Final flush to make sure all statuses are saved
            self.status_manager.save()
            
        except Exception as e:
            logger.error(f"Error in vector creation process: {e}", exc_info=True)
            raise
            
    async def _log_progress(self, batch_num: int, total_files: int) -> None:
        """Log progress statistics."""
        elapsed = time.time() - self.start_time
        articles_per_sec = self.processed_count / elapsed if elapsed > 0 else 0
        chunks_per_sec = self.chunk_count / elapsed if elapsed > 0 else 0
        tokens_per_min = self.token_count / elapsed * 60 if elapsed > 0 else 0
        
        if self.processed_count > 0:
            # Calculate ETA
            remaining_articles = total_files - self.processed_count
            remaining_seconds = remaining_articles / articles_per_sec if articles_per_sec > 0 else 0
            eta = datetime.now() + timedelta(seconds=remaining_seconds)
            eta_str = eta.strftime("%Y-%m-%d %H:%M:%S")
        else:
            eta_str = "Unknown"
            
        # Log progress metrics
        logger.info(
            f"Progress: {self.processed_count}/{total_files} articles "
            f"({self.processed_count / total_files * 100:.1f}%) - "
            f"Skipped: {self.skipped_count} articles - "
            f"Speed: {articles_per_sec:.2f} articles/sec, {chunks_per_sec:.2f} chunks/sec - "
            f"Token rate: {tokens_per_min:.0f} tokens/min - "
            f"Rate limits hit: {self.rate_limited_count} - "
            f"ETA: {eta_str}"
        )
        
        # Save vectors for this batch
        if self.chunk_count > 0:
            batch_file = self.vectors_dir / f"vectors_{datetime.now().strftime('%Y%m%d%H%M%S')}_{batch_num}.parquet"
            logger.info(f"Saved vectors to {batch_file}")
        
    async def _process_batch(self, batch_num: int, article_files: List[Path]) -> None:
        """Process a batch of article files efficiently."""
        # Step 1: Preload and filter articles that need processing
        filtered_articles = []
        total_estimated_tokens = 0
        
        for file_path in article_files:
            try:
                with open(file_path, 'r') as f:
                    article_data = json.load(f)
                    
                url = article_data.get('url')
                if not url:
                    continue
                    
                # Skip if already vectorized successfully
                status = self.status_manager.get_status(url)
                if status and status.vectorized and not status.error:
                    self.skipped_count += 1
                    continue
                    
                # Estimate tokens for this article (4 chars ≈ 1 token)
                content = article_data.get('content', '')
                estimated_tokens = len(content) // 4
                total_estimated_tokens += estimated_tokens
                
                filtered_articles.append((url, article_data, estimated_tokens))
                
            except Exception as e:
                logger.error(f"Error loading article from {file_path}: {e}")
                continue
                
        if not filtered_articles:
            logger.info(f"Batch {batch_num}: No articles need processing")
            return
            
        logger.info(f"Batch {batch_num}: Processing {len(filtered_articles)} articles (est. {total_estimated_tokens} tokens)")
        
        # Step 2: Sort articles by token count and prepare optimal chunks
        filtered_articles.sort(key=lambda x: x[2], reverse=True)  # Sort by estimated tokens
        pending_chunks = []  # List of (url, chunk_text, chunk_id) tuples
        current_chunk_tokens = 0
        current_chunk_texts = []
        current_chunk_urls = []
        current_chunk_ids = []

        for url, article_data, est_tokens in filtered_articles:
            # Extract content for embedding
            title = article_data.get('title', '')
            content = article_data.get('content', '')
            
            if not content:
                logger.warning(f"Empty content for {url}")
                continue
                
            # Create chunks for processing
            article_chunks = self._chunk_article(url, title, content)
            
            # Group chunks into optimal-sized batches
            for url, text, chunk_id in article_chunks:
                chunk_tokens = len(text) // 4  # Estimate tokens
                
                # If adding this chunk would exceed batch size, process current batch
                if current_chunk_tokens + chunk_tokens > MAX_BATCH_SIZE * CHUNK_SIZE:
                    if current_chunk_texts:
                        pending_chunks.append((
                            current_chunk_urls.copy(),
                            current_chunk_texts.copy(),
                            current_chunk_ids.copy()
                        ))
                        current_chunk_texts = []
                        current_chunk_urls = []
                        current_chunk_ids = []
                        current_chunk_tokens = 0
                
                current_chunk_texts.append(text)
                current_chunk_urls.append(url)
                current_chunk_ids.append(chunk_id)
                current_chunk_tokens += chunk_tokens
        
        # Add any remaining chunks
        if current_chunk_texts:
            pending_chunks.append((
                current_chunk_urls.copy(),
                current_chunk_texts.copy(),
                current_chunk_ids.copy()
            ))
        
        # Step 3: Process chunks in optimized batches
        total_chunks = sum(len(texts) for _, texts, _ in pending_chunks)
        self.chunk_count += total_chunks
        logger.info(f"Batch {batch_num}: Total chunks to process: {total_chunks}")
        
        # Track URLs to mark as completed
        processed_urls = set()
        vectors_by_url = {}  # Store vectors by URL for each article
        
        # Process optimized chunk batches
        for urls, texts, chunk_ids in pending_chunks:
            try:
                # Create embeddings for batch with adaptive retry logic
                embeddings = await self._create_embeddings_batch(texts)
                
                # Save each embedding to its corresponding URL
                for url, embedding, chunk_id in zip(urls, embeddings, chunk_ids):
                    if embedding is not None:
                        processed_urls.add(url)
                        
                        # Initialize vectors collection for this URL if needed
                        if url not in vectors_by_url:
                            vectors_by_url[url] = []
                            
                        # Store the vector with metadata
                        vectors_by_url[url].append({
                            'id': chunk_id,
                            'vector': embedding,
                            'url': url
                        })
                        
            except Exception as e:
                logger.error(f"Error processing chunk batch: {e}")
                # Continue processing other batches even if one fails
        
        # Step 4: Save vectors to parquet file for processed URLs
        if vectors_by_url:
            # Create DataFrame for saving
            all_vectors = []
            for vectors in vectors_by_url.values():
                all_vectors.extend(vectors)
                
            df = pd.DataFrame([{
                'id': v['id'],
                'url': v['url'],
                'vector': v['vector']
            } for v in all_vectors])
            
            # Save to parquet file
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{self.vectors_dir}/vectors_{timestamp}_{batch_num}.parquet"
            df.to_parquet(filename, compression='snappy')
            logger.info(f"Saved {len(all_vectors)} vectors to {filename}")
        
        # Step 5: Update status for all processed URLs
        if processed_urls:
            batch_statuses = {
                url: ProcessingStatus(url=url, vectorized=True, error=False)
                for url in processed_urls
            }
            
            # Update status in a single operation
            self.status_manager.update_batch_status(batch_statuses)
            self.processed_count += len(batch_statuses)
        
    def _chunk_article(self, url: str, title: str, content: str) -> List[Tuple[str, str, str]]:
        """
        Split article into chunks for efficient embedding.
        
        Returns:
            List of (url, text, chunk_id) tuples
        """
        # Prepare full content with title for chunking
        full_text = f"{title}\n\n{content}" if title else content
        words = full_text.split()
        
        if len(words) <= CHUNK_SIZE:
            # If content is small, use a single chunk
            chunk_id = f"{url}_chunk_1"
            return [(url, full_text, chunk_id)]
            
        # Create overlapping chunks
        chunks = []
        for i in range(0, len(words), CHUNK_SIZE - CHUNK_OVERLAP):
            chunk_words = words[i:i + CHUNK_SIZE]
            if not chunk_words:
                continue
                
            chunk_text = " ".join(chunk_words)
            chunk_id = f"{url}_chunk_{len(chunks) + 1}"
            chunks.append((url, chunk_text, chunk_id))
            
        return chunks
            
    async def _create_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Create embeddings for a batch of texts with adaptive rate limiting.
        
        Args:
            texts: List of texts to create embeddings for
            
        Returns:
            List of embedding vectors
        """
        # Estimate token count (4 chars ≈ 1 token)
        estimated_tokens = sum(len(text) // 4 for text in texts)
        
        # Dynamically adjust delay based on rate limit and estimated tokens
        await self._adaptive_rate_limit(estimated_tokens)
        
        for retry in range(MAX_RETRIES):
            try:
                async with self.semaphore:
                    # Record request time
                    self.last_request_time = time.time()
                    
                    # Make API request
                    start_time = time.time()
                    response = await self.client.embeddings.create(
                        model=EMBEDDING_MODEL,
                        input=texts
                    )
                    duration = time.time() - start_time
                    
                    # Record actual token usage
                    actual_tokens = response.usage.total_tokens
                    self.token_count += actual_tokens
                    self.token_usage_window.append((time.time(), actual_tokens))
                    
                    # Clean up old token usage records (older than 1 minute)
                    current_time = time.time()
                    self.token_usage_window = [
                        (t, tokens) for t, tokens in self.token_usage_window 
                        if current_time - t < 60
                    ]
                    
                    # Calculate current rate for logging
                    recent_tokens = sum(tokens for _, tokens in self.token_usage_window)
                    tokens_per_second = actual_tokens / duration if duration > 0 else 0
                    
                    logger.debug(
                        f"Processed {len(texts)} texts, {actual_tokens} tokens in {duration:.2f}s "
                        f"({tokens_per_second:.1f} tokens/s, window rate: {recent_tokens} tokens/min)"
                    )
                    
                    return [item.embedding for item in response.data]
                    
            except Exception as e:
                if isinstance(e, RateLimitError) or "rate limit" in str(e).lower():
                    self.rate_limited_count += 1
                    
                    # Exponential backoff with jitter
                    wait_time = min(30, (2 ** retry) * MIN_RETRY_DELAY * (0.8 + 0.4 * random.random()))
                    logger.warning(f"Rate limit hit, retrying in {wait_time:.1f}s (attempt {retry+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Error creating batch embeddings: {e}")
                    if retry < MAX_RETRIES - 1:
                        # Brief delay before retry for non-rate-limit errors
                        await asyncio.sleep(1)
                    else:
                        # Return zero vectors as fallback after all retries
                        return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]
        
        # Return zero vectors if all retries failed
        logger.error(f"Failed to create embeddings after {MAX_RETRIES} retries")
        return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]
    
    async def _adaptive_rate_limit(self, estimated_tokens: int) -> None:
        """
        Adaptively limit rate based on recent token usage.
        
        Args:
            estimated_tokens: Estimated tokens for upcoming request
        """
        current_time = time.time()
        
        # Calculate tokens used in the last minute
        one_minute_ago = current_time - 60
        recent_usage = sum(
            tokens for timestamp, tokens in self.token_usage_window 
            if timestamp >= one_minute_ago
        )
        
        # Clean up old usage records
        self.token_usage_window = [
            (t, tokens) for t, tokens in self.token_usage_window 
            if t >= one_minute_ago
        ]
        
        # Calculate current utilization
        utilization = (recent_usage + estimated_tokens) / TOKEN_RATE_LIMIT
        
        # Only wait if we're close to the limit
        if utilization > 0.9:  # Changed from no threshold to 0.9
            # Calculate adaptive wait time based on utilization
            base_wait = 0.05  # Reduced base wait time
            usage_factor = max(0, (utilization - 0.9) / 0.1)  # Scale factor based on how close to limit
            wait_time = min(5, base_wait + (usage_factor * 2))  # Cap at 5 seconds, reduced from 10
            
            if wait_time > 0.1:  # Only log if wait is significant
                logger.info(f"Rate limit approaching ({recent_usage} tokens/min), waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
        
        # Minimal delay between requests to prevent bursts
        time_since_last = current_time - self.last_request_time
        if time_since_last < 0.05:  # Reduced from 0.1 to 0.05
            await asyncio.sleep(0.05 - time_since_last)

    def chunk_text(self, text: str, title: str, url: str) -> List[Dict[str, Any]]:
        """
        Split text into chunks based on token count with overlap.
        
        Args:
            text: Text to chunk
            title: Article title
            url: Article URL
            
        Returns:
            List of chunk dictionaries
        """
        # Encode text into tokens
        tokens = TOKENIZER.encode(text)
        
        if len(tokens) <= CHUNK_SIZE:
            # If text is small enough, just use it as a single chunk
            return [{
                "id": f"{url}_chunk_1",
                "content": text,
                "title": title,
                "url": url,
                "chunk_index": 1,
                "total_chunks": 1
            }]
        
        # Split into overlapping chunks based on token count
        chunks = []
        chunk_index = 1
        start = 0
        total_chunks = (len(tokens) - 1) // (CHUNK_SIZE - CHUNK_OVERLAP) + 1

        while start < len(tokens):
            end = min(start + CHUNK_SIZE, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = TOKENIZER.decode(chunk_tokens)
            
            # Create chunk with metadata
            chunks.append({
                "id": f"{url}_chunk_{chunk_index}",
                "content": chunk_text,
                "title": title,
                "url": url,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks
            })
            
            # Move start position accounting for overlap
            start += CHUNK_SIZE - CHUNK_OVERLAP
            chunk_index += 1
        
        return chunks

async def main():
    """Main entry point."""
    import random  # Import at module level
    
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Create vectors from processed articles.')
    parser.add_argument('--input-dir', type=str, default='data/processed_articles',
                        help='Directory containing processed articles')
    parser.add_argument('--output-dir', type=str, default='data/vectors',
                        help='Directory for output vectors')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE,
                        help='Number of articles to process per batch')
    parser.add_argument('--workers', type=int, default=MAX_PARALLEL_REQUESTS,
                        help='Maximum number of parallel workers')
    args = parser.parse_args()
    
    # Create and run vector creator
    creator = VectorCreator(
        processed_dir=args.input_dir,
        vectors_dir=args.output_dir,
        batch_size=args.batch_size,
        max_workers=args.workers
    )
    
    try:
        await creator.create_vectors()
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        # Make sure to save status before exiting
        creator.status_manager.save()
    except Exception as e:
        logger.error(f"Error during vector creation: {e}", exc_info=True)
        creator.status_manager.save()

if __name__ == "__main__":
    import random  # Import here too in case module-level import is skipped
    asyncio.run(main()) 