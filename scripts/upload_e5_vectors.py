#!/usr/bin/env python3
"""
Upload locally saved E5 vectors to Pinecone.

This script:
1. Reads vectors from local JSONL files
2. Uploads them to the specified Pinecone index
3. Handles batching and error recovery
"""

import os
import json
import argparse
import logging
import time
from typing import List, Dict, Any
import traceback
from datetime import datetime
from tqdm import tqdm
import glob

# Pinecone import
from pinecone import Pinecone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"e5_upload_{datetime.now().strftime('%Y%m%d%H%M%S')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class E5VectorUploader:
    """Uploads E5 vectors from local files to Pinecone."""
    
    def __init__(
        self,
        input_dir: str,
        target_index: str,
        target_namespace: str = "",
        batch_size: int = 100,
        debug: bool = False
    ):
        """
        Initialize the uploader.
        
        Args:
            input_dir: Directory containing the vector files
            target_index: Name of the target Pinecone index
            target_namespace: Namespace in target index
            batch_size: Number of vectors to upload in each batch
            debug: Whether to enable debug mode
        """
        self.input_dir = input_dir
        self.target_index = target_index
        self.target_namespace = target_namespace
        self.batch_size = batch_size
        self.debug = debug
        self.checkpoint_file = "e5_upload_checkpoint.json"
        self.processed_files = set()
        
        # Initialize Pinecone
        self.pc = Pinecone()
        self.target_idx = self.pc.Index(target_index)
        
        # Statistics
        self.stats = {
            "total_vectors_uploaded": 0,
            "successful_upserts": 0,
            "failed_vectors": 0,
            "start_time": time.time(),
            "session_start_time": time.time(),
            "processed_in_session": 0,
            "wu_count": 0,
        }
        
        # Load checkpoint if exists
        self._load_checkpoint()
    
    def _load_checkpoint(self):
        """Load processed files from checkpoint file if it exists."""
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, 'r') as f:
                    checkpoint = json.load(f)
                    self.processed_files = set(checkpoint.get('processed_files', []))
                logger.info(f"Loaded {len(self.processed_files)} processed files from checkpoint")
                # Print some sample files from the checkpoint
                sample_files = list(self.processed_files)[:5] if self.processed_files else []
                logger.info(f"Sample files from checkpoint: {sample_files}")
            except Exception as e:
                logger.error(f"Error loading checkpoint file: {e}")
                logger.error(traceback.format_exc())
                self.processed_files = set()
    
    def _save_checkpoint(self):
        """Save current progress to checkpoint file."""
        with open(self.checkpoint_file, 'w') as f:
            json.dump({
                'processed_files': list(self.processed_files),
                'timestamp': datetime.now().isoformat()
            }, f)
    
    def upload(self, test: bool = False):
        """
        Upload vectors to Pinecone.
        
        Args:
            test: Whether to run in test mode (process only a few vectors)
        """
        logger.info(f"Starting upload to {self.target_index} namespace {self.target_namespace or '[default]'}")
        logger.info(f"Using batch size: {self.batch_size}")
        
        # Get list of vector files
        vector_files = glob.glob(os.path.join(self.input_dir, "*.jsonl"))
        
        # Filter out already processed files
        unprocessed_files = [f for f in vector_files if os.path.basename(f) not in self.processed_files]
        
        if not unprocessed_files:
            logger.info("No new files to process")
            return
        
        logger.info(f"Found {len(unprocessed_files)} unprocessed files out of {len(vector_files)} total")
        
        # If test mode, only process a small number of files
        if test:
            logger.info("Test mode enabled - processing only a few files")
            unprocessed_files = unprocessed_files[:min(2, len(unprocessed_files))]
        
        # Process each file
        for file_path in unprocessed_files:
            file_name = os.path.basename(file_path)
            logger.info(f"Processing file: {file_name}")
            
            vectors = []
            current_batch = []
            
            # Open and process the file
            with open(file_path, 'r') as f:
                lines = f.readlines()
                logger.info(f"File contains {len(lines)} lines")
                
                # Process each line
                for i, line in enumerate(tqdm(lines, desc=f"Processing {file_name}")):
                    try:
                        vector_data = json.loads(line.strip())
                        current_batch.append(vector_data)
                        
                        # When batch is full, upload it
                        if len(current_batch) >= self.batch_size:
                            self._upload_batch(current_batch)
                            self.stats["processed_in_session"] += len(current_batch)
                            current_batch = []
                            
                            # Print progress
                            if i % (self.batch_size * 10) == 0:
                                session_elapsed = time.time() - self.stats["session_start_time"]
                                current_rate = self.stats["processed_in_session"] / session_elapsed if session_elapsed > 0 else 0
                                logger.info(f"Progress: {i}/{len(lines)} vectors. Rate: {current_rate:.2f} v/sec")
                            
                            # Save checkpoint occasionally
                            if i % (self.batch_size * 50) == 0:
                                self._save_checkpoint()
                    except Exception as e:
                        logger.error(f"Error processing line {i} in file {file_name}: {e}")
                        self.stats["failed_vectors"] += 1
                
                # Upload any remaining vectors
                if current_batch:
                    self._upload_batch(current_batch)
                    self.stats["processed_in_session"] += len(current_batch)
            
            # Mark file as processed
            self.processed_files.add(file_name)
            self._save_checkpoint()
            
            # Print file completion stats
            logger.info(f"Completed processing {file_name}")
            self._print_progress()
        
        # Print final results
        self._print_final_results()
    
    def _upload_batch(self, batch: List[Dict[str, Any]]):
        """Upload a batch of vectors to Pinecone."""
        if not batch:
            return
        
        if self.debug:
            logger.info(f"Debug: Uploading batch of {len(batch)} vectors")
            logger.info(f"Sample vector metadata: {batch[0].get('metadata', {})}")
            # Check for required fields
            if 'id' not in batch[0] or 'values' not in batch[0]:
                logger.error(f"Missing required fields in vector: {batch[0].keys()}")
                return
        
        try:
            if self.debug:
                # Print sample vector structure in debug mode
                sample = batch[0]
                logger.info(f"Sample vector: id={sample['id']}, values_length={len(sample['values'])}, metadata_keys={list(sample.get('metadata', {}).keys())}")
            
            # Upload to Pinecone
            response = self.target_idx.upsert(
                vectors=batch,
                namespace=self.target_namespace
            )
            
            # Update statistics
            upserted = getattr(response, "upserted_count", len(batch))
            self.stats["successful_upserts"] += upserted
            self.stats["wu_count"] += len(batch)
            
            if self.debug:
                logger.info(f"Upserted {upserted} vectors to Pinecone")
        except Exception as e:
            logger.error(f"Error upserting batch to Pinecone: {e}")
            logger.error(traceback.format_exc())
            self.stats["failed_vectors"] += len(batch)
            # Wait before retrying
            time.sleep(5)
    
    def _print_progress(self):
        """Print current upload progress."""
        session_elapsed_time = time.time() - self.stats["session_start_time"]
        total_elapsed_time = time.time() - self.stats["start_time"]
        
        session_rate = self.stats["processed_in_session"] / session_elapsed_time if session_elapsed_time > 0 else 0
        overall_rate = self.stats["successful_upserts"] / total_elapsed_time if total_elapsed_time > 0 else 0
        
        logger.info(
            f"Total Vectors Uploaded: {self.stats['successful_upserts']} | "
            f"This Session: {self.stats['processed_in_session']} | "
            f"Rate: {session_rate:.2f} vectors/sec"
        )
        
        # Estimated cost based on Write Units
        est_wu_cost = self.stats["wu_count"] * 2.00 / 1_000_000  # $2.00 per million WUs
        
        logger.info(
            f"Write Units: {self.stats['wu_count']} (est. ${est_wu_cost:.2f}) | "
            f"Failed Vectors: {self.stats['failed_vectors']}"
        )
    
    def _print_final_results(self):
        """Print final upload results."""
        elapsed_time = time.time() - self.stats["start_time"]
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        logger.info(f"Upload completed in {int(hours)}h {int(minutes)}m {seconds:.2f}s")
        logger.info(f"Total Vectors Uploaded: {self.stats['successful_upserts']}")
        logger.info(f"Failed Vectors: {self.stats['failed_vectors']}")
        
        # Estimated cost based on Write Units
        est_wu_cost = self.stats["wu_count"] * 2.00 / 1_000_000  # $2.00 per million WUs
        
        logger.info(f"Total Write Units: {self.stats['wu_count']} (est. ${est_wu_cost:.2f})")
    
    def list_files(self):
        """List all vector files in the input directory."""
        vector_files = glob.glob(os.path.join(self.input_dir, "*.jsonl"))
        unprocessed_files = [f for f in vector_files if os.path.basename(f) not in self.processed_files]
        
        logger.info(f"Total files: {len(vector_files)}")
        logger.info(f"Processed files: {len(self.processed_files)}")
        logger.info(f"Unprocessed files: {len(unprocessed_files)}")
        
        if unprocessed_files:
            logger.info("Unprocessed files:")
            for f in unprocessed_files:
                logger.info(f"  - {os.path.basename(f)} ({os.path.getsize(f) / (1024*1024):.2f} MB)")

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Upload locally saved E5 vectors to Pinecone")
    
    parser.add_argument("--input-dir", type=str, default="e5_vectors", help="Directory containing vector files")
    parser.add_argument("--target-index", type=str, default="holocron-sbert-e5", help="Target index name")
    parser.add_argument("--target-namespace", type=str, default="", help="Target namespace")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for uploads")
    parser.add_argument("--test", action="store_true", help="Run in test mode with a small batch")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--list-files", action="store_true", help="List available vector files")
    
    args = parser.parse_args()
    
    logger.info(f"Starting upload from '{args.input_dir}' to '{args.target_index}'")
    logger.info(f"Batch size: {args.batch_size}")
    if args.test:
        logger.info("Test mode enabled - will process only a small batch")
    if args.debug:
        logger.info("Debug mode enabled - will show verbose logs")
    
    # Create and run uploader
    uploader = E5VectorUploader(
        input_dir=args.input_dir,
        target_index=args.target_index,
        target_namespace=args.target_namespace,
        batch_size=args.batch_size,
        debug=args.debug
    )
    
    if args.list_files:
        uploader.list_files()
        return
    
    uploader.upload(test=args.test)

if __name__ == "__main__":
    main() 