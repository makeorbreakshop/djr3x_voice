"""
Batch Processing System for Holocron Knowledge Base

This module implements a queue-based worker system for parallel processing of Wookieepedia articles,
with features for progress tracking, resumability, and polite crawling.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from asyncio import Queue, Task
from collections import deque

logger = logging.getLogger(__name__)

@dataclass
class BatchProgress:
    """Tracks progress of batch processing."""
    total_urls: int
    processed_urls: int
    failed_urls: List[str]
    current_batch: int
    start_time: datetime
    last_checkpoint: datetime
    checkpoint_file: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert progress to dictionary for serialization."""
        return {
            "total_urls": self.total_urls,
            "processed_urls": self.processed_urls,
            "failed_urls": self.failed_urls,
            "current_batch": self.current_batch,
            "start_time": self.start_time.isoformat(),
            "last_checkpoint": self.last_checkpoint.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], checkpoint_file: str) -> 'BatchProgress':
        """Create progress from dictionary."""
        return cls(
            total_urls=data["total_urls"],
            processed_urls=data["processed_urls"],
            failed_urls=data["failed_urls"],
            current_batch=data["current_batch"],
            start_time=datetime.fromisoformat(data["start_time"]),
            last_checkpoint=datetime.fromisoformat(data["last_checkpoint"]),
            checkpoint_file=checkpoint_file
        )

class RateLimiter:
    """Implements rate limiting for polite crawling."""
    
    def __init__(self, requests_per_minute: int = 30):
        self.delay = 60.0 / requests_per_minute
        self.last_request = datetime.now()
        self.request_times = deque(maxlen=requests_per_minute)
        self._lock = asyncio.Lock()  # Add lock for thread safety
    
    async def acquire(self):
        """Wait until it's safe to make another request."""
        async with self._lock:  # Ensure thread safety
            now = datetime.now()
            
            # Remove old requests from the window
            while self.request_times and (now - self.request_times[0]).total_seconds() > 60:
                self.request_times.popleft()
            
            # If we've made too many requests in the last minute, wait
            if len(self.request_times) >= self.request_times.maxlen:
                wait_time = 60 - (now - self.request_times[0]).total_seconds()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            
            # Ensure minimum delay between requests
            time_since_last = (now - self.last_request).total_seconds()
            if time_since_last < self.delay:
                await asyncio.sleep(self.delay - time_since_last)
            
            # Update state
            self.last_request = datetime.now()
            self.request_times.append(self.last_request)

class BatchProcessor:
    """Manages batch processing of URLs with parallel workers and progress tracking."""
    
    def __init__(
        self,
        urls: List[str],
        num_workers: int = 3,
        batch_size: int = 10,
        checkpoint_dir: str = "data/checkpoints",
        requests_per_minute: int = 30
    ):
        self.urls = urls
        self.num_workers = num_workers
        self.batch_size = batch_size
        self.checkpoint_dir = checkpoint_dir
        self.queue: Queue = Queue()
        self.workers: List[Task] = []
        self.rate_limiter = RateLimiter(requests_per_minute)
        
        # Create checkpoint directory
        os.makedirs(checkpoint_dir, exist_ok=True)
        
        # Initialize or load progress
        self.checkpoint_file = os.path.join(
            checkpoint_dir,
            f"batch_progress_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        self.progress = self._load_or_create_progress()
    
    def _load_or_create_progress(self) -> BatchProgress:
        """Load progress from checkpoint or create new progress tracker."""
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, 'r') as f:
                    data = json.load(f)
                return BatchProgress.from_dict(data, self.checkpoint_file)
            except Exception as e:
                logger.error(f"Failed to load checkpoint: {e}")
        
        return BatchProgress(
            total_urls=len(self.urls),
            processed_urls=0,  # Now using this as a counter
            failed_urls=[],
            current_batch=0,
            start_time=datetime.now(),
            last_checkpoint=datetime.now(),
            checkpoint_file=self.checkpoint_file
        )
    
    async def save_checkpoint(self):
        """Save current progress to checkpoint file."""
        self.progress.last_checkpoint = datetime.now()
        try:
            with open(self.progress.checkpoint_file, 'w') as f:
                json.dump(self.progress.to_dict(), f)
            logger.info(f"Checkpoint saved: {self.progress.checkpoint_file}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
    
    async def process_url(self, url: str) -> bool:
        """Process a single URL with rate limiting and error handling.
        
        This method should be overridden by the actual implementation.
        
        Args:
            url: The URL to process
            
        Returns:
            True if processing succeeded, False otherwise
        """
        try:
            # This is just a placeholder - the actual implementation
            # should override this method
            await asyncio.sleep(0.1)  # Simulate processing
            return True
        except Exception as e:
            logger.error(f"Failed to process URL {url}: {e}")
            self.progress.failed_urls.append(url)
            return False
    
    async def worker(self, worker_id: int):
        """Worker coroutine for processing URLs from the queue."""
        logger.info(f"Worker {worker_id} started")
        while True:
            try:
                url = await self.queue.get()
                if url is None:  # Poison pill
                    logger.info(f"Worker {worker_id} received shutdown signal")
                    break
                
                # Apply rate limiting before processing
                await self.rate_limiter.acquire()
                
                # Process the URL - note that we always mark it as processed whether
                # it succeeds in generating chunks or not, to avoid endless retries
                success = await self.process_url(url)
                
                # Increment the processed count regardless of success
                # This ensures URLs are always marked as done even if they don't
                # generate chunks (solving the "0 chunks from 1 articles" issue)
                self.progress.processed_urls += 1
                
                # Save checkpoint every batch_size URLs
                if self.progress.processed_urls % self.batch_size == 0:
                    await self.save_checkpoint()
                
                self.queue.task_done()
            except Exception as e:
                logger.error(f"Worker {worker_id} encountered error: {e}")
                if url:  # Only mark as failed if we had a URL
                    self.progress.failed_urls.append(url)
    
    async def run(self):
        """Run the batch processing operation."""
        try:
            # Skip already processed URLs
            urls_to_process = self.urls[self.progress.processed_urls:]
            
            # Start workers
            self.workers = [
                asyncio.create_task(self.worker(i))
                for i in range(self.num_workers)
            ]
            
            # Add URLs to queue
            for url in urls_to_process:
                await self.queue.put(url)
            
            # Add poison pills to stop workers
            for _ in range(self.num_workers):
                await self.queue.put(None)
            
            # Wait for all workers to complete
            await asyncio.gather(*self.workers)
            
            # Save final checkpoint
            await self.save_checkpoint()
            
            logger.info(f"Batch processing completed. "
                       f"Processed: {self.progress.processed_urls}/{self.progress.total_urls}, "
                       f"Failed: {len(self.progress.failed_urls)}")
            
            return True
        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            return False 