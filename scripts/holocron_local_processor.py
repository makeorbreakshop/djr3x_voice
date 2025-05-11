#!/usr/bin/env python3
"""
Holocron Local Processor

This script processes URLs from the local CSV file:
1. Reads unprocessed URLs from holocron/supabase_dump/Holocron URLs.csv
2. Processes content into appropriate chunks
3. Generates embeddings for the chunks
4. Saves results locally in Parquet format for Pinecone upload
5. Tracks processing status in a local status file

Usage:
    python scripts/holocron_local_processor.py [--limit N] [--workers N] 
    [--batch-size N] [--requests-per-minute N] [--test]
"""

import os
import sys
import json
import argparse
import asyncio
import logging
import time
import csv
import pandas as pd
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from pathlib import Path

# Add src and root directories to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import components
from src.holocron.wookieepedia_scraper import WookieepediaScraper
from src.holocron.data_processor import HolocronDataProcessor
from src.holocron.batch_processor import BatchProcessor

# Add colorama for cross-platform colored terminal
try:
    from colorama import init, Fore, Style
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
        logging.FileHandler(f"logs/holocron_local_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Constants
CSV_FILE_PATH = "holocron/supabase_dump/Holocron URLs.csv"
STATUS_FILE_PATH = "data/processing_status.csv"
VECTORS_DIR = "data/vectors"
CHECKPOINT_DIR = "data/checkpoints"

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

class LocalURLStore:
    """
    Store for managing URL processing status locally instead of in Supabase.
    Reads from the CSV export and tracks status in a local CSV file.
    """
    
    def __init__(self, csv_path: str, status_path: str):
        """Initialize the local URL store."""
        self.csv_path = csv_path
        self.status_path = status_path
        self.status_data = self._load_or_create_status_file()
        
    def _load_or_create_status_file(self) -> Dict[str, Dict]:
        """Load the status file or create it if it doesn't exist."""
        status_data = {}
        
        # Create status file if it doesn't exist
        if not os.path.exists(self.status_path):
            # Make sure the directory exists
            os.makedirs(os.path.dirname(self.status_path), exist_ok=True)
            
            # Read all URLs from the CSV and initialize status
            with open(self.csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    url = row.get('url', '')
                    if url:
                        status_data[url] = {
                            'is_processed': False,
                            'processed_at': None,
                            'priority': row.get('priority', 'low'),
                            'id': row.get('id', '')
                        }
            
            # Write initial status file
            self._write_status_file(status_data)
            logger.info(f"Created new status file with {len(status_data)} URLs")
        else:
            # Read existing status file
            with open(self.status_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    url = row.get('url', '')
                    if url:
                        status_data[url] = {
                            'is_processed': row.get('is_processed', 'False').lower() == 'true',
                            'processed_at': row.get('processed_at', None),
                            'priority': row.get('priority', 'low'),
                            'id': row.get('id', '')
                        }
            logger.info(f"Loaded existing status file with {len(status_data)} URLs")
            
        return status_data
        
    def _write_status_file(self, status_data: Dict[str, Dict]) -> None:
        """Write the status data to the CSV file."""
        with open(self.status_path, 'w', encoding='utf-8', newline='') as f:
            fieldnames = ['url', 'is_processed', 'processed_at', 'priority', 'id']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for url, data in status_data.items():
                writer.writerow({
                    'url': url,
                    'is_processed': data.get('is_processed', False),
                    'processed_at': data.get('processed_at', ''),
                    'priority': data.get('priority', 'low'),
                    'id': data.get('id', '')
                })
                
    async def get_unprocessed_urls(self, limit: Optional[int] = None, priority: Optional[str] = None) -> List[Dict]:
        """Get unprocessed URLs with optional limit and priority filter."""
        result = []
        
        for url, data in self.status_data.items():
            if not data.get('is_processed', False):
                if priority is None or data.get('priority', 'low') == priority:
                    result.append({
                        'url': url,
                        'id': data.get('id', ''),
                        'priority': data.get('priority', 'low')
                    })
                    
                    if limit is not None and len(result) >= limit:
                        break
                        
        return result
        
    async def mark_as_processed(self, urls: List[str]) -> None:
        """Mark URLs as processed."""
        current_time = datetime.now().isoformat()
        
        for url in urls:
            if url in self.status_data:
                self.status_data[url]['is_processed'] = True
                self.status_data[url]['processed_at'] = current_time
                
        # Write updated status to file
        self._write_status_file(self.status_data)
        logger.info(f"Marked {len(urls)} URLs as processed")
        
    async def get_processed_count(self) -> int:
        """Get count of processed URLs."""
        return sum(1 for data in self.status_data.values() if data.get('is_processed', False))
        
    async def get_total_count(self) -> int:
        """Get total count of URLs."""
        return len(self.status_data)

def print_progress_header():
    """Print a header for the progress display."""
    print("\n" + "="*80)
    print(f"{Fore.CYAN}HOLOCRON LOCAL PROCESSOR{Style.RESET_ALL}".center(80))
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
    
    async def updater_loop(self, total_urls: int, url_store: LocalURLStore):
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
                
                # Update processed count from status file every 10 seconds
                if progress_stats["last_update"] is None or time.time() - progress_stats["last_update"] > 10:
                    try:
                        processed_count = await url_store.get_processed_count()
                        total_count = await url_store.get_total_count()
                        completion = (processed_count / total_count) * 100 if total_count > 0 else 0
                        print(f"\n{Fore.YELLOW}Status:{Style.RESET_ALL} {processed_count} of {total_count} URLs processed ({completion:.2f}%)")
                        progress_stats["last_update"] = time.time()
                    except Exception as e:
                        logger.error(f"Error updating stats: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    async def start(self, total_urls: int, url_store: LocalURLStore):
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

class LocalDataProcessor(HolocronDataProcessor):
    """Extension of HolocronDataProcessor that saves results locally instead of to Supabase."""
    
    def __init__(self, vectors_dir: str):
        super().__init__()
        self.vectors_dir = vectors_dir
        os.makedirs(vectors_dir, exist_ok=True)
        
    def chunk_article(self, content: Dict) -> List[Dict]:
        """
        Override the parent method to ensure chunk_id is included.
        Chunks an article into smaller parts for processing.
        
        Args:
            content: Dictionary containing article content
            
        Returns:
            List of chunks with content and metadata
        """
        # Get chunks using parent method (which might not include chunk_id)
        chunks = super().chunk_article(content)
        
        # Ensure each chunk has a chunk_id
        for i, chunk in enumerate(chunks):
            if 'chunk_id' not in chunk:
                chunk['chunk_id'] = f"chunk_{i+1}"
                
        return chunks
        
    async def generate_embedding(self, content: str) -> List[float]:
        """
        Generate embeddings for content using OpenAI API.
        
        Args:
            content: The text to generate embeddings for
            
        Returns:
            List of embedding values
        """
        try:
            import openai
            from openai import OpenAI

            # Get API key from environment variable
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
                
            # Initialize client
            client = OpenAI(api_key=api_key)
            
            # Generate embeddings
            response = client.embeddings.create(
                input=content,
                model="text-embedding-ada-002"
            )
            
            # Extract and return embedding values
            embedding = response.data[0].embedding
            
            return embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            # Return zeros as fallback (this will be flagged for manual review)
            return [0.0] * 1536
        
    async def process_and_upload(self, contents: List[Dict]) -> bool:
        """
        Process content and save vectors locally instead of uploading to Supabase.
        
        Args:
            contents: List of content items to process
            
        Returns:
            True if successful, False otherwise
        """
        try:
            all_vectors = []
            all_chunks = []
            chunk_mapping = {}  # To map chunk text back to metadata
            chunk_index = 0
            
            # First pass: chunk all articles and collect all texts
            for content in contents:
                # Ensure content has an ID
                if 'id' not in content:
                    # Generate a unique ID if none exists
                    content['id'] = f"article_{int(time.time())}_{len(all_vectors)}"
                
                # Chunk the article
                chunks = self.chunk_article(content)
                
                # Collect chunks and prepare mapping
                for chunk in chunks:
                    chunk_text = chunk['content']
                    all_chunks.append(chunk_text)
                    
                    # Store metadata mapping using index
                    chunk_mapping[chunk_index] = {
                        'id': f"{content['id']}_{chunk['chunk_id']}",
                        'metadata': {
                            'content': chunk_text,
                            'title': content.get('title', ''),
                            'url': content.get('url', ''),
                            'chunk_id': chunk['chunk_id'],
                            'content_tokens': chunk.get('content_tokens', 0),
                            'priority': content.get('priority', 'low')
                        }
                    }
                    chunk_index += 1
            
            # Batch generate embeddings for all chunks
            batch_size = 100  # OpenAI recommends 100 for stability
            embeddings = []
            
            for i in range(0, len(all_chunks), batch_size):
                batch = all_chunks[i:i+batch_size]
                try:
                    # Generate embeddings for the batch
                    batch_embeddings = await self.generate_embeddings_batch(batch)
                    embeddings.extend(batch_embeddings)
                except Exception as e:
                    logger.error(f"Error generating embeddings batch {i}-{i+len(batch)}: {e}")
                    # Add zero embeddings as fallback
                    embeddings.extend([[0.0] * 1536] * len(batch))
            
            # Create vectors using the embeddings and metadata
            for idx, embedding in enumerate(embeddings):
                if idx in chunk_mapping:
                    vector = {
                        'id': chunk_mapping[idx]['id'],
                        'values': embedding,
                        'metadata': chunk_mapping[idx]['metadata']
                    }
                    all_vectors.append(vector)
            
            # Save vectors to parquet file
            if all_vectors:
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{self.vectors_dir}/vectors_{timestamp}_{len(all_vectors)}.parquet"
                
                # Convert to DataFrame
                df = pd.DataFrame([{
                    'id': v['id'],
                    'values': v['values'],
                    'metadata': json.dumps(v['metadata'])
                } for v in all_vectors])
                
                # Save to parquet
                df.to_parquet(filename, compression='snappy')
                logger.info(f"Saved {len(all_vectors)} vectors to {filename}")
                
            return True
        except Exception as e:
            logger.error(f"Error processing content: {e}")
            return False
            
    async def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a batch of texts using OpenAI API.
        
        Args:
            texts: List of texts to generate embeddings for
            
        Returns:
            List of embedding vectors
        """
        try:
            import openai
            from openai import OpenAI

            # Get API key from environment variable
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")
                
            # Initialize client
            client = OpenAI(api_key=api_key)
            
            # Generate embeddings in batch
            response = client.embeddings.create(
                input=texts,
                model="text-embedding-ada-002"
            )
            
            # Extract and return embedding values in correct order
            embeddings = [data.embedding for data in response.data]
            
            return embeddings
        except Exception as e:
            logger.error(f"Error generating batch embeddings: {e}")
            # Return zeros as fallback (this will be flagged for manual review)
            return [[0.0] * 1536] * len(texts)

async def run_pipeline(args):
    """Run the local processing pipeline."""
    # Create output directories if they don't exist
    os.makedirs("logs", exist_ok=True)
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    os.makedirs(VECTORS_DIR, exist_ok=True)
    
    # Initialize progress tracking
    progress_stats["start_time"] = time.time()
    progress_stats["last_update"] = None
    
    # Print welcome header
    print_progress_header()
    
    # Check if CSV file exists
    if not os.path.exists(CSV_FILE_PATH):
        print(f"{Fore.RED}Error: CSV file not found at {CSV_FILE_PATH}{Style.RESET_ALL}")
        logger.error(f"CSV file not found at {CSV_FILE_PATH}")
        return False
    
    # Initialize URL Store
    url_store = LocalURLStore(CSV_FILE_PATH, STATUS_FILE_PATH)
    
    # Step 1: Get unprocessed URLs from the local status file
    print(f"{Fore.CYAN}Step 1:{Style.RESET_ALL} Retrieving unprocessed URLs from local status file...")
    logger.info(f"Getting unprocessed URLs (limit: {args.limit}, priority: {args.priority})")
    unprocessed_urls_data = await url_store.get_unprocessed_urls(args.limit, args.priority)
    
    if not unprocessed_urls_data:
        print(f"{Fore.RED}Error: No unprocessed URLs found{Style.RESET_ALL}")
        logger.error("No unprocessed URLs found")
        return False
    
    # Extract URLs
    urls = [item['url'] for item in unprocessed_urls_data]
    
    print(f"{Fore.GREEN}Found {len(urls)} unprocessed URLs to process{Style.RESET_ALL}")
    logger.info(f"Found {len(urls)} unprocessed URLs")
    
    # Get current counts to show progress
    total_urls = await url_store.get_total_count()
    processed_urls = await url_store.get_processed_count()
    completion = (processed_urls / total_urls) * 100 if total_urls > 0 else 0
    print(f"{Fore.YELLOW}Status:{Style.RESET_ALL} {processed_urls} of {total_urls} URLs processed ({completion:.2f}%)")
    logger.info(f"Status before processing: {processed_urls} of {total_urls} URLs processed ({completion:.2f}%)")
    
    # If test mode, only process a few URLs
    if args.test and len(urls) > 3:
        print(f"{Fore.YELLOW}Test mode: Processing only 3 URLs{Style.RESET_ALL}")
        urls = urls[:3]
        logger.info("Test mode: Limited to 3 URLs")
    
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
            checkpoint_dir=CHECKPOINT_DIR,
            requests_per_minute=args.requests_per_minute
        )
        
        # Initialize data processor for local storage
        data_processor = LocalDataProcessor(VECTORS_DIR)
        
        # Override the process_url method to use our actual processing logic
        async def process_url(url: str) -> bool:
            worker_id = asyncio.current_task().get_name().split('_')[-1]
            worker_id = int(worker_id) if worker_id.isdigit() else 0
            
            try:
                progress_stats["worker_status"][worker_id] = {"active": True, "status": f"Scraping {url.split('/')[-1]}"}
                
                # Scrape content
                async with WookieepediaScraper() as scraper:
                    content = await scraper.scrape_article(url)
                
                if content:
                    # Update worker status
                    progress_stats["worker_status"][worker_id] = {
                        "active": True, 
                        "status": f"Processing {content.get('title', 'unknown')} ({len(content.get('sections', []))} sections)"
                    }
                    
                    # Add URL to content for tracking
                    content['url'] = url
                    
                    # Process and save content locally
                    success = await data_processor.process_and_upload([content])
                    
                    # Update progress stats for successful processing
                    progress_stats["processed_urls"] += 1
                    
                    # Get chunk count and token count from content if available
                    article_chunks = data_processor.chunk_article(content)
                    progress_stats["total_chunks"] += len(article_chunks)
                    progress_stats["total_tokens"] += sum(chunk.get("content_tokens", 0) for chunk in article_chunks)
                    
                    # Update worker status
                    progress_stats["worker_status"][worker_id] = {
                        "active": True, 
                        "status": f"Completed {content.get('title', 'unknown')}"
                    }
                    
                    return success
                else:
                    # Update stats for failed scraping
                    progress_stats["processed_urls"] += 1
                    progress_stats["failed_urls"] += 1
                    progress_stats["worker_status"][worker_id] = {
                        "active": True, 
                        "status": f"Failed to scrape {url.split('/')[-1]}"
                    }
                    return False
            except Exception as e:
                # Update stats for exceptions
                progress_stats["processed_urls"] += 1
                progress_stats["failed_urls"] += 1
                progress_stats["worker_status"][worker_id] = {
                    "active": True, 
                    "status": f"Error: {str(e)[:30]}..."
                }
                logger.error(f"Failed to process URL {url}: {e}")
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
                print(f"\n{Fore.GREEN}Holocron local processing completed successfully!{Style.RESET_ALL}")
                logger.info("Holocron local processing completed successfully")
                
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
                
                # Mark all successful URLs as processed
                successful_urls = [url for url in urls if url not in processor.progress.failed_urls]
                
                if successful_urls:
                    print(f"{Fore.CYAN}Step 5:{Style.RESET_ALL} Marking {len(successful_urls)} URLs as processed...")
                    logger.info(f"Marking {len(successful_urls)} URLs as processed")
                    await url_store.mark_as_processed(successful_urls)
                    print(f"{Fore.GREEN}Successfully marked {len(successful_urls)} URLs as processed{Style.RESET_ALL}")
                                        
                # Verify status
                try:
                    # Log processing status
                    processed_result = await url_store.get_processed_count()
                    total_result = await url_store.get_total_count()
                    completion = (processed_result / total_result) * 100 if total_result > 0 else 0
                    print(f"{Fore.YELLOW}Final Status:{Style.RESET_ALL} {processed_result} of {total_result} URLs processed ({completion:.2f}%)")
                    logger.info(f"Status after processing: {processed_result} of {total_result} URLs processed ({completion:.2f}%)")
                    
                    # List generated Parquet files
                    parquet_files = list(Path(VECTORS_DIR).glob("*.parquet"))
                    print(f"{Fore.YELLOW}Generated Files:{Style.RESET_ALL} {len(parquet_files)} Parquet files with vectors")
                    logger.info(f"Generated {len(parquet_files)} Parquet files with vectors")
                    
                    for file in parquet_files[:5]:  # Show first 5 files
                        file_size = file.stat().st_size / (1024 * 1024)  # Size in MB
                        print(f"  - {file.name} ({file_size:.2f} MB)")
                    
                    if len(parquet_files) > 5:
                        print(f"  ... and {len(parquet_files) - 5} more files")
                        
                except Exception as e:
                    logger.error(f"Error checking final status: {e}")
                    
                return True
            else:
                print(f"\n{Fore.RED}Holocron processing failed during processing phase{Style.RESET_ALL}")
                logger.error("Holocron processing failed during processing phase")
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
    parser = argparse.ArgumentParser(description="Run the Holocron local processing pipeline")
    parser.add_argument('--limit', type=int, default=50,
                      help="Maximum number of URLs to process in this run (default: 50)")
    parser.add_argument('--priority', type=str, choices=['high', 'medium', 'low'], default=None,
                      help="Filter URLs by priority (optional)")
    parser.add_argument('--workers', type=int, default=3,
                      help="Number of worker processes (default: 3)")
    parser.add_argument('--batch-size', type=int, default=10,
                      help="Number of URLs to process before checkpointing (default: 10)")
    parser.add_argument('--requests-per-minute', type=int, default=60,
                      help="Maximum requests per minute for rate limiting (default: 60)")
    parser.add_argument('--test', action='store_true',
                      help="Run in test mode, only process 3 URLs")
    parser.add_argument('--no-color', action='store_true',
                      help="Disable colored output")
    
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