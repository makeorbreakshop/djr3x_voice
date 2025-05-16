#!/usr/bin/env python3
"""
Batch Vector Creation Script

This script creates vector embeddings for processed Wookieepedia articles using OpenAI's
batch API for better efficiency and cost-effectiveness. The batch API allows processing
large numbers of embeddings asynchronously at 50% of the normal cost.

Usage:
    python scripts/create_vectors_batch.py --input-dir data/processed_articles --output-dir data/vectors_text-embedding-3-small [options]

Cost Benefits:
    - Batch API requests are 50% cheaper than individual API calls
    - Asynchronous processing reduces overall completion time
    - Automatic retries and error handling improve reliability
"""

import os
import sys
import json
import argparse
import logging
import time
import asyncio
import glob
import signal
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
import traceback
import random
import uuid
import hashlib

import pandas as pd
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import retry, wait_random_exponential, stop_after_attempt

# Add parent directory to path so we can import from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import ProcessStatusManager for URL tracking
from process_status_manager import ProcessStatusManager, ProcessingStatus

# Configure argument parsing
parser = argparse.ArgumentParser(description='Create vectors from processed articles using OpenAI batch API')
parser.add_argument('--input-dir', type=str, default='data/processed_articles', help='Input directory')
parser.add_argument('--output-dir', type=str, default='data/vectors_text-embedding-3-small', help='Output directory')
parser.add_argument('--batch-size', type=int, default=20000, help='Batch size (number of articles per batch, max 50000)')
parser.add_argument('--jsonl-dir', type=str, default='data/batch_jsonl_text-embedding-3-small', help='Directory for JSONL batch files')
parser.add_argument('--start-file', type=str, help='File to start processing from')
parser.add_argument('--test', action='store_true', help='Run in test mode (prepare but don\'t submit batches)')
parser.add_argument('--no-skip', action='store_true', help='Do not skip already processed URLs')
parser.add_argument('--chunk-size', type=int, default=500, help='Maximum tokens per chunk')
parser.add_argument('--overlap', type=int, default=50, help='Overlap between chunks')
parser.add_argument('--max-batch-chunks', type=int, default=1000, 
                   help='Maximum chunks per batch API call (max 2048)')
parser.add_argument('--poll-interval', type=int, default=600, 
                   help='Seconds between batch job status checks')
parser.add_argument('--embedding-model', type=str, default='text-embedding-3-small',
                   help='OpenAI embedding model to use')
parser.add_argument('--embedding-dimensions', type=int, default=1536,
                   help='Dimensions for the embedding model')
parser.add_argument('--log-level', type=str, default='INFO',
                   choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                   help='Set the logging level')
parser.add_argument('--progress-interval', type=int, default=10,
                   help='Number of batches between progress updates')
parser.add_argument('--limit', type=int, help='Stop after processing a certain number of batches')
args = parser.parse_args()

# Configure logging with a single handler and controlled verbosity
log_file = f"logs/vector_creation_batch_{datetime.now().strftime('%Y%m%d%H%M%S')}.log"
os.makedirs(os.path.dirname(log_file), exist_ok=True)
logging.basicConfig(
    level=getattr(logging, args.log_level),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Constants
CHUNK_SIZE = args.chunk_size
OVERLAP = args.overlap
BATCH_SIZE = args.batch_size
MAX_BATCH_CHUNKS = args.max_batch_chunks
EMBEDDING_MODEL = args.embedding_model
EMBEDDING_DIMENSIONS = args.embedding_dimensions
PROCESSING_STATUS_FILE = "data/processing_status_text-embedding-3-small.csv"
MAX_RETRIES = 5
POLL_INTERVAL = args.poll_interval
JSONL_DIR = args.jsonl_dir

# Global flag for graceful shutdown
shutdown_requested = False

class BatchVectorCreator:
    """Creates vectors from processed article files using OpenAI's batch API."""
    
    def __init__(self, input_dir: str, output_dir: str, jsonl_dir: str, batch_size: int = BATCH_SIZE, 
                 start_file: Optional[str] = None, test_mode: bool = False,
                 skip_processed: bool = True, max_batch_chunks: int = MAX_BATCH_CHUNKS,
                 limit: Optional[int] = None, stop_on_error: bool = False,
                 embedding_model: str = EMBEDDING_MODEL, embedding_dimensions: int = EMBEDDING_DIMENSIONS):
        """
        Initialize the batch vector creator.
        
        Args:
            input_dir: Directory containing article JSON files
            output_dir: Directory to write vector output files
            jsonl_dir: Directory to write JSONL batch files
            batch_size: Number of articles to process per batch
            start_file: File to start processing from (for resuming)
            test_mode: If True, don't make actual API calls
            skip_processed: Skip articles that have already been processed
            max_batch_chunks: Maximum chunks per batch API call
            limit: Stop after processing a certain number of batches
            stop_on_error: Whether to stop on first error
            embedding_model: OpenAI embedding model to use
            embedding_dimensions: Dimensionality of embeddings
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.jsonl_dir = Path(jsonl_dir)
        self.batch_size = batch_size
        self.start_file = start_file
        self.test_mode = test_mode
        self.skip_processed = skip_processed
        self.max_batch_chunks = max_batch_chunks
        self.limit = limit
        self.stop_on_error = stop_on_error
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        
        # Create output and JSONL directories if they don't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize OpenAI client
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        # Initialize ProcessStatusManager for URL tracking
        self.status_manager = ProcessStatusManager(PROCESSING_STATUS_FILE)
        logger.info(f"Loaded {len(self.status_manager.get_all_statuses())} URL statuses for deduplication")
        
        # Stats
        self.total_articles = 0
        self.total_chunks = 0
        self.successful_articles = 0
        self.failed_articles = 0
        self.skipped_articles = 0
        self.start_time = time.time()
        
        # Tracking the last processed file for resumability
        self.last_processed_file = None
        
        # Batch job tracking
        self.batch_jobs = []
        self.batch_file_ids = []
        self.chunk_id_mapping = {}  # Maps custom_ids to article metadata
        
        # Add retry decorator for API calls
        self._submit_batch = retry(
            wait=wait_random_exponential(min=1, max=20),
            stop=stop_after_attempt(6)
        )(self._submit_batch_internal)
    
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
        
        # Use a sensible default in case we can't parse the URL
        return file_name
    
    async def process_article(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Process a single article file.
        
        Args:
            file_path: Path to the article JSON file
            
        Returns:
            List of chunk dictionaries
        """
        try:
            # Extract URL from file path for deduplication
            url = self._extract_url_from_file_path(file_path)
            
            # Skip if already processed and skip_processed is True
            if self.skip_processed:
                status = self.status_manager.get_status(url)
                if status and status.processed and status.vectorized:
                    logger.debug(f"Skipping already processed article: {url}")
                    self.skipped_articles += 1
                    return []
            
            # Read and parse the article JSON file
            with open(file_path, 'r', encoding='utf-8') as f:
                article_data = json.load(f)
            
            # Check if article has content
            if not article_data:
                logger.warning(f"Empty article file: {file_path}")
                return []
            
            # Extract article data - handle both list and dictionary formats
            if isinstance(article_data, list):
                # Handle list format (multiple chunks)
                chunks = []
                for i, chunk_data in enumerate(article_data):
                    if not isinstance(chunk_data, dict):
                        logger.warning(f"Invalid chunk data in {file_path}, chunk {i}")
                        continue
                    
                    # Extract content from the chunk
                    content = chunk_data.get('content', '')
                    if not content:
                        logger.warning(f"Empty content in {file_path}, chunk {i}")
                        continue
                    
                    # Create chunk dictionary with consistent format
                    chunk = {
                        'file_path': file_path,
                        'url': url,
                        'title': chunk_data.get('title', ''),
                        'text': content,  # Ensure we use the key 'text' for content
                        'chunk_index': i,
                        'total_chunks': len(article_data)
                    }
                    chunks.append(chunk)
            else:
                # Handle dictionary format (single article)
                content = article_data.get('content', '')
                if not content:
                    logger.warning(f"Empty content in {file_path}")
                    return []
                
                # Create a single chunk dictionary
                chunk = {
                    'file_path': file_path,
                    'url': url,
                    'title': article_data.get('title', ''),
                    'text': content,  # Ensure we use the key 'text' for content
                    'chunk_index': 0,
                    'total_chunks': 1
                }
                chunks = [chunk]
            
            # Update counters
            self.successful_articles += 1
            self.total_chunks += len(chunks)
            self.last_processed_file = file_path
            
            return chunks
            
        except Exception as e:
            logger.error(f"Error processing {file_path}: {str(e)}")
            self.failed_articles += 1
            if self.stop_on_error:
                raise
            return []
    
    async def process_batch(self, batch_num: int, file_paths: List[str]) -> None:
        """
        Process a batch of files.
        
        Args:
            batch_num: The batch number
            file_paths: List of file paths to process
        """
        try:
            batch_start = time.time()
            logger.info(f"Processing batch {batch_num} with {len(file_paths)} articles")
            
            # Process all articles in this batch
            all_chunks = []
            
            for file_path in file_paths:
                chunks = await self.process_article(file_path)
                if chunks:
                    all_chunks.extend(chunks)
            
            logger.info(f"Batch {batch_num} generated {len(all_chunks)} chunks from {len(file_paths)} articles")
            
            if not all_chunks:
                logger.warning(f"No chunks to process in batch {batch_num}")
                return
            
            # Split chunks into sub-batches if needed to stay under max_batch_chunks
            # The batch API has a limit of 2048 inputs per batch
            sub_batches = [
                all_chunks[i:i + self.max_batch_chunks] 
                for i in range(0, len(all_chunks), self.max_batch_chunks)
            ]
            
            logger.info(f"Splitting {len(all_chunks)} chunks into {len(sub_batches)} sub-batches")
            
            jsonl_files = []
            batch_jobs = []
            
            # Create JSONL files for each sub-batch and submit them
            for sub_batch_idx, sub_batch in enumerate(sub_batches):
                # Create JSONL file for this sub-batch
                jsonl_file = await self.create_batch_jsonl(sub_batch, batch_num, sub_batch_idx)
                if jsonl_file:
                    jsonl_files.append(jsonl_file)
                    
                    # Submit batch job for this sub-batch
                    jobs = await self.submit_batch_jobs(jsonl_files)
                    if jobs:
                        batch_jobs.extend(jobs)
            
            # Store the batch jobs for later polling
            self.batch_jobs.extend(batch_jobs)
            
            # Log progress
            batch_duration = time.time() - batch_start
            logger.info(f"Batch {batch_num} processing completed in {batch_duration:.2f} seconds")
            
        except Exception as e:
            logger.error(f"Error processing batch {batch_num}: {str(e)}")
            if self.stop_on_error:
                raise
    
    async def create_batch_jsonl(self, chunks: List[Dict[str, Any]], batch_num: int, sub_batch: int) -> str:
        """
        Create a JSONL file for batch processing.
        
        Args:
            chunks: List of chunks to include in the JSONL file
            batch_num: Batch number
            sub_batch: Sub-batch number
            
        Returns:
            Path to the created JSONL file
        """
        if not chunks:
            return None
        
        os.makedirs(self.jsonl_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        jsonl_file = os.path.join(self.jsonl_dir, f"batch_{batch_num}_{sub_batch}_{timestamp}.jsonl")
        
        with open(jsonl_file, 'w', encoding='utf-8') as f:
            for i, chunk in enumerate(chunks):
                # Create a custom ID that includes file path and chunk info
                # Format: "file_path:chunk_index_chunk_text_summary"
                text_summary = chunk['text'][:30].replace('\n', ' ').replace('\r', '').strip()
                if len(text_summary) == 30:
                    text_summary += '...'
                
                custom_id = f"{chunk['file_path']}:{i}_{text_summary}"
                
                # Create the batch API request JSON structure
                request = {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": "/v1/embeddings",
                    "body": {
                        "model": self.embedding_model,
                        "input": chunk['text'],
                        "encoding_format": "float",
                        "dimensions": self.embedding_dimensions
                    }
                }
                f.write(json.dumps(request) + '\n')
        
        logger.info(f"Created JSONL file {jsonl_file} with {len(chunks)} chunks")
        return jsonl_file
    
    async def _submit_batch_internal(self, jsonl_file: str) -> dict:
        """Internal method to submit a batch with retries."""
        try:
            # --- START DIAGNOSTIC LOGGING ---
            logger.debug(f"Diag: In _submit_batch_internal for {jsonl_file}")
            logger.debug(f"Diag: Type of self.client: {type(self.client)}")
            logger.debug(f"Diag: ID of self.client object: {id(self.client)}")
            has_batches = hasattr(self.client, 'batches')
            logger.debug(f"Diag: hasattr(self.client, 'batches'): {has_batches}")
            if has_batches:
                logger.debug(f"Diag: Type of self.client.batches: {type(self.client.batches)}")
            # --- END DIAGNOSTIC LOGGING ---
            
            # First, create a file object that will be used for the batch
            logger.debug(f"Uploading file {jsonl_file} to OpenAI for batch processing")
            with open(jsonl_file, "rb") as f:
                file_obj = self.client.files.create(
                    file=f,
                    purpose="batch"
                )
            logger.debug(f"File uploaded with ID: {file_obj.id}")

            # Now create the batch job using batches directly as shown in the documentation
            batch_job = self.client.batches.create(
                input_file_id=file_obj.id,
                endpoint="/v1/embeddings",
                completion_window="24h",
                metadata={
                    "description": f"Wookieepedia embeddings batch {os.path.basename(jsonl_file)}"
                }
            )
            
            logger.debug(f"Batch job created with ID: {batch_job.id}")
            return batch_job

        except Exception as e:
            logger.error(f"Error in batch submission for {jsonl_file}: {e}")
            logger.error(f"Diag (on error): Type of self.client: {type(self.client)}")
            has_batches = hasattr(self.client, 'batches')
            logger.error(f"Diag (on error): hasattr(self.client, 'batches'): {has_batches}")
            logger.error(f"Diag (on error): Attributes of self.client: {dir(self.client)}")
            raise RuntimeError(f"Error submitting batch for {jsonl_file}: {e}")

    async def submit_batch_jobs(self, jsonl_files: List[str]) -> List[dict]:
        """Submit batch jobs to OpenAI with retries and rate limiting."""
        if not jsonl_files:
            return []
        
        if self.test_mode:
            logger.info(f"Test mode: Would submit {len(jsonl_files)} batch jobs to OpenAI")
            return [{"id": f"test_job_id_{i}"} for i in range(len(jsonl_files))]
        
        logger.info(f"Submitting {len(jsonl_files)} batches to OpenAI embeddings API")
        
        batch_jobs = []
        for jsonl_file in jsonl_files:
            try:
                batch_job = await self._submit_batch_internal(jsonl_file)
                batch_jobs.append(batch_job)
                logger.info(f"Successfully submitted batch from file {jsonl_file}")
                
                # Add a small delay between submissions to avoid rate limits
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error submitting batch for {jsonl_file}: {str(e)}")
                if self.stop_on_error:
                    raise
        
        return batch_jobs
    
    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    async def _poll_job_status(self, job_id: str) -> dict:
        """Poll a single job's status with retries."""
        return self.client.batches.retrieve(job_id)

    async def poll_batch_jobs(self) -> Dict[str, Dict]:
        """Poll batch jobs until all are completed or failed."""
        if self.test_mode or not self.batch_jobs:
            logger.info("Test mode or no batch jobs submitted, skipping polling")
            return {}

        logger.info(f"Polling {len(self.batch_jobs)} batch jobs until completion")

        job_outputs: Dict[str, Dict] = {}
        active_jobs = list(self.batch_jobs)  # Create a mutable copy
        job_details: Dict[str, Any] = {job.id: job for job in active_jobs}

        polling_start_time = time.time()

        while active_jobs:
            jobs_to_remove = []
            for i, job_summary in enumerate(active_jobs):
                job_id = job_summary.id
                try:
                    # Use batches directly as shown in the documentation
                    job = self.client.batches.retrieve(job_id)
                    
                    current_status = job.status
                    if current_status != job_details[job_id].status:
                        logger.debug(f"Job {job_id} status changed: {job_details[job_id].status} -> {current_status}")
                        job_details[job_id] = job
                    
                    if current_status in ["completed", "failed", "cancelled"]:
                        logger.info(f"Job {job_id} {current_status}")
                        if current_status == "completed":
                            # Get the output file ID
                            if hasattr(job, "output_file_id") and job.output_file_id:
                                output_content = await self._get_file_content_bytes(job.output_file_id)
                                output_content_str = output_content.decode('utf-8')
                                job_outputs[job_id] = {
                                    "output_content_lines": output_content_str.strip().split('\n'),
                                    "job_details": job
                                }
                            else:
                                logger.warning(f"Job {job_id} marked as completed but no output_file_id")
                                job_outputs[job_id] = {"error": "No output file ID", "job_details": job}
                        else:
                            job_outputs[job_id] = {"error": f"Job {current_status}", "job_details": job}
                        
                        jobs_to_remove.append(job_summary)
                except Exception as e:
                    logger.error(f"Error polling job {job_id}: {e}")
                    # If we've been polling for a long time and can't get job details, 
                    # consider it failed to avoid infinite loops
                    if time.time() - polling_start_time > (25 * 60 * 60):  # 25 hours
                        logger.warning(f"Giving up on job {job_id} after 25 hours")
                        job_outputs[job_id] = {"error": f"Polling timeout: {str(e)}", "job_details": job_details[job_id]}
                        jobs_to_remove.append(job_summary)
            
            # Remove completed or failed jobs
            for job in jobs_to_remove:
                active_jobs.remove(job)
            
            if active_jobs:
                jobs_count = len(active_jobs)
                logger.info(f"Waiting for {jobs_count} batch job(s) to complete. Sleeping for {POLL_INTERVAL} seconds.")
                await asyncio.sleep(POLL_INTERVAL)
                
                # Check for timeout on entire polling operation
                if time.time() - polling_start_time > (25 * 60 * 60):  # 25 hours
                    logger.warning(f"Polling timeout after 25 hours with {jobs_count} job(s) still active")
                    for job in active_jobs:
                        job_id = job.id
                        job_outputs[job_id] = {"error": "Global polling timeout", "job_details": job_details[job_id]}
                    break
        
        logger.info(f"All batch jobs processed. Successful: {len([j for j in job_outputs.values() if 'error' not in j])}")
        return job_outputs

    @retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(10))
    async def _get_file_content_bytes(self, file_id: str) -> bytes:
        """Get file content as bytes with retries."""
        # The .content() method on a file typically returns bytes
        response = self.client.files.content(file_id)
        return response.read() # Assuming response is a file-like object or has a .read() method for bytes
    
    async def process_batch_outputs(self, batch_results: Dict[str, Dict]) -> None:
        """
        Process batch results and save embeddings.
        
        Args:
            batch_results: Dictionary mapping job IDs to job outputs from poll_batch_jobs
        """
        if self.test_mode:
            logger.info("Test mode: Would process batch outputs and save embeddings")
            return

        if not batch_results:
            logger.warning("No batch results to process")
            return

        logger.info(f"Processing outputs from {len(batch_results)} batch jobs")

        total_vectors_processed = 0
        total_errors_in_processing = 0

        for job_id, result_data in batch_results.items():
            if "error" in result_data:
                logger.error(f"Skipping processing for job {job_id} due to error: {result_data['error']}")
                total_errors_in_processing +=1
                continue

            output_lines = result_data.get("output_content_lines", [])
            if not output_lines:
                logger.warning(f"No output content lines found for job {job_id}")
                continue
            
            job_detail_info = result_data.get("job_details")
            logger.info(f"Processing {len(output_lines)} results from batch job {job_id} (Original input file: {job_detail_info.input_file_id if job_detail_info else 'N/A'})")

            for line_num, line_content in enumerate(output_lines):
                try:
                    if not line_content.strip(): # Skip empty lines
                        continue
                    
                    response_item = json.loads(line_content)
                    custom_id = response_item.get("custom_id")
                    
                    # The actual embedding result is usually nested
                    # e.g., response_item["response"]["body"]["data"][0]["embedding"]
                    # This structure depends on the endpoint, for /v1/embeddings:
                    response_body = response_item.get("response", {}).get("body", {})
                    if not response_body:
                        logger.error(f"Job {job_id}, line {line_num}: No 'response' or 'body' in output line: {line_content[:200]}...")
                        total_errors_in_processing += 1
                        continue
                    
                    embedding_data_list = response_body.get("data")
                    if not isinstance(embedding_data_list, list) or not embedding_data_list:
                        logger.error(f"Job {job_id}, line {line_num}: 'data' is not a list or is empty in response body: {str(response_body)[:200]}...")
                        total_errors_in_processing += 1
                        continue
                    
                    # Assuming one embedding per request in the batch input
                    embedding_obj = embedding_data_list[0]
                    embedding = embedding_obj.get("embedding")

                    if not custom_id or not embedding:
                        logger.error(f"Job {job_id}, line {line_num}: Missing custom_id or embedding in response: {line_content[:200]}...")
                        total_errors_in_processing += 1
                        continue

                    parsed_custom_id = self._parse_custom_id(custom_id)
                    if not parsed_custom_id:
                        logger.warning(f"Job {job_id}, line {line_num}: Could not parse custom_id: {custom_id}")
                        total_errors_in_processing += 1
                        continue

                    file_path, chunk_index, chunk_text = parsed_custom_id
                    await self._save_embedding(file_path, chunk_index, chunk_text, embedding)
                    total_vectors_processed += 1

                except json.JSONDecodeError as jde:
                    logger.error(f"Job {job_id}, line {line_num}: JSONDecodeError for line: {line_content[:200]}... Error: {jde}")
                    total_errors_in_processing += 1
                except Exception as e:
                    logger.error(f"Job {job_id}, line {line_num}: Error processing result line: {str(e)}. Line content: {line_content[:200]}...")
                    total_errors_in_processing += 1
        
        logger.info(f"Finished processing all batch outputs. Created {total_vectors_processed} vectors with {total_errors_in_processing} errors during processing.")
    
    def _log_progress(self, batch_num, total_batches):
        """Log the current progress of batch processing."""
        pct_complete = (batch_num / total_batches) * 100 if total_batches > 0 else 0
        logger.info(f"Processed batch {batch_num}/{total_batches} ({pct_complete:.1f}% complete)")

    def _parse_custom_id(self, custom_id: str) -> Optional[Tuple[str, int, str]]:
        """
        Parse the custom ID to extract file path, chunk index, and chunk text.
        
        Args:
            custom_id: The custom ID from the batch response
            
        Returns:
            Tuple of (file_path, chunk_index, chunk_text) or None if parsing fails
        """
        try:
            # Expected format: "file_path:chunk_index"
            if ':' not in custom_id:
                return None
                
            parts = custom_id.split(':', 1)
            if len(parts) != 2:
                return None
                
            file_path = parts[0]
            chunk_info = parts[1]
            
            if '_' not in chunk_info:
                return None
                
            chunk_parts = chunk_info.split('_', 1)
            if len(chunk_parts) != 2:
                return None
                
            chunk_index = int(chunk_parts[0])
            chunk_text = chunk_parts[1]
            
            return file_path, chunk_index, chunk_text
        except Exception as e:
            logger.error(f"Error parsing custom ID {custom_id}: {str(e)}")
            return None
    
    async def _save_embedding(self, file_path: str, chunk_index: int, chunk_text: str, embedding: List[float]) -> None:
        """
        Save an embedding to the output directory.
        
        Args:
            file_path: The original file path
            chunk_index: The index of the chunk
            chunk_text: The text content of the chunk
            embedding: The embedding vector
        """
        try:
            # Extract URL from file path
            url = self._extract_url_from_file_path(file_path)
            
            # Create a unique filename based on the URL hash and chunk index
            url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
            output_file = os.path.join(self.output_dir, f"{url_hash}_{chunk_index}.json")
            
            # Create the vector object
            vector_data = {
                "url": url,
                "file_path": file_path,
                "chunk_index": chunk_index,
                "text": chunk_text,
                "embedding": embedding,
                "model": self.embedding_model,
                "dimensions": len(embedding),
                "created_at": datetime.now().isoformat()
            }
            
            # Save the vector to a JSON file
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(vector_data, f, ensure_ascii=False)
                
            # Update the status manager to mark this URL as vectorized
            if self.status_manager is not None:
                status = self.status_manager.get_status(url)
                if status:
                    status.vectorized = True
                    self.status_manager.update_status(url, status)
        except Exception as e:
            logger.error(f"Error saving embedding for {file_path} chunk {chunk_index}: {str(e)}")
            if self.stop_on_error:
                raise
    
    def _log_final_summary(self):
        """Log final summary statistics."""
        elapsed = time.time() - self.start_time
        logger.info("=" * 50)
        logger.info(f"Processing complete in {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        logger.info(f"Total articles processed: {self.total_articles}")
        logger.info(f"Articles successfully processed: {self.successful_articles}")
        logger.info(f"Articles failed: {self.failed_articles}")
        logger.info(f"Articles skipped: {self.skipped_articles}")
        logger.info(f"Total chunks created: {self.total_chunks}")
        logger.info(f"Last processed file: {self.last_processed_file}")
        logger.info("=" * 50)
    
    def _handle_shutdown(self, batch_num):
        """Handle graceful shutdown request."""
        logger.warning(f"Shutdown requested. Finishing batch {batch_num} and exiting...")
        self._log_final_summary()
        return True
    
    async def run(self) -> None:
        """Run the batch vector creation process."""
        try:
            start_time = time.time()
            logger.info("Starting batch vector creation process...")
            
            # Find all the JSON files in the input directory
            if not os.path.exists(self.input_dir):
                logger.error(f"Input directory {self.input_dir} does not exist")
                return
            
            logger.info(f"Finding JSON files in {self.input_dir}...")
            file_paths = []
            for root, _, files in os.walk(self.input_dir):
                for file in files:
                    if file.endswith('.json'):
                        file_paths.append(os.path.join(root, file))
            
            logger.info(f"Found {len(file_paths)} JSON files")
            
            # Create the output directory if it doesn't exist
            os.makedirs(self.output_dir, exist_ok=True)
            
            # Create batches of files
            batch_size = self.batch_size
            batches = [file_paths[i:i + batch_size] for i in range(0, len(file_paths), batch_size)]
            logger.info(f"Processing {len(file_paths)} files in {len(batches)} batches")
            
            # Process each batch
            for batch_num, file_batch in enumerate(batches, start=1):
                await self.process_batch(batch_num, file_batch)
                
                # Log progress after each batch
                self._log_progress(batch_num, len(batches))
                
                # Stop after the limit if specified
                if self.limit and batch_num >= self.limit:
                    logger.info(f"Reached batch limit of {self.limit}, stopping")
                    break
            
            # Poll batch jobs and process results
            logger.info("All batches submitted, waiting for batch jobs to complete...")
            job_outputs = await self.poll_batch_jobs()
            
            # Process batch outputs
            await self.process_batch_outputs(job_outputs)
            
            # Log final summary
            elapsed = time.time() - start_time
            logger.info("==================================================")
            logger.info(f"Processing complete in {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
            logger.info(f"Total articles processed: {self.total_articles}")
            logger.info(f"Articles successfully processed: {self.successful_articles}")
            logger.info(f"Articles failed: {self.failed_articles}")
            logger.info(f"Articles skipped: {self.skipped_articles}")
            logger.info(f"Total chunks created: {self.total_chunks}")
            logger.info(f"Last processed file: {self.last_processed_file}")
            logger.info("==================================================")
        
        except Exception as e:
            logger.error(f"Error in batch vector creation process: {str(e)}")
            if self.stop_on_error:
                raise

def signal_handler(sig, frame):
    """Handle interrupt signals for graceful shutdown."""
    global shutdown_requested
    if not shutdown_requested:
        logger.warning("Interrupt received, will exit after current batch completes...")
        shutdown_requested = True
    else:
        logger.warning("Second interrupt received, forcing exit...")
        sys.exit(1)

def main():
    """Main function."""
    try:
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Initialize vector creator
        vector_creator = BatchVectorCreator(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            jsonl_dir=args.jsonl_dir,
            batch_size=args.batch_size,
            start_file=args.start_file,
            test_mode=args.test,
            skip_processed=not args.no_skip,
            max_batch_chunks=args.max_batch_chunks,
            limit=args.limit
        )
        
        # Start the processing
        if args.test:
            logger.info("Running in TEST mode - will process 10 articles and prepare batches but not submit to API")
            # Override batch size for test mode
            vector_creator.batch_size = 10
        
        # Run the process
        asyncio.run(vector_creator.run())
        
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        logger.error(traceback.format_exc())
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 