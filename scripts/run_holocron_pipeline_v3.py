#!/usr/bin/env python3
"""
Holocron RAG Pipeline Runner (Version 3)

This script runs the optimized pipeline for the Holocron knowledge system:
1. Pulls unprocessed URLs directly from the database (no scraping step)
2. Process content into appropriate chunks
3. Generate embeddings for the chunks
4. Upload to the Supabase vector database
5. Mark URLs as processed in the database

Version 3 improves on Version 2 by using the existing URLs in the database
rather than collecting URLs from scratch each time.

Usage:
    python scripts/run_holocron_pipeline_v3.py [--limit N] [--workers N] [--batch-size N] 
    [--requests-per-minute N] [--priority high|medium|low]
"""

import os
import sys
import json
import argparse
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from colorama import Fore, Style, init

# Add src and root directories to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import patches first to handle the httpx client issue
try:
    from holocron import patches
    logging.info("Successfully imported patches for httpx")
except ImportError:
    logging.warning("Could not import holocron.patches - Supabase client might fail")

from src.holocron.wookieepedia_scraper import WookieepediaScraper
from src.holocron.data_processor import HolocronDataProcessor
from src.holocron.batch_processor import BatchProcessor
from holocron.url_collector.url_store import URLStore

# Add colorama for cross-platform colored terminal
try:
    init()  # Initialize colorama
    COLOR_ENABLED = True
except ImportError:
    # Define dummy color constants if colorama not available
    class DummyFore:
        def __getattr__(self, name):
            return ""
    class DummyStyle:
        def __getattr__(self, name):
            return ""
    Fore = DummyFore()
    Style = DummyStyle()
    COLOR_ENABLED = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/holocron_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Progress tracking variables
progress_stats = {
    "total_chunks": 0,
    "total_tokens": 0,
    "processed_urls": 0,
    "failed_urls": 0,
    "start_time": None,
    "last_update": None,
    "worker_status": {}
}

def print_progress_header():
    """Print a header for the progress display."""
    print("\n" + "="*80)
    print(f"{Fore.CYAN}HOLOCRON KNOWLEDGE PIPELINE{Style.RESET_ALL}".center(80))
    print("-"*80)
    print(f"{Fore.YELLOW}Star Wars Canon Knowledge Processing System{Style.RESET_ALL}".center(80))
    print("="*80 + "\n")

def print_status_bar(current: int, total: int, width: int = 40, prefix: str = '', suffix: str = '') -> None:
    """
    Print a progress bar to the terminal.
    
    Args:
        current: Current progress value
        total: Total progress value
        width: Width of the progress bar in characters
        prefix: Text to display before the progress bar
        suffix: Text to display after the progress bar
    """
    filled_length = int(width * current // total)
    bar = Fore.GREEN + '█' * filled_length + Fore.WHITE + '░' * (width - filled_length) + Style.RESET_ALL
    percent = f"{100 * (current / total):.1f}%"
    status_line = f"\r{prefix} |{bar}| {percent} {suffix}"
    print(status_line, end='', flush=True)

def format_time(seconds: float) -> str:
    """Format seconds into a readable time string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes, seconds = divmod(seconds, 60)
        return f"{int(minutes)}m {int(seconds)}s"
    else:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"

def calculate_eta(current: int, total: int, elapsed: float) -> str:
    """Calculate and format estimated time remaining."""
    if current == 0:
        return "estimating..."
    
    rate = elapsed / current
    remaining = rate * (total - current)
    return format_time(remaining)

def print_progress_summary():
    """Print a summary of the current progress."""
    if not progress_stats["start_time"]:
        return
    
    elapsed = time.time() - progress_stats["start_time"]
    
    # Clear line and print stats
    print("\r" + " " * 80, end="\r")
    
    # Print processing stats
    print(f"\n{Fore.CYAN}Process Stats:{Style.RESET_ALL}")
    print(f"  ├─ URLs Processed: {Fore.GREEN}{progress_stats['processed_urls']}{Style.RESET_ALL}")
    print(f"  ├─ Chunks Created: {Fore.GREEN}{progress_stats['total_chunks']}{Style.RESET_ALL}")
    print(f"  ├─ Tokens Indexed: {Fore.GREEN}{progress_stats['total_tokens']}{Style.RESET_ALL}")
    print(f"  ├─ Failed URLs: {Fore.RED if progress_stats['failed_urls'] > 0 else Fore.GREEN}{progress_stats['failed_urls']}{Style.RESET_ALL}")
    print(f"  └─ Elapsed Time: {Fore.YELLOW}{format_time(elapsed)}{Style.RESET_ALL}")
    print()
    
    # Print worker status
    if progress_stats["worker_status"]:
        print(f"{Fore.CYAN}Worker Status:{Style.RESET_ALL}")
        for worker_id, status in progress_stats["worker_status"].items():
            icon = "🔄" if status["active"] else "⏸️"
            color = Fore.GREEN if status["active"] else Fore.YELLOW
            print(f"  {icon} Worker {worker_id}: {color}{status['status']}{Style.RESET_ALL}")
    print()

class ProgressUpdater:
    """Updates the console with progress information at regular intervals."""
    
    def __init__(self, update_interval: float = 1.0):
        self.update_interval = update_interval
        self.active = False
        self._task = None
    
    async def updater_loop(self, total_urls: int, url_store: URLStore):
        """Main loop for updating the progress display."""
        self.active = True
        while self.active:
            if progress_stats["start_time"]:
                elapsed = time.time() - progress_stats["start_time"]
                eta = calculate_eta(progress_stats["processed_urls"], total_urls, elapsed)
                
                # Print progress bar for URL processing
                print_status_bar(
                    progress_stats["processed_urls"],
                    total_urls,
                    prefix=f"{Fore.CYAN}Progress:{Style.RESET_ALL}",
                    suffix=f"ETA: {eta}"
                )
                
                # Update processed count from database every 10 seconds
                if progress_stats["last_update"] is None or time.time() - progress_stats["last_update"] > 10:
                    try:
                        processed_count = await url_store.get_processed_count()
                        total_count = await url_store.get_total_count()
                        completion = (processed_count / total_count) * 100 if total_count > 0 else 0
                        print(f"\n{Fore.YELLOW}Database Status:{Style.RESET_ALL} {processed_count} of {total_count} URLs processed ({completion:.2f}%)")
                        progress_stats["last_update"] = time.time()
                    except Exception as e:
                        logger.error(f"Error updating database stats: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    async def start(self, total_urls: int, url_store: URLStore):
        """Start the progress updater."""
        self._task = asyncio.create_task(self.updater_loop(total_urls, url_store))
    
    async def stop(self):
        """Stop the progress updater."""
        if self._task:
            self.active = False
            await asyncio.sleep(0.5)  # Let the task finish gracefully
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            # Final print with newline
            print()

async def run_pipeline(args):
    """Run the optimized Holocron pipeline, getting URLs from the database."""
    # Create output directories if they don't exist
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data/checkpoints", exist_ok=True)
    
    # Initialize progress tracking
    progress_stats["start_time"] = time.time()
    progress_stats["last_update"] = None
    
    # Print welcome header
    print_progress_header()
    
    # Initialize URL Store
    url_store = URLStore()
    
    # Step 1: Get unprocessed URLs directly from the database
    print(f"{Fore.CYAN}Step 1:{Style.RESET_ALL} Retrieving unprocessed URLs from database...")
    logger.info(f"Getting unprocessed URLs from database (limit: {args.limit}, priority: {args.priority})")
    unprocessed_urls_data = await url_store.get_unprocessed_urls(args.limit, args.priority)
    
    if not unprocessed_urls_data:
        print(f"{Fore.RED}Error: No unprocessed URLs found in the database{Style.RESET_ALL}")
        logger.error("No unprocessed URLs found in the database")
        return False
    
    # Extract URLs and create ID mapping
    urls = [item['url'] for item in unprocessed_urls_data]
    # Create a mapping of URLs to their database IDs for later use
    url_to_id = {item['url']: item['id'] for item in unprocessed_urls_data}
    
    print(f"{Fore.GREEN}Found {len(urls)} unprocessed URLs to process{Style.RESET_ALL}")
    logger.info(f"Found {len(urls)} unprocessed URLs in the database")
    
    # Get current counts to show progress
    total_urls = await url_store.get_total_count()
    processed_urls = await url_store.get_processed_count()
    completion = (processed_urls / total_urls) * 100 if total_urls > 0 else 0
    print(f"{Fore.YELLOW}Database Status:{Style.RESET_ALL} {processed_urls} of {total_urls} URLs processed ({completion:.2f}%)")
    logger.info(f"Database status before processing: {processed_urls} of {total_urls} URLs processed ({completion:.2f}%)")
    
    # Initialize progress updater
    progress_updater = ProgressUpdater()
    
    # Step 2-4: Process URLs using BatchProcessor
    if urls:
        print(f"{Fore.CYAN}Step 2-4:{Style.RESET_ALL} Starting batch processing with {args.workers} workers...")
        logger.info(f"Starting batch processing of {len(urls)} URLs with {args.workers} workers")
        
        # Initialize worker status tracking
        for i in range(args.workers):
            progress_stats["worker_status"][i] = {"active": True, "status": "Initializing"}
        
        processor = BatchProcessor(
            urls=urls,
            num_workers=args.workers,
            batch_size=args.batch_size,
            checkpoint_dir="data/checkpoints",
            requests_per_minute=args.requests_per_minute
        )
        
        # Initialize data processor for actual content processing
        data_processor = HolocronDataProcessor()
        
        # Override the process_url method to use our actual processing logic
        debug = getattr(args, 'debug', False)
        async def process_url(url: str) -> bool:
            import time as _time
            worker_id = asyncio.current_task().get_name().split('_')[-1]
            worker_id = int(worker_id) if worker_id.isdigit() else 0
            timings = {}
            
            try:
                t_start = _time.perf_counter()
                progress_stats["worker_status"][worker_id] = {"active": True, "status": f"Scraping {url.split('/')[-1]}"}
                
                # Scrape content
                t0 = _time.perf_counter()
                async with WookieepediaScraper() as scraper:
                    content = await scraper.scrape_article(url)
                t1 = _time.perf_counter()
                timings['scrape'] = t1 - t0
                
                if content:
                    progress_stats["worker_status"][worker_id] = {
                        "active": True, 
                        "status": f"Processing {content.get('title', 'unknown')} ({len(content.get('sections', []))} sections)"
                    }
                    
                    # Process and upload content
                    t2 = _time.perf_counter()
                    success = await data_processor.process_and_upload([content])
                    t3 = _time.perf_counter()
                    timings['process_upload'] = t3 - t2
                    
                    # Update progress stats
                    progress_stats["processed_urls"] += 1
                    
                    # Get chunk count and token count
                    t4 = _time.perf_counter()
                    article_chunks = data_processor.chunk_article(content)
                    t5 = _time.perf_counter()
                    timings['chunking'] = t5 - t4
                    
                    progress_stats["total_chunks"] += len(article_chunks)
                    progress_stats["total_tokens"] += sum(chunk.get("content_tokens", 0) for chunk in article_chunks)
                    
                    t_end = _time.perf_counter()
                    total_time = t_end - t_start
                    
                    # Always log timing info with color and clear formatting
                    timing_msg = (
                        f"\n{Fore.CYAN}[TIMING]{Style.RESET_ALL} {url.split('/')[-1]}\n"
                        f"  ├─ Total: {Fore.GREEN}{total_time:.2f}s{Style.RESET_ALL}\n"
                        f"  ├─ Scrape: {timings['scrape']:.2f}s\n"
                        f"  ├─ Process/Upload: {timings['process_upload']:.2f}s\n"
                        f"  ├─ Chunking: {timings['chunking']:.2f}s\n"
                        f"  ├─ Chunks: {len(article_chunks)}\n"
                        f"  └─ Tokens: {sum(chunk.get('content_tokens', 0) for chunk in article_chunks)}\n"
                    )
                    print(timing_msg)
                    logger.info(f"[TIMING] {url.split('/')[-1]} | "
                              f"Total={total_time:.2f}s | "
                              f"Scrape={timings['scrape']:.2f}s | "
                              f"Process/Upload={timings['process_upload']:.2f}s | "
                              f"Chunking={timings['chunking']:.2f}s | "
                              f"Chunks={len(article_chunks)} | "
                              f"Tokens={sum(chunk.get('content_tokens', 0) for chunk in article_chunks)}")
                    
                    progress_stats["worker_status"][worker_id] = {
                        "active": True, 
                        "status": f"Completed {content.get('title', 'unknown')}"
                    }
                    return success
                else:
                    t_end = _time.perf_counter()
                    total_time = t_end - t_start
                    logger.warning(f"Failed to scrape URL: {url} (Total time={total_time:.2f}s, Scrape time={timings.get('scrape', 0):.2f}s)")
                    progress_stats["processed_urls"] += 1
                    progress_stats["failed_urls"] += 1
                    progress_stats["worker_status"][worker_id] = {
                        "active": True, 
                        "status": f"Failed to scrape {url.split('/')[-1]}"
                    }
                    return False
                    
            except Exception as e:
                t_end = _time.perf_counter()
                total_time = t_end - t_start
                logger.error(f"Error processing URL {url}: {str(e)} (Total time={total_time:.2f}s)")
                progress_stats["processed_urls"] += 1
                progress_stats["failed_urls"] += 1
                progress_stats["worker_status"][worker_id] = {
                    "active": True, 
                    "status": f"Error: {str(e)[:30]}..."
                }
                return False
        
        # Set the processing function
        processor.process_url = process_url
        
        # Start the progress updater
        await progress_updater.start(len(urls), url_store)
        
        # Inject our worker naming scheme
        original_worker = processor.worker
        async def worker_with_name(worker_id: int):
            task = asyncio.current_task()
            task.set_name(f"worker_{worker_id}")
            return await original_worker(worker_id)
        processor.worker = worker_with_name
        
        try:
            # Run the batch processor
            success = await processor.run()
            
            # Stop the progress updater
            await progress_updater.stop()
            
            # Show final progress summary
            print_progress_summary()
            
            if success:
                print(f"\n{Fore.GREEN}Holocron pipeline completed successfully!{Style.RESET_ALL}")
                logger.info("Holocron pipeline completed successfully")
                
                # Log final statistics - FIXED: Handle both int and collection types
                if hasattr(processor.progress, 'processed_urls'):
                    if isinstance(processor.progress.processed_urls, (list, set, tuple)):
                        processed_count = len(processor.progress.processed_urls)
                    else:
                        # Handle case when processed_urls is an integer counter
                        processed_count = processor.progress.processed_urls
                    print(f"Total URLs processed in this run: {processed_count}")
                    logger.info(f"Total URLs processed in this run: {processed_count}")
                else:
                    logger.info(f"Total URLs processed in this run: Unknown")
                    
                failed_count = len(processor.progress.failed_urls)
                print(f"Failed URLs in this run: {Fore.RED if failed_count > 0 else Fore.GREEN}{failed_count}{Style.RESET_ALL}")
                logger.info(f"Failed URLs in this run: {failed_count}")
                
                if processor.progress.failed_urls:
                    print(f"{Fore.YELLOW}Failed URLs:{Style.RESET_ALL}")
                    logger.info("Failed URLs:")
                    for url in processor.progress.failed_urls:
                        print(f"  - {url}")
                        logger.info(f"  - {url}")
                
                # Mark ALL URLs as processed, regardless of success or failure
                # This includes URLs that failed with errors and URLs that didn't generate any chunks
                all_urls_to_mark = set(urls)  # Mark all URLs that were attempted
                
                if all_urls_to_mark:
                    # Get IDs for all URLs to mark as processed
                    ids_to_mark = [url_to_id[url] for url in all_urls_to_mark if url in url_to_id]
                    if ids_to_mark:
                        print(f"{Fore.CYAN}Step 5:{Style.RESET_ALL} Marking {len(ids_to_mark)} URLs as processed in the database...")
                        logger.info(f"Marking {len(ids_to_mark)} URLs as processed in the database")
                        await url_store.mark_as_processed(ids_to_mark)
                        print(f"{Fore.GREEN}Successfully marked {len(ids_to_mark)} URLs as processed{Style.RESET_ALL}")
                                        
                # Verify database status
                try:
                    # Log processing status from database
                    processed_result = await url_store.get_processed_count()
                    total_result = await url_store.get_total_count()
                    completion = (processed_result / total_result) * 100 if total_result > 0 else 0
                    print(f"{Fore.YELLOW}Final Database Status:{Style.RESET_ALL} {processed_result} of {total_result} URLs processed ({completion:.2f}%)")
                    logger.info(f"Database status after processing: {processed_result} of {total_result} URLs processed ({completion:.2f}%)")
                except Exception as e:
                    logger.error(f"Error checking database status: {e}")
                    
                return True
            else:
                print(f"\n{Fore.RED}Holocron pipeline failed during processing phase{Style.RESET_ALL}")
                logger.error("Holocron pipeline failed during processing phase")
                return False
                
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Pipeline interrupted by user. Saving checkpoint...{Style.RESET_ALL}")
            logger.info("Pipeline interrupted by user")
            await processor.save_checkpoint()
            await progress_updater.stop()
            print(f"{Fore.YELLOW}Checkpoint saved. You can resume later.{Style.RESET_ALL}")
            return False
            
        finally:
            # Ensure progress updater is stopped
            await progress_updater.stop()
    else:
        print(f"{Fore.RED}No URLs to process{Style.RESET_ALL}")
        logger.error("No URLs to process")
        return False

def main():
    """Parse arguments and run the pipeline."""
    parser = argparse.ArgumentParser(description="Run the optimized Holocron knowledge pipeline")
    parser.add_argument('--limit', type=int, default=1000,
                      help="Maximum number of URLs to process in this run (default: 1000)")
    parser.add_argument('--priority', type=str, choices=['high', 'medium', 'low'], default=None,
                      help="Filter URLs by priority (optional)")
    parser.add_argument('--workers', type=int, default=10,
                      help="Number of worker processes (default: 10)")
    parser.add_argument('--batch-size', type=int, default=100,
                      help="Number of URLs to process before checkpointing (default: 100)")
    parser.add_argument('--requests-per-minute', type=int, default=60,
                      help="Maximum requests per minute for rate limiting (Wookieepedia allows 60 req/min)")
    parser.add_argument('--no-color', action='store_true',
                      help="Disable colored output")
    parser.add_argument('--debug', action='store_true',
                      help="Enable detailed step-level timing and debug logging")
    
    args = parser.parse_args()
    
    # Handle --no-color option
    global Fore, Style, COLOR_ENABLED
    if args.no_color:
        class DummyFore:
            def __getattr__(self, name):
                return ""
        class DummyStyle:
            def __getattr__(self, name):
                return ""
        Fore = DummyFore()
        Style = DummyStyle()
        COLOR_ENABLED = False
    
    try:
        # Run the pipeline
        success = asyncio.run(run_pipeline(args))
        
        # Exit with appropriate status code
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Pipeline interrupted by user.{Style.RESET_ALL}")
        sys.exit(1)

if __name__ == "__main__":
    main() 