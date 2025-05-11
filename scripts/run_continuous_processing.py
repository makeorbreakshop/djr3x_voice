#!/usr/bin/env python3
"""
Continuous Processing System for Holocron Knowledge Base

This script automates the entire pipeline for processing URLs into the Holocron knowledge base:
- Continuously processes URLs until all are complete
- Smart batching with configurable batch sizes
- Automatic prioritization (processes high priority first, then medium, then low)
- Built-in delays between batches to respect rate limits
- Error handling with automatic retries and longer delays after failures
- Progress tracking with detailed logging and statistics

Usage:
    python scripts/run_continuous_processing.py [--batch-size N] [--delay N] [--workers N] 
    [--prioritize] [--limit N] [--reset-failed]
"""

import os
import sys
import time
import json
import argparse
import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from supabase import create_client

from config.app_settings import (
    SUPABASE_URL,
    SUPABASE_KEY
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/continuous_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# ANSI color codes for terminal output
COLORS = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
    "RED": "\033[31m",
    "BG_GREEN": "\033[42m",
    "BG_YELLOW": "\033[43m",
    "BG_RED": "\033[41m",
}

class URLProcessor:
    """Manages continuous processing of URLs with batching and error handling."""
    
    def __init__(
        self,
        batch_size: int = 150,
        delay: int = 5,
        max_retries: int = 3,
        workers: int = 10,
        requests_per_minute: int = 60,
        prioritize: bool = True,
        limit: Optional[int] = None,
        reset_failed: bool = False
    ):
        self.batch_size = batch_size
        self.delay = delay
        self.max_retries = max_retries
        self.workers = workers
        self.requests_per_minute = requests_per_minute
        self.prioritize = prioritize
        self.limit = limit
        self.reset_failed = reset_failed
        
        # Initialize Supabase client
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Processing statistics
        self.start_time = datetime.now()
        self.processed_urls = 0
        self.failed_batches = 0
        self.successful_batches = 0
        self.batch_times = []
        
    async def get_db_status(self) -> Tuple[int, int]:
        """
        Get the current status of URL processing from the database.
        
        Returns:
            Tuple of (processed_urls, total_urls)
        """
        try:
            # Get total count
            total_result = await asyncio.to_thread(
                lambda: self.supabase.table("holocron_urls").select("id", count="exact").execute()
            )
            total_urls = total_result.count if hasattr(total_result, 'count') else 0
            
            # Get processed count
            processed_result = await asyncio.to_thread(
                lambda: self.supabase.table("holocron_urls")
                .select("id", count="exact")
                .eq("is_processed", True)
                .execute()
            )
            processed_urls = processed_result.count if hasattr(processed_result, 'count') else 0
            
            return processed_urls, total_urls
        except Exception as e:
            logger.error(f"Error getting database status: {e}")
            return 0, 0
            
    async def reset_failed_urls(self) -> int:
        """
        Reset URLs that were processed but have no chunks.
        
        Returns:
            Number of URLs reset
        """
        if not self.reset_failed:
            return 0
            
        logger.info("Resetting URLs that were processed but have no chunks")
        
        try:
            # Run the rectify_processed_urls.py script
            result = subprocess.run(
                ["python", "scripts/rectify_processed_urls.py"],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Parse output to get number of reset URLs
            output = result.stdout + result.stderr
            reset_count = 0
            
            for line in output.splitlines():
                if "Reset " in line and " URLs for reprocessing" in line:
                    try:
                        reset_count = int(line.split("Reset ")[1].split(" URLs")[0])
                    except ValueError:
                        pass
            
            logger.info(f"Reset {reset_count} URLs for reprocessing")
            return reset_count
        except subprocess.CalledProcessError as e:
            logger.error(f"Error running URL rectification script: {e}")
            logger.error(f"Output: {e.stdout}")
            logger.error(f"Error: {e.stderr}")
            return 0
        except Exception as e:
            logger.error(f"Error resetting failed URLs: {e}")
            return 0
            
    async def run_batch(self, batch_num: int, priority: Optional[str] = None) -> bool:
        """
        Run a single batch of URL processing.
        
        Args:
            batch_num: Batch number for logging
            priority: Optional priority level to process (high, medium, low)
            
        Returns:
            True if the batch was successful, False otherwise
        """
        cmd = [
            "python", "scripts/run_holocron_pipeline_v3.py",
            "--limit", str(self.batch_size),
            "--workers", str(self.workers),
            "--batch-size", str(10),  # Checkpoint frequency
            "--requests-per-minute", str(self.requests_per_minute)
        ]
        
        if priority:
            cmd.extend(["--priority", priority])
        
        if getattr(self, 'debug', False):
            cmd.append("--debug")
        
        # Print color-coded status
        priority_str = f"{priority} priority " if priority else ""
        print(f"\n{COLORS['BOLD']}{COLORS['BG_YELLOW']} Batch #{batch_num} {COLORS['RESET']} - Starting batch of {self.batch_size} URLs...")
        print(f"Priority mode: {COLORS['CYAN']}Processing {priority_str}URLs{COLORS['RESET']}")
        print(f"Command: {' '.join(cmd)}")
        print(f"Processing batch... ", end="", flush=True)
        
        # Log command
        logger.info(f"Starting batch #{batch_num} with {self.batch_size} URLs")
        if priority:
            logger.info(f"Processing {priority} priority URLs")
        logger.info(f"Running command: {' '.join(cmd)}")
        
        batch_start = datetime.now()
        
        try:
            # Run the command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            # Check for success in output
            output = result.stdout + result.stderr
            
            # Count URLs processed in this batch
            urls_processed = 0
            for line in output.splitlines():
                if "Total URLs processed in this run:" in line:
                    try:
                        urls_processed = int(line.split("Total URLs processed in this run:")[1].strip())
                    except ValueError:
                        pass
            
            batch_end = datetime.now()
            batch_duration = (batch_end - batch_start).total_seconds() / 60.0
            urls_per_minute = urls_processed / batch_duration if batch_duration > 0 else 0
            
            # Update statistics
            self.processed_urls += urls_processed
            self.successful_batches += 1
            self.batch_times.append(batch_duration)
            
            # Calculate rolling average processing rate
            avg_batch_time = sum(self.batch_times[-5:]) / min(len(self.batch_times), 5)
            avg_urls_per_minute = self.batch_size / avg_batch_time if avg_batch_time > 0 else 0
            
            # Print color-coded status
            print(f"{COLORS['BOLD']}{COLORS['BG_GREEN']} SUCCESS {COLORS['RESET']}")
            print(f"Processed {COLORS['BOLD']}{urls_processed}{COLORS['RESET']} URLs in {COLORS['BOLD']}{batch_duration:.1f}{COLORS['RESET']} minutes")
            print(f"Processing rate: {COLORS['BOLD']}{urls_per_minute:.1f}{COLORS['RESET']} URLs/minute")
            
            # Log success
            logger.info(f"Batch #{batch_num} completed successfully")
            logger.info(f"Processed {urls_processed} URLs in {batch_duration:.1f} minutes ({urls_per_minute:.1f} URLs/minute)")
            
            return True
        except subprocess.CalledProcessError as e:
            batch_end = datetime.now()
            batch_duration = (batch_end - batch_start).total_seconds() / 60.0
            
            # Update statistics
            self.failed_batches += 1
            
            # Print color-coded status
            print(f"{COLORS['BOLD']}{COLORS['BG_RED']} FAILED {COLORS['RESET']}")
            
            # Log error
            logger.error(f"Batch failed with exit code {e.returncode}")
            logger.error(f"Error output: {e.stdout}")
            
            # Check if no more URLs to process
            if "No unprocessed URLs found in the database" in e.stdout:
                logger.info("No more URLs to process in this priority level")
                return False
            
            return False
    
    async def run_continuous(self) -> bool:
        """
        Run continuous processing of URLs until all are complete.
        
        Returns:
            True if processing was successful, False otherwise
        """
        # Create output directories
        os.makedirs("logs", exist_ok=True)
        
        # Check database status
        processed_urls, total_urls = await self.get_db_status()
        logger.info(f"Database status: {processed_urls}/{total_urls} URLs processed ({processed_urls/total_urls*100:.2f}% complete)")
        
        # Reset failed URLs if requested
        if self.reset_failed:
            reset_count = await self.reset_failed_urls()
            if reset_count > 0:
                # Recheck database status
                processed_urls, total_urls = await self.get_db_status()
                logger.info(f"Database status after reset: {processed_urls}/{total_urls} URLs processed ({processed_urls/total_urls*100:.2f}% complete)")
        
        # Initialize batch counter
        batch_num = 1
        
        # Determine priorities to process
        priorities = ["high", "medium", "low"] if self.prioritize else [None]
        
        # Process each priority in order
        for priority in priorities:
            # Print section header
            priority_str = f"{priority.upper()} PRIORITY" if priority else "ALL PRIORITY LEVELS"
            print(f"\n{COLORS['BOLD']}{COLORS['BG_YELLOW']} PROCESSING {priority_str} {COLORS['RESET']}\n")
            
            # Process batches in this priority level
            retries = 0
            while retries <= self.max_retries:
                # Get current status
                processed_urls, total_urls = await self.get_db_status()
                remaining = total_urls - processed_urls
                
                # Calculate processing statistics
                elapsed_minutes = (datetime.now() - self.start_time).total_seconds() / 60.0
                overall_rate = self.processed_urls / elapsed_minutes if elapsed_minutes > 0 else 0
                
                # Estimate completion time
                if self.processed_urls > 0 and overall_rate > 0:
                    remaining_minutes = remaining / overall_rate
                    est_completion = datetime.now() + timedelta(minutes=remaining_minutes)
                    est_completion_str = est_completion.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    remaining_minutes = 0
                    est_completion_str = "Unknown"
                
                # Print batch header with color-coded progress info
                print(f"\n{COLORS['BOLD']}Batch #{batch_num} - {processed_urls}/{total_urls} URLs processed ({processed_urls/total_urls*100:.2f}%){COLORS['RESET']}")
                if self.processed_urls > 0:
                    print(f"Session progress: {self.processed_urls} URLs in {elapsed_minutes:.1f} minutes")
                    print(f"Processing rate: {COLORS['BOLD']}{overall_rate:.1f}{COLORS['RESET']} URLs/minute")
                    print(f"Estimated completion: {COLORS['BOLD']}{est_completion_str}{COLORS['RESET']} ({remaining_minutes:.1f} minutes remaining)")
                
                # Run a batch
                success = await self.run_batch(batch_num, priority)
                
                if success:
                    # Reset retry counter on success
                    retries = 0
                    batch_num += 1
                    
                    # Check if we've reached our limit
                    if self.limit and self.processed_urls >= self.limit:
                        logger.info(f"Reached processing limit of {self.limit} URLs")
                        return True
                    
                    # Wait before next batch
                    if self.delay > 0:
                        logger.info(f"Waiting {self.delay} seconds before next batch")
                        await asyncio.sleep(self.delay)
                else:
                    # Handle errors
                    retries += 1
                    
                    if retries > self.max_retries:
                        # If this is the last priority level, this is a persistent error
                        if priority == priorities[-1] or not self.prioritize:
                            logger.error(f"Failed {retries} times, giving up")
                            return False
                        else:
                            # Move to next priority level
                            logger.info(f"No more URLs to process in {priority} priority, moving to next priority")
                            break
                    else:
                        # Wait longer before retry
                        retry_delay = self.delay * (2 ** retries)
                        logger.warning(f"Batch failed, retrying in {retry_delay} seconds (retry {retries}/{self.max_retries})")
                        print(f"Waiting {retry_delay} seconds before retry...")
                        await asyncio.sleep(retry_delay)
        
        # Print final statistics
        processed_urls, total_urls = await self.get_db_status()
        elapsed_minutes = (datetime.now() - self.start_time).total_seconds() / 60.0
        overall_rate = self.processed_urls / elapsed_minutes if elapsed_minutes > 0 else 0
        
        print(f"\n{COLORS['BOLD']}{COLORS['BG_GREEN']} PROCESSING COMPLETE {COLORS['RESET']}")
        print(f"Database status: {processed_urls}/{total_urls} URLs processed ({processed_urls/total_urls*100:.2f}%)")
        print(f"Session processed {self.processed_urls} URLs in {elapsed_minutes:.1f} minutes ({overall_rate:.1f} URLs/minute)")
        print(f"Successful batches: {self.successful_batches}, Failed batches: {self.failed_batches}")
        
        logger.info(f"Continuous processing completed")
        logger.info(f"Database status: {processed_urls}/{total_urls} URLs processed ({processed_urls/total_urls*100:.2f}%)")
        logger.info(f"Session processed {self.processed_urls} URLs in {elapsed_minutes:.1f} minutes ({overall_rate:.1f} URLs/minute)")
        logger.info(f"Successful batches: {self.successful_batches}, Failed batches: {self.failed_batches}")
        
        return True

async def main():
    """Parse arguments and run continuous processing."""
    parser = argparse.ArgumentParser(
        description="Run continuous processing of URLs for the Holocron knowledge system"
    )
    parser.add_argument('--batch-size', type=int, default=150,
                      help="Number of URLs to process in each batch (default: 150)")
    parser.add_argument('--delay', type=int, default=5,
                      help="Seconds to wait between batches (default: 5)")
    parser.add_argument('--workers', type=int, default=10,
                      help="Number of worker processes (default: 10)")
    parser.add_argument('--requests-per-minute', type=int, default=60,
                      help="Maximum requests per minute for rate limiting (default: 60)")
    parser.add_argument('--prioritize', action='store_true',
                      help="Process high priority URLs first, then medium, then low")
    parser.add_argument('--limit', type=int, default=None,
                      help="Maximum number of URLs to process in this run (optional)")
    parser.add_argument('--reset-failed', action='store_true',
                      help="Reset URLs that were processed but have no chunks")
    parser.add_argument('--debug', action='store_true',
                      help="Enable detailed step-level timing and debug logging in batch pipeline")
    
    args = parser.parse_args()
    
    # Create processor
    processor = URLProcessor(
        batch_size=args.batch_size,
        delay=args.delay,
        max_retries=3,
        workers=args.workers,
        requests_per_minute=args.requests_per_minute,
        prioritize=args.prioritize,
        limit=args.limit,
        reset_failed=args.reset_failed
    )
    processor.debug = args.debug
    
    # Run continuous processing
    success = await processor.run_continuous()
    
    # Exit with appropriate status code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main()) 