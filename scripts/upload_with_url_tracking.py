#!/usr/bin/env python3
"""
Pinecone Upload With Processing Status Tracking and Performance Metrics

This script uploads vectors to Pinecone while using the existing
processing_status.csv file to track which URLs have already been
processed, thus avoiding duplicates. It includes comprehensive 
performance tracking to monitor upload rates and identify rate limiting issues.

Usage:
    python scripts/upload_with_url_tracking.py [--batch-size N] [--test] [--delay SECONDS] [--vectors-dir DIR]

Performance Monitoring:
    - Tracks overall and per-batch upload rates
    - Monitors for rate limiting issues
    - Provides estimated completion times
    - Generates detailed performance reports
"""

import os
import sys
import json
import argparse
import logging
import time
import shutil
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Tuple
from pathlib import Path
import statistics

import pandas as pd
from tqdm import tqdm
from pinecone import Pinecone
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/pinecone_upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_VECTORS_DIR = "data/vectors"
PROCESSING_STATUS_FILE = "data/processing_status.csv"
MAX_RETRIES = 5
BASE_WAIT_TIME = 1  # Base wait time in seconds

# Performance metrics constants
BATCH_TIMING_WINDOW = 10  # Number of recent batches to calculate rolling average
PINECONE_RATE_LIMIT_QPS = 100  # Pinecone queries per second rate limit (for paid tier)
PINECONE_RATE_LIMIT_VECTORS = 100  # Approximate vectors per batch limit before hitting QPS

class PerformanceTracker:
    """Tracks performance metrics for vector uploads"""
    
    def __init__(self):
        self.start_time = time.time()
        self.total_vectors_processed = 0
        self.total_vectors_skipped = 0
        self.total_vectors_attempted = 0
        self.total_batches_processed = 0
        self.batch_times = []  # To calculate rolling average
        self.rate_limited_batches = 0
        self.error_batches = 0
        self.largest_batch_size = 0
        self.smallest_batch_size = float('inf')
        self.last_report_time = time.time()
        self.report_interval = 60  # seconds
    
    def record_batch(self, batch_size: int, duration: float, was_rate_limited: bool = False, 
                    had_error: bool = False, vectors_skipped: int = 0):
        """Record metrics for a processed batch"""
        self.total_batches_processed += 1
        self.total_vectors_processed += (batch_size - vectors_skipped) if not had_error else 0
        self.total_vectors_skipped += vectors_skipped
        self.total_vectors_attempted += batch_size
        self.batch_times.append((batch_size, duration))
        
        # Keep only recent batches for rolling average
        if len(self.batch_times) > BATCH_TIMING_WINDOW:
            self.batch_times.pop(0)
            
        if was_rate_limited:
            self.rate_limited_batches += 1
            
        if had_error:
            self.error_batches += 1
            
        self.largest_batch_size = max(self.largest_batch_size, batch_size)
        if batch_size > 0:  # Avoid counting empty batches
            self.smallest_batch_size = min(self.smallest_batch_size, batch_size) 
            
        # Calculate and log batch rate
        batch_rate = batch_size / duration if duration > 0 else 0
        if batch_size > 0:  # Only log non-empty batches
            logger.info(f"Batch rate: {batch_rate:.2f} vectors/second ({batch_size} vectors in {duration:.2f} seconds)")
            
            # Warn if approaching rate limits
            if batch_rate > PINECONE_RATE_LIMIT_QPS * 0.8:
                logger.warning(f"Approaching Pinecone rate limit! Current rate: {batch_rate:.2f}/{PINECONE_RATE_LIMIT_QPS} QPS")
            
        # Check if it's time to report metrics
        current_time = time.time()
        if current_time - self.last_report_time >= self.report_interval:
            self.report_metrics()
            self.last_report_time = current_time
            
    def record_file_skipped(self, skipped_count: int):
        """Record metrics for skipped vectors in a file"""
        self.total_vectors_skipped += skipped_count
        self.total_vectors_attempted += skipped_count
    
    def get_avg_vectors_per_second(self) -> float:
        """Calculate the average vectors uploaded per second"""
        elapsed = time.time() - self.start_time
        if elapsed > 0 and self.total_vectors_processed > 0:
            return self.total_vectors_processed / elapsed
        return 0
        
    def get_projected_completion_time(self, total_vectors: int) -> Tuple[float, timedelta]:
        """
        Estimate completion time based on current rate
        Returns: (percentage_complete, estimated_remaining_time)
        """
        vectors_remaining = total_vectors - self.total_vectors_processed
        current_rate = self.get_avg_vectors_per_second()
        
        if current_rate > 0:
            seconds_remaining = vectors_remaining / current_rate
            return (self.total_vectors_processed / total_vectors * 100, 
                   timedelta(seconds=int(seconds_remaining)))
        return (self.total_vectors_processed / total_vectors * 100 if total_vectors > 0 else 0, 
                timedelta(seconds=0))
                
    def get_rolling_batch_metrics(self) -> Dict[str, float]:
        """Calculate rolling average metrics for recent batches"""
        if not self.batch_times:
            return {
                "avg_batch_duration": 0,
                "avg_vectors_per_batch": 0,
                "avg_batch_rate": 0,
                "median_batch_duration": 0
            }
            
        durations = [duration for _, duration in self.batch_times]
        sizes = [size for size, _ in self.batch_times]
        
        avg_duration = sum(durations) / len(durations)
        avg_size = sum(sizes) / len(sizes)
        avg_rate = avg_size / avg_duration if avg_duration > 0 else 0
        
        return {
            "avg_batch_duration": avg_duration,
            "avg_vectors_per_batch": avg_size,
            "avg_batch_rate": avg_rate,
            "median_batch_duration": statistics.median(durations) if len(durations) > 0 else 0
        }
        
    def report_metrics(self):
        """Log current performance metrics"""
        elapsed = time.time() - self.start_time
        overall_rate = self.get_avg_vectors_per_second()
        rolling_metrics = self.get_rolling_batch_metrics()
        
        logger.info(f"===== PERFORMANCE METRICS =====")
        logger.info(f"Elapsed time: {timedelta(seconds=int(elapsed))}")
        logger.info(f"Vectors processed: {self.total_vectors_processed}")
        logger.info(f"Vectors skipped: {self.total_vectors_skipped}")
        logger.info(f"Overall rate: {overall_rate:.2f} vectors/second")
        logger.info(f"Batches processed: {self.total_batches_processed}")
        logger.info(f"Rate limited batches: {self.rate_limited_batches}")
        logger.info(f"Error batches: {self.error_batches}")
        logger.info(f"Recent batch metrics (last {min(BATCH_TIMING_WINDOW, len(self.batch_times))} batches):")
        logger.info(f"  - Avg batch size: {rolling_metrics['avg_vectors_per_batch']:.2f} vectors")
        logger.info(f"  - Avg batch duration: {rolling_metrics['avg_batch_duration']:.2f} seconds")
        logger.info(f"  - Avg batch rate: {rolling_metrics['avg_batch_rate']:.2f} vectors/second")
        logger.info(f"  - Median batch duration: {rolling_metrics['median_batch_duration']:.2f} seconds")
        logger.info(f"===============================")
        
    def final_report(self, total_vectors: int):
        """Generate final performance report"""
        elapsed = time.time() - self.start_time
        overall_rate = self.get_avg_vectors_per_second()
        rolling_metrics = self.get_rolling_batch_metrics()
        vectors_per_hour = overall_rate * 3600
        
        # Calculate efficiency metrics
        percent_skipped = (self.total_vectors_skipped / self.total_vectors_attempted * 100) if self.total_vectors_attempted > 0 else 0
        effective_rate = overall_rate / (1 - (percent_skipped/100)) if percent_skipped < 100 else 0
        
        logger.info(f"\n========== FINAL UPLOAD REPORT ==========")
        logger.info(f"Total runtime: {timedelta(seconds=int(elapsed))}")
        logger.info(f"Total vectors processed: {self.total_vectors_processed} / {total_vectors}")
        logger.info(f"Total vectors skipped: {self.total_vectors_skipped} ({percent_skipped:.1f}%)")
        logger.info(f"Total vectors attempted: {self.total_vectors_attempted}")
        logger.info(f"Average upload rate: {overall_rate:.2f} vectors/second ({vectors_per_hour:.0f} vectors/hour)")
        logger.info(f"Effective rate (excluding skipped): {effective_rate:.2f} vectors/second")
        logger.info(f"Total batches processed: {self.total_batches_processed}")
        logger.info(f"Rate limited batches: {self.rate_limited_batches}")
        logger.info(f"Error batches: {self.error_batches}")
        logger.info(f"Largest batch size: {self.largest_batch_size}")
        logger.info(f"Smallest batch size: {self.smallest_batch_size if self.smallest_batch_size != float('inf') else 0}")
        
        # Add recent performance data
        if len(self.batch_times) > 0:
            logger.info(f"\nRecent performance metrics:")
            logger.info(f"  - Recent batch rate: {rolling_metrics['avg_batch_rate']:.2f} vectors/second")
            logger.info(f"  - Recent batch duration: {rolling_metrics['avg_batch_duration']:.2f} seconds")
            logger.info(f"  - Recent batch size: {rolling_metrics['avg_vectors_per_batch']:.1f} vectors")
        
        if self.rate_limited_batches > 0:
            rate_limited_percentage = (self.rate_limited_batches / self.total_batches_processed) * 100
            logger.info(f"\nRate limited percentage: {rate_limited_percentage:.2f}%")
            
            if rate_limited_percentage > 20:
                logger.info("RECOMMENDATION: Consider reducing batch size to 50 or less and increasing delay between batches to 1-2 seconds")
            elif rate_limited_percentage > 10:
                logger.info("RECOMMENDATION: Consider reducing batch size or increasing delay between batches")
            elif rate_limited_percentage > 5:
                logger.info("RECOMMENDATION: Consider adding a small delay between batches (0.5-1 second)")
        
        # Add recommendations for future runs
        if overall_rate < 5:
            optimal_batch = min(PINECONE_RATE_LIMIT_VECTORS, int(self.largest_batch_size * 1.5)) if self.largest_batch_size > 0 else 100
            logger.info(f"\nRECOMMENDATION: Upload rate seems low. Consider increasing batch size to ~{optimal_batch}")
        elif overall_rate > 90:
            logger.info(f"\nNOTE: Approaching Pinecone rate limits ({overall_rate:.1f}/{PINECONE_RATE_LIMIT_QPS} QPS)")
                
        logger.info(f"==========================================\n")


class PineconeUploader:
    """Uploads vectors to Pinecone using processing status tracking to avoid duplicates."""
    
    def __init__(self, batch_size: int = 100, delay_between_batches: float = 0, vectors_dir: str = DEFAULT_VECTORS_DIR):
        """Initialize the uploader."""
        # Load environment variables
        load_dotenv()
        
        # Get Pinecone API key
        self.api_key = os.getenv("PINECONE_API_KEY")
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY not found in environment variables")
            
        # Get or set default Pinecone index name
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")
        
        # Initialize Pinecone client
        self.pc = Pinecone(api_key=self.api_key)
        
        # Set batch size
        self.batch_size = batch_size
        
        # Set delay between batches (seconds)
        self.delay_between_batches = delay_between_batches
        
        # Set vectors directory
        self.vectors_dir = vectors_dir
        
        # Initialize performance tracker
        self.performance_tracker = PerformanceTracker()
        
        # Load processed URLs
        self.processed_urls = self._load_processed_urls()
        logger.info(f"Loaded {len(self.processed_urls)} already processed URLs")
        
        # Log upload configuration
        logger.info(f"Upload configuration:")
        logger.info(f"  - Batch size: {batch_size} vectors")
        logger.info(f"  - Delay between batches: {delay_between_batches} seconds")
        logger.info(f"  - Vectors directory: {vectors_dir}")
        logger.info(f"  - Target Pinecone index: {self.index_name}")
        
    def _load_processed_urls(self) -> Set[str]:
        """Load the list of already processed URLs from CSV."""
        if os.path.exists(PROCESSING_STATUS_FILE):
            df = pd.read_csv(PROCESSING_STATUS_FILE)
            # Create a set of URLs that have is_processed = True
            processed_urls = set(df[df['is_processed'] == True]['url'].tolist())
            return processed_urls
        return set()
        
    def _update_processed_urls(self, new_urls: Set[str]) -> None:
        """Update the CSV with newly processed URLs."""
        if not new_urls:
            return
            
        if os.path.exists(PROCESSING_STATUS_FILE):
            df = pd.read_csv(PROCESSING_STATUS_FILE)
            # Update is_processed for the new URLs
            for url in new_urls:
                if url in df['url'].values:
                    df.loc[df['url'] == url, 'is_processed'] = True
                else:
                    # Add new URL if not already in the DataFrame
                    new_row = pd.DataFrame({'url': [url], 'is_processed': [True]})
                    df = pd.concat([df, new_row], ignore_index=True)
            df.to_csv(PROCESSING_STATUS_FILE, index=False)
        else:
            # If the file doesn't exist, create it with the new URLs
            df = pd.DataFrame({
                'url': list(new_urls),
                'is_processed': [True] * len(new_urls)
            })
            df.to_csv(PROCESSING_STATUS_FILE, index=False)
        
        # Update our in-memory set
        self.processed_urls.update(new_urls)
        
    def _extract_url_from_metadata(self, metadata: Dict) -> str:
        """Extract the URL from a vector's metadata."""
        # The URL is stored in the metadata under different possible keys
        url = metadata.get('url', '')
        if not url:
            url = metadata.get('source_url', '')
        if not url:
            # Look for URL in source field
            source = metadata.get('source', '')
            if isinstance(source, dict):
                url = source.get('url', '')
        
        # If still no URL, try to extract from content 
        if not url and 'content' in metadata:
            content = metadata['content']
            
            # Check for [Source] section in the content
            source_index = content.find('[Source]')
            if source_index != -1:
                # Get text after [Source] until next newline
                source_section = content[source_index:]
                url_start = source_section.find('http')
                if url_start != -1:
                    # Extract URL - find the end by looking for whitespace
                    source_section = source_section[url_start:]
                    url_end = min(x for x in [source_section.find(' '), source_section.find('\n')] if x != -1) if (source_section.find(' ') != -1 or source_section.find('\n') != -1) else len(source_section)
                    url = source_section[:url_end].strip()
            
            # If no URL found but content has a title, generate a potential Wookieepedia URL
            if not url and content.startswith('# '):
                title_line = content.split('\n')[0][2:].strip()  # Extract title without the '# '
                
                # Convert title to URL format for Wookieepedia with proper handling of special characters
                import urllib.parse
                # Handle parentheses and other special characters that are kept in Wookieepedia URLs
                title_url = title_line.replace(' ', '_').replace('(', '%28').replace(')', '%29')
                title_url = title_url.replace("'", '%27').replace('"', '%22')
                url = f"https://starwars.fandom.com/wiki/{title_url}"
                
        return url
        
    def upload_file(self, file_path: str, test_mode: bool = False) -> bool:
        """
        Upload vectors from a single Parquet file to Pinecone,
        skipping vectors from URLs that have already been processed.
        """
        try:
            # Start timing for file processing
            file_start_time = time.time()
            
            # Load the Parquet file
            df = pd.read_parquet(file_path)
            logger.info(f"Loaded {len(df)} vectors from {file_path}")
            
            # Get the index if not in test mode
            if not test_mode:
                index = self.pc.Index(self.index_name)
            
            # Prepare vectors for upload, filtering by URL
            vectors = []
            skipped_count = 0
            new_urls = set()  # Track new URLs found in this file
            
            for _, row in df.iterrows():
                # Parse metadata
                metadata = json.loads(row['metadata'])
                url = self._extract_url_from_metadata(metadata)
                
                # Skip if URL is already processed
                if url and url in self.processed_urls:
                    skipped_count += 1
                    continue
                    
                # Add to vectors for upload
                vector = {
                    'id': row['id'],
                    'values': row['values'],
                    'metadata': metadata
                }
                vectors.append(vector)
                
                # Track URL as processed
                if url:
                    new_urls.add(url)
            
            if skipped_count > 0:
                logger.info(f"Skipped {skipped_count} vectors from already processed URLs")
                # Record the skipped vectors in the performance tracker
                self.performance_tracker.record_file_skipped(skipped_count)
                
            if not vectors:
                logger.info(f"No new vectors to upload from {file_path}")
                file_duration = time.time() - file_start_time
                logger.info(f"File processing completed in {file_duration:.2f} seconds")
                return True
                
            # Split into batches
            batches = [vectors[i:i + self.batch_size] for i in range(0, len(vectors), self.batch_size)]
            
            if test_mode:
                # In test mode, just simulate uploads and update URL tracking
                for i, batch in enumerate(batches):
                    logger.info(f"TEST MODE: Would upload batch {i+1}/{len(batches)} ({len(batch)} vectors)")
                    # Still record batch metrics even in test mode
                    self.performance_tracker.record_batch(batch_size=len(batch), duration=0.1, vectors_skipped=0)
                self._update_processed_urls(new_urls)
                logger.info(f"TEST MODE: Added {len(new_urls)} new URLs to tracking file")
                file_duration = time.time() - file_start_time
                logger.info(f"File processing completed in {file_duration:.2f} seconds (TEST MODE)")
                return True
            
            # Upload batches with retry logic
            for i, batch in enumerate(batches):
                batch_start = time.time()
                logger.info(f"Uploading batch {i+1}/{len(batches)} ({len(batch)} vectors)")
                
                rate_limited = False
                had_error = False
                
                try:
                    self._upload_batch_with_retry(index, batch)
                    logger.info(f"Batch {i+1}/{len(batches)} completed successfully")
                except Exception as e:
                    had_error = True
                    if "rate limit" in str(e).lower():
                        rate_limited = True
                        logger.error(f"Batch {i+1}/{len(batches)} failed due to rate limiting after max retries")
                    else:
                        logger.error(f"Batch {i+1}/{len(batches)} failed with error: {e}")
                
                batch_duration = time.time() - batch_start
                
                # Record performance metrics
                self.performance_tracker.record_batch(
                    batch_size=len(batch),
                    duration=batch_duration,
                    was_rate_limited=rate_limited,
                    had_error=had_error,
                    vectors_skipped=0
                )
                
                logger.info(f"Batch {i+1}/{len(batches)} took {batch_duration:.2f} seconds")
                
                # Add delay between batches if specified (helps with rate limiting)
                if i < len(batches) - 1 and self.delay_between_batches > 0:
                    time.sleep(self.delay_between_batches)
            
            # Update URL tracking
            self._update_processed_urls(new_urls)
            logger.info(f"Added {len(new_urls)} new URLs to tracking file")
            
            file_duration = time.time() - file_start_time
            logger.info(f"File processing completed in {file_duration:.2f} seconds")
            
            # Project estimated completion time
            percent_complete, time_remaining = self.performance_tracker.get_projected_completion_time(len(df))
            logger.info(f"Current progress: {percent_complete:.1f}% complete")
            logger.info(f"Estimated time remaining: {time_remaining}")
            
            return True
                
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")
            return False
            
    def _upload_batch_with_retry(self, index, batch):
        """Upload a batch with exponential backoff retry logic."""
        retry_count = 0
        
        while retry_count < MAX_RETRIES:
            try:
                # Start timing the upsert operation
                upsert_start = time.time()
                
                # Perform the upsert
                index.upsert(vectors=batch)
                
                # Calculate and log the duration
                upsert_duration = time.time() - upsert_start
                batch_size = len(batch)
                
                # Calculate QPS (queries per second)
                batch_qps = batch_size / upsert_duration if upsert_duration > 0 else 0
                
                logger.info(f"Upsert operation took {upsert_duration:.2f} seconds for {batch_size} vectors")
                logger.info(f"Rate: {batch_qps:.2f} vectors/second for this batch ({batch_qps/PINECONE_RATE_LIMIT_QPS*100:.1f}% of QPS limit)")
                
                # Warn if we're approaching rate limits
                if batch_qps > PINECONE_RATE_LIMIT_QPS * 0.8:
                    logger.warning(f"Approaching Pinecone rate limit! Consider increasing delay or reducing batch size.")
                    
                    # Dynamically adjust delay if needed
                    if self.delay_between_batches < 0.5 and batch_qps > PINECONE_RATE_LIMIT_QPS * 0.9:
                        adjusted_delay = 0.5
                        logger.warning(f"Auto-adjusting delay to {adjusted_delay}s to avoid rate limiting")
                        self.delay_between_batches = adjusted_delay
                
                # If successful, return without error
                return
                
            except Exception as e:
                retry_count += 1
                # More aggressive backoff for rate limiting
                wait_time = BASE_WAIT_TIME * (2 ** retry_count) * (2 if "rate limit" in str(e).lower() else 1)
                
                if "rate limit" in str(e).lower():
                    logger.warning(f"Rate limit exceeded. Retrying in {wait_time}s (Attempt {retry_count}/{MAX_RETRIES})")
                    
                    # Auto-increase delay between batches when we hit rate limits
                    if self.delay_between_batches < 1.0:
                        adjusted_delay = max(1.0, self.delay_between_batches * 2)
                        logger.warning(f"Auto-increasing delay between batches to {adjusted_delay}s")
                        self.delay_between_batches = adjusted_delay
                else:
                    logger.warning(f"Error uploading batch: {e}. Retrying in {wait_time}s (Attempt {retry_count}/{MAX_RETRIES})")
                
                if retry_count < MAX_RETRIES:
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed to upload batch after {MAX_RETRIES} attempts")
                    raise
    
    def process_all_files(self, test_mode: bool = False, start_file: str = None, max_files: int = None) -> None:
        """
        Process all Parquet files in the vectors directory.
        
        Args:
            test_mode: If True, don't actually upload to Pinecone
            start_file: Optional filename to resume processing from
            max_files: Optional limit on number of files to process
        """
        # Start timing the entire process
        overall_start = time.time()
        
        # Get all Parquet files
        parquet_files = sorted(list(Path(self.vectors_dir).glob("*.parquet")))
        logger.info(f"Found {len(parquet_files)} Parquet files to process")
        
        # Filter to start from a specific file if requested
        if start_file:
            start_idx = next((i for i, f in enumerate(parquet_files) if f.name == start_file), None)
            if start_idx is not None:
                parquet_files = parquet_files[start_idx:]
                logger.info(f"Resuming from {start_file}, {len(parquet_files)} files remaining")
            else:
                logger.warning(f"Start file {start_file} not found, processing all files")
        
        # Limit number of files if requested
        if max_files is not None and max_files > 0:
            parquet_files = parquet_files[:max_files]
            logger.info(f"Limited to processing {len(parquet_files)} files")
        
        # Count total vectors for progress tracking
        total_vectors = 0
        if not test_mode:
            logger.info("Counting total vectors to process (for progress estimation)...")
            for file_path in parquet_files:
                try:
                    df = pd.read_parquet(file_path)
                    total_vectors += len(df)
                except Exception as e:
                    logger.warning(f"Could not count vectors in {file_path}: {e}")
            logger.info(f"Total vectors to process: {total_vectors}")
        
        # Process each file with progress bar
        for i, file_path in enumerate(tqdm(parquet_files, desc="Processing files")):
            logger.info(f"Processing file {i+1}/{len(parquet_files)}: {file_path}")
            success = self.upload_file(file_path, test_mode)
            
            # Wait between files to avoid rate limiting
            if success and i < len(parquet_files) - 1:
                time.sleep(2)  # Short pause between files
        
        # Generate performance report
        overall_duration = time.time() - overall_start
        logger.info(f"Completed processing {len(parquet_files)} files in {timedelta(seconds=int(overall_duration))}")
        
        # Generate final performance report
        self.performance_tracker.final_report(total_vectors)
        
        # Add recommendations based on performance
        if self.performance_tracker.rate_limited_batches > 0:
            rate_limited_pct = (self.performance_tracker.rate_limited_batches / self.performance_tracker.total_batches_processed) * 100
            if rate_limited_pct > 20:
                logger.info("RECOMMENDATION: Consider smaller batch sizes and larger delays between batches to reduce rate limiting")
            elif rate_limited_pct > 5:
                logger.info("RECOMMENDATION: Consider adding a small delay between batches to reduce rate limiting")

def main():
    """Main function to run the uploader."""
    parser = argparse.ArgumentParser(description="Upload vectors to Pinecone using processing status tracking")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for uploads")
    parser.add_argument("--delay", type=float, default=0, help="Delay in seconds between batch uploads (helps with rate limiting)")
    parser.add_argument("--test", action="store_true", help="Run in test mode without actual uploads")
    parser.add_argument("--start-file", type=str, help="Resume processing from this file")
    parser.add_argument("--max-files", type=int, help="Maximum number of files to process")
    parser.add_argument("--list-files", action="store_true", help="List all available vector files and exit")
    parser.add_argument("--vectors-dir", type=str, default=DEFAULT_VECTORS_DIR, help="Directory containing vector files")
    parser.add_argument("--reset-tracking", action="store_true", help="Reset URL tracking status (backup existing state)")
    args = parser.parse_args()
    
    # List files if requested
    if args.list_files:
        vector_files = sorted(list(Path(args.vectors_dir).glob("*.parquet")))
        print(f"Found {len(vector_files)} vector files:")
        for i, file in enumerate(vector_files):
            print(f"{i+1}. {file.name}")
        return
    
    # Reset URL tracking if requested
    if args.reset_tracking:
        if os.path.exists(PROCESSING_STATUS_FILE):
            # Create backup with timestamp
            backup_file = f"{PROCESSING_STATUS_FILE}.bak.{int(time.time())}"
            shutil.copy2(PROCESSING_STATUS_FILE, backup_file)
            logger.info(f"Created backup of URL tracking file: {backup_file}")
            
            # Create empty tracking file
            pd.DataFrame(columns=['url', 'is_processed']).to_csv(PROCESSING_STATUS_FILE, index=False)
            logger.info(f"Reset URL tracking file: {PROCESSING_STATUS_FILE}")
        else:
            logger.info(f"No URL tracking file found to reset")
        
        if not args.test and not args.start_file and not args.list_files:
            logger.info("URL tracking reset complete. Run the script again without --reset-tracking to start processing.")
            return
    
    try:
        uploader = PineconeUploader(batch_size=args.batch_size, delay_between_batches=args.delay, vectors_dir=args.vectors_dir)
        uploader.process_all_files(test_mode=args.test, start_file=args.start_file, max_files=args.max_files)
    except Exception as e:
        logger.error(f"Error during upload process: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 