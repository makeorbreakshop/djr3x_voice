#!/usr/bin/env python3
"""
Upload to Pinecone

This script uploads locally processed vectors to Pinecone:
1. Reads vectors from Parquet files in data/vectors
2. Uploads them to Pinecone in batches
3. Tracks upload progress

Usage:
    python scripts/upload_to_pinecone.py [--batch-size N] [--test]
"""

import os
import sys
import json
import argparse
import asyncio
import logging
import time
import glob
from datetime import datetime
from typing import List, Dict, Any, Optional
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

class PineconeUploader:
    """Handles uploading vectors to Pinecone."""
    
    def __init__(self, batch_size: int = 500):
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
        self.batch_size = batch_size
        self.max_parallel_files = 3  # Process up to 3 files in parallel
        
        # Initialize upload status
        self.upload_status = self._load_upload_status()
        
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
                
            # Upload vectors in larger batches with parallel processing
            total_batches = (len(vectors) + self.batch_size - 1) // self.batch_size
            tasks = []
            
            for i in range(0, len(vectors), self.batch_size):
                batch = vectors[i:i + self.batch_size]
                tasks.append(asyncio.create_task(self._upload_batch(index, batch, i//self.batch_size + 1, total_batches, file_path)))
                
            # Wait for all batches to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
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
            
    async def _upload_batch(self, index, batch, batch_num, total_batches, file_path):
        """Helper function to upload a single batch."""
        try:
            index.upsert(vectors=batch)
            logger.info(f"Uploaded batch {batch_num}/{total_batches} from {file_path}")
            return True
        except Exception as e:
            logger.error(f"Error uploading batch {batch_num} from {file_path}: {e}")
            return False
            
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
        print(f"Batch size: {self.batch_size}")
        print(f"Max parallel files: {self.max_parallel_files}")
        print(f"Index: {self.index_name}")
        
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
        print(f"\nUploaded {total_processed} files to Pinecone")
        print(f"Index: {self.index_name}")

def main():
    """Parse arguments and run the uploader."""
    parser = argparse.ArgumentParser(description="Upload vectors to Pinecone")
    parser.add_argument('--batch-size', type=int, default=500,
                      help="Number of vectors to upload in each batch (default: 500)")
    parser.add_argument('--test', action='store_true',
                      help="Run in test mode, only process 1 file")
    args = parser.parse_args()
    
    uploader = PineconeUploader(batch_size=args.batch_size)
    asyncio.run(uploader.upload_all_async(test_mode=args.test))

if __name__ == "__main__":
    main() 