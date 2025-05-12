#!/usr/bin/env python3
"""
Upload to Pinecone

This script uploads locally processed vectors to Pinecone:
1. Reads vectors from Parquet files in data/vectors
2. Uploads them to Pinecone in batches with deduplication
3. Tracks upload progress with robust error handling and retry logic

Usage:
    python scripts/upload_to_pinecone.py [--batch-size N] [--test] [--skip-deduplication]
"""

import os
import sys
import json
import argparse
import asyncio
import logging
import time
import glob
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Set, Tuple
from pathlib import Path

import pandas as pd
import numpy as np
from tqdm import tqdm
from pinecone import Pinecone, ServerlessSpec
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
VECTORS_DIR = "../data/vectors"
UPLOAD_STATUS_FILE = "../data/pinecone_upload_status.json"
VECTOR_FINGERPRINTS_FILE = "../data/vector_fingerprints.json"

class PineconeUploader:
    """Handles uploading vectors to Pinecone with deduplication and adaptive rate limiting."""
    
    def __init__(self, batch_size: int = 500, skip_deduplication: bool = False):
        """Initialize the uploader."""
        # Load environment variables
        load_dotenv()
        
        # Get Pinecone API key
        self.api_key = os.getenv("PINECONE_API_KEY")
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY not found in environment variables")
            
        # Get Pinecone index name
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")
        
        # Initialize Pinecone client
        self.pc = Pinecone(api_key=self.api_key)
        
        # Set batch size and parallel processing settings
        self.initial_batch_size = batch_size
        self.batch_size = batch_size
        self.max_parallel_files = 3  # Process up to 3 files in parallel
        
        # Rate limiting settings
        self.min_batch_size = 50  # Minimum batch size we'll go down to
        self.max_batch_size = 1000  # Maximum batch size we'll go up to
        self.success_threshold = 3  # Number of consecutive successes before increasing batch size
        self.consecutive_successes = 0
        self.consecutive_failures = 0
        
        # Exponential backoff settings
        self.base_wait_time = 1  # Base wait time in seconds
        self.max_retries = 5  # Maximum number of retries
        
        # Initialize upload status
        self.upload_status = self._load_upload_status()
        
        # Vector fingerprinting for deduplication
        self.skip_deduplication = skip_deduplication
        self.vector_fingerprints = self._load_vector_fingerprints()
        
    def _load_upload_status(self) -> Dict[str, bool]:
        """Load the upload status file or create it if it doesn't exist."""
        if os.path.exists(UPLOAD_STATUS_FILE):
            with open(UPLOAD_STATUS_FILE, 'r') as f:
                return json.load(f)
        return {}
        
    def _save_upload_status(self) -> None:
        """Save the current upload status."""
        os.makedirs(os.path.dirname(UPLOAD_STATUS_FILE), exist_ok=True)
        with open(UPLOAD_STATUS_FILE, 'w') as f:
            json.dump(self.upload_status, f, indent=2)
            
    def _load_vector_fingerprints(self) -> Dict[str, str]:
        """Load vector fingerprints for deduplication."""
        if os.path.exists(VECTOR_FINGERPRINTS_FILE):
            with open(VECTOR_FINGERPRINTS_FILE, 'r') as f:
                return json.load(f)
        return {}
        
    def _save_vector_fingerprints(self) -> None:
        """Save vector fingerprints."""
        os.makedirs(os.path.dirname(VECTOR_FINGERPRINTS_FILE), exist_ok=True)
        with open(VECTOR_FINGERPRINTS_FILE, 'w') as f:
            json.dump(self.vector_fingerprints, f, indent=2)
            
    def _generate_vector_fingerprint(self, vector: Dict) -> str:
        """Generate a fingerprint for a vector based on its content."""
        # Create a concatenated string of key metadata fields
        metadata = vector.get('metadata', {})
        content = metadata.get('content', '')
        title = metadata.get('title', '')
        url = metadata.get('url', '')
        
        # Generate fingerprint from content and metadata
        fingerprint_data = f"{content}|{title}|{url}"
        return hashlib.md5(fingerprint_data.encode('utf-8')).hexdigest()
            
    def init_index(self) -> None:
        """Initialize the Pinecone index if it doesn't exist."""
        # Check if our index already exists
        if self.index_name not in self.pc.list_indexes().names():
            # Create a new index
            self.pc.create_index(
                name=self.index_name,
                dimension=1536,  # OpenAI embeddings dimension
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1")
            )
            logger.info(f"Created new Pinecone index: {self.index_name}")
        else:
            logger.info(f"Using existing Pinecone index: {self.index_name}")
            
    def get_unprocessed_files(self) -> List[str]:
        """Get a list of unprocessed Parquet files."""
        all_files = glob.glob(os.path.join(VECTORS_DIR, "*.parquet"))
        unprocessed_files = [f for f in all_files if f not in self.upload_status]
        return unprocessed_files
        
    async def _check_vector_exists(self, index, vector_id: str) -> bool:
        """Check if a vector already exists in Pinecone."""
        try:
            response = index.fetch(ids=[vector_id])
            return vector_id in response.get('vectors', {})
        except Exception as e:
            logger.warning(f"Error checking if vector {vector_id} exists: {e}")
            return False
            
    def _filter_existing_vectors(self, vectors: List[Dict]) -> Tuple[List[Dict], int]:
        """Filter out vectors that already exist in Pinecone based on fingerprints."""
        if self.skip_deduplication:
            return vectors, 0
            
        new_vectors = []
        duplicate_count = 0
        
        for vector in vectors:
            vector_id = vector['id']
            fingerprint = self._generate_vector_fingerprint(vector)
            
            # Check if we've seen this fingerprint before
            if vector_id in self.vector_fingerprints:
                # If fingerprint changed, update it and keep the vector
                if self.vector_fingerprints[vector_id] != fingerprint:
                    self.vector_fingerprints[vector_id] = fingerprint
                    new_vectors.append(vector)
                else:
                    # Same fingerprint, skip this vector
                    duplicate_count += 1
            else:
                # New vector ID, add to fingerprints and keep
                self.vector_fingerprints[vector_id] = fingerprint
                new_vectors.append(vector)
                
        return new_vectors, duplicate_count
        
    async def upload_file_async(self, file_path: str) -> bool:
        """
        Upload vectors from a single Parquet file to Pinecone asynchronously.
        """
        try:
            # Load the Parquet file
            df = pd.read_parquet(file_path)
            logger.info(f"Loaded {len(df)} vectors from {file_path}")
            
            # Get the index
            index = self.pc.Index(self.index_name)
            
            # Prepare vectors for upload
            vectors = []
            for _, row in df.iterrows():
                vector = {
                    'id': row['id'],
                    'values': row['values'],
                    'metadata': json.loads(row['metadata'])
                }
                vectors.append(vector)
                
            # Filter out vectors that already exist in Pinecone
            vectors, duplicate_count = self._filter_existing_vectors(vectors)
            
            if duplicate_count > 0:
                logger.info(f"Skipping {duplicate_count} duplicate vectors from {file_path}")
                
            if not vectors:
                logger.info(f"No new vectors to upload from {file_path}")
                self.upload_status[file_path] = True
                self._save_upload_status()
                return True
                
            # Upload vectors in batches with adaptive batch sizing
            total_batches = (len(vectors) + self.batch_size - 1) // self.batch_size
            tasks = []
            
            for i in range(0, len(vectors), self.batch_size):
                batch = vectors[i:i + self.batch_size]
                tasks.append(asyncio.create_task(self._upload_batch_with_retry(index, batch, i//self.batch_size + 1, total_batches, file_path)))
                
            # Wait for all batches to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Save fingerprints after processing
            self._save_vector_fingerprints()
            
            if all(result == True for result in results):
                # Mark file as processed
                self.upload_status[file_path] = True
                self._save_upload_status()
                logger.info(f"Successfully uploaded all vectors from {file_path}")
                return True
            else:
                logger.error(f"Some batches failed for {file_path}")
                return False
                
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")
            return False
            
    async def _upload_batch_with_retry(self, index, batch, batch_num, total_batches, file_path):
        """Upload a batch with exponential backoff retry logic."""
        retries = 0
        wait_time = self.base_wait_time
        
        while retries <= self.max_retries:
            try:
                success = await self._upload_batch(index, batch, batch_num, total_batches, file_path)
                if success:
                    # Update batch size based on success
                    self._adjust_batch_size(True)
                    return True
                else:
                    # Batch failed but not due to exception, adjust batch size down
                    self._adjust_batch_size(False)
            except Exception as e:
                logger.error(f"Error on attempt {retries + 1} for batch {batch_num}: {e}")
                self._adjust_batch_size(False)
                
            # Exponential backoff
            retries += 1
            if retries <= self.max_retries:
                logger.info(f"Retrying batch {batch_num} in {wait_time} seconds (attempt {retries + 1}/{self.max_retries + 1})")
                await asyncio.sleep(wait_time)
                wait_time = min(wait_time * 2, 60)  # Cap at 60 seconds
            
        logger.error(f"Failed to upload batch {batch_num} after {self.max_retries + 1} attempts")
        return False
            
    async def _upload_batch(self, index, batch, batch_num, total_batches, file_path):
        """Helper function to upload a single batch."""
        try:
            index.upsert(vectors=batch)
            logger.info(f"Uploaded batch {batch_num}/{total_batches} from {file_path} (batch size: {len(batch)})")
            return True
        except Exception as e:
            logger.error(f"Error uploading batch {batch_num} from {file_path}: {e}")
            raise e
            
    def _adjust_batch_size(self, success: bool):
        """Adjust batch size based on success or failure."""
        if success:
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            
            # Increase batch size after consecutive successes
            if self.consecutive_successes >= self.success_threshold:
                old_size = self.batch_size
                self.batch_size = min(self.batch_size + 50, self.max_batch_size)
                if old_size != self.batch_size:
                    logger.info(f"Increased batch size to {self.batch_size}")
                self.consecutive_successes = 0
        else:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            
            # Reduce batch size after failure
            if self.consecutive_failures > 0:
                old_size = self.batch_size
                self.batch_size = max(self.batch_size // 2, self.min_batch_size)
                if old_size != self.batch_size:
                    logger.info(f"Reduced batch size to {self.batch_size}")
            
    async def upload_all_async(self, test_mode: bool = False) -> None:
        """Upload all unprocessed files to Pinecone with parallel processing."""
        # Get unprocessed files
        files = self.get_unprocessed_files()
        
        if not files:
            logger.info("No unprocessed files found")
            print("No unprocessed files found")
            return
            
        # Limit files in test mode
        if test_mode and len(files) > 1:
            files = files[:1]
            logger.info(f"Test mode: Processing only 1 file")
            print(f"Test mode: Processing only 1 file")
            
        # Display summary
        print(f"Found {len(files)} unprocessed files")
        print(f"Initial batch size: {self.batch_size}")
        print(f"Max parallel files: {self.max_parallel_files}")
        print(f"Index: {self.index_name}")
        print(f"Deduplication: {'Disabled' if self.skip_deduplication else 'Enabled'}")
        
        # Initialize index
        self.init_index()
        
        # Process files in parallel with limit
        for i in range(0, len(files), self.max_parallel_files):
            batch_files = files[i:i + self.max_parallel_files]
            tasks = [self.upload_file_async(file) for file in batch_files]
            results = await asyncio.gather(*tasks)
            
            for file, success in zip(batch_files, results):
                if success:
                    print(f"Successfully uploaded {os.path.basename(file)}")
                else:
                    print(f"Failed to upload {os.path.basename(file)}")
                    
        # Show summary
        total_processed = sum(1 for status in self.upload_status.values() if status)
        fingerprint_count = len(self.vector_fingerprints)
        print(f"\nUploaded {total_processed} files to Pinecone")
        print(f"Total vector fingerprints tracked: {fingerprint_count}")
        print(f"Final batch size: {self.batch_size}")
        print(f"Index: {self.index_name}")

def main():
    """Parse arguments and run the uploader."""
    parser = argparse.ArgumentParser(description="Upload vectors to Pinecone")
    parser.add_argument('--batch-size', type=int, default=500,
                      help="Number of vectors to upload in each batch (default: 500)")
    parser.add_argument('--test', action='store_true',
                      help="Run in test mode, only process 1 file")
    parser.add_argument('--skip-deduplication', action='store_true',
                      help="Skip vector deduplication")
    args = parser.parse_args()
    
    uploader = PineconeUploader(batch_size=args.batch_size, 
                               skip_deduplication=args.skip_deduplication)
    asyncio.run(uploader.upload_all_async(test_mode=args.test))

if __name__ == "__main__":
    main() 