#!/usr/bin/env python3
"""
Migrate vectors from holocron-knowledge (OpenAI embeddings) to holocron-sbert-e5 (E5 embeddings).

This script:
1. Uses Pinecone's list() and fetch() methods to efficiently retrieve vectors
2. Extracts text content from metadata
3. Generates new E5 embeddings using the intfloat/e5-small-v2 model
4. Upserts the new embeddings to holocron-sbert-e5 index
5. Uses SQLite for robust progress tracking
"""

import os
import time
import argparse
import logging
from typing import List, Dict, Any, Generator, Optional, Tuple, Set
import traceback
import json
from datetime import datetime
from tqdm import tqdm
import numpy as np
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
import torch
import sqlite3
import gc
import pinecone
from sentence_transformers import SentenceTransformer
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import psutil
from pinecone import Pinecone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class E5Migrator:
    """Migrates vectors from OpenAI embeddings to E5 embeddings in Pinecone."""
    
    def __init__(
        self,
        source_index: str,
        target_index: str,
        source_namespace: str = "",
        target_namespace: str = "",
        model_name: str = "intfloat/e5-small-v2",
        batch_size: int = 1000,
        num_workers: int = 4,
        save_locally: bool = False,
        output_dir: str = "e5_vectors",
        force: bool = False,
        vectors_per_file: int = 5000
    ):
        """Initialize the E5 migration tool"""
        self.source_index = source_index
        self.target_index = target_index
        self.source_namespace = source_namespace
        self.target_namespace = target_namespace
        self.model_name = model_name
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.save_locally = save_locally
        self.output_dir = output_dir
        self.force = force
        self.vectors_per_file = vectors_per_file
        
        # Initialize Pinecone clients
        pc = Pinecone()  # Uses API key from environment
        self.source_idx = pc.Index(source_index)
        if not save_locally:
            self.target_idx = pc.Index(target_index)
        
        # Ensure output directory exists
        if save_locally:
            os.makedirs(output_dir, exist_ok=True)
            
        # Initialize the model
        self.model = SentenceTransformer(model_name)
        
        # Initialize stats
        self.session_stats = {
            'processed': 0,
            'errors': 0,
            'saved_locally': 0,
            'uploaded': 0,
            'start_time': time.time()
        }
        
        # Set up SQLite database - more robust than JSON checkpoints
        self.db_path = "e5_migration.db"
        self._init_db()
        
        # Stats tracking
        self.total_processed = 0
        self.total_errors = 0
        self.current_file_count = 0
        self.current_file_vectors = 0
        
        # Optimize model settings
        self.model.max_seq_length = 512  # Increase max sequence length
        self.model.encode_kwargs = {
            'batch_size': batch_size,
            'show_progress_bar': False,
            'normalize_embeddings': True,  # Pre-normalize embeddings
            'convert_to_tensor': True  # Keep tensors on device
        }
        
        if torch.backends.mps.is_available():
            self.device = torch.device("mps")
            self.model = self.model.to(self.device)
            
        # Enable mixed precision for faster inference
        if torch.cuda.is_available():
            self.model.half()  # Convert to FP16 for faster inference
            
        # Increase processing batch sizes
        self.process_batch_size = min(5000, batch_size)  # Increased from original
        self.vector_batch_size = min(2000, batch_size)   # For vector processing
        
        # Statistics
        self.stats = {
            "total_vectors_on_source": 0,
            "processed_ids_cumulative": self._get_processed_count(),
            "successful_upserts": 0,
            "failed_ids": 0,
            "empty_content_ids": 0,
            "start_time": time.time(),
            "ru_count": 0,
            "wu_count": 0,
            "session_start_time": time.time(),
            "processed_in_session": 0,
        }

        try:
            source_stats = self.source_idx.describe_index_stats()
            # Ensure namespace access is safe
            namespace_stats = source_stats.namespaces.get(self.source_namespace)
            if namespace_stats:
                self.stats["total_vectors_on_source"] = namespace_stats.vector_count
            else:
                self.stats["total_vectors_on_source"] = 0 # Namespace might be empty or not exist
            
            # Pre-calculate the namespace string for logging to avoid complex f-string expression
            ns_display_for_log = self.source_namespace or "[default]"
            logger.info(f"Source index '{self.source_index}' (ns: '{ns_display_for_log}') has {self.stats['total_vectors_on_source']} total vectors.")
        except Exception as e:
            logger.warning(f"Could not get source index stats: {e}. Progress bar total may be based on --limit only.")
            self.stats["total_vectors_on_source"] = 0

    def _init_db(self):
        """Initialize SQLite database for tracking processed vectors"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Create tables if they don't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS processed_vectors (
                        vector_id TEXT PRIMARY KEY,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Add index to speed up lookups
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS processed_vectors_idx 
                    ON processed_vectors(vector_id)
                """)
                
                # Create a batches table for checkpoint tracking
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS migration_batches (
                        batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        batch_size INTEGER,
                        first_id TEXT,
                        last_id TEXT,
                        status TEXT
                    )
                """)
                
                # Add stats table for global metrics
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS migration_stats (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                conn.commit()
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
            
    def _is_processed(self, vector_id: str) -> bool:
        """Check if a vector has been processed"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM processed_vectors WHERE vector_id = ?", (vector_id,))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Error checking processed status: {e}")
            return False
            
    def _mark_processed(self, vector_ids: List[str]):
        """Mark vectors as processed in the database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Use executemany for better performance
                cursor.executemany(
                    "INSERT OR IGNORE INTO processed_vectors (vector_id) VALUES (?)",
                    [(vid,) for vid in vector_ids]
                )
                conn.commit()
                
                # Update overall stats
                self.stats["processed_in_session"] += len(vector_ids)
                self.stats["processed_ids_cumulative"] = self._get_processed_count()
        except Exception as e:
            logger.error(f"Error marking vectors as processed: {e}")
            
    def _get_processed_count(self) -> int:
        """Get total count of processed vectors"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM processed_vectors")
                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error getting processed count: {e}")
            return 0
            
    def extract_text_content(self, vector_data) -> Optional[str]:
        """
        Extract text content from vector metadata.
        Handles both dictionary-style vector data and Pinecone Vector objects.
        
        Args:
            vector_data: Vector data including metadata (can be dict or Pinecone Vector object)
            
        Returns:
            Extracted text content or None if not found
        """
        vector_id = None
        metadata = None
        
        # Handle Pinecone Vector objects from API response
        if hasattr(vector_data, 'id'):
            vector_id = vector_data.id
            metadata = vector_data.metadata if hasattr(vector_data, 'metadata') else None
        else:
            # Handle dictionary format
            vector_id = vector_data.get("id", "unknown")
            metadata = vector_data.get("metadata", {})
        
        # Try to find content in various fields
        if metadata is None:
            logger.warning(f"No metadata found in vector {vector_id}")
            self.stats["empty_content_ids"] += 1
            return None
        
        # Try to convert metadata to a dictionary if it's not already
        if not isinstance(metadata, dict):
            try:
                # If metadata is an object with attributes
                metadata_dict = {}
                for attr in dir(metadata):
                    # Skip private attributes and methods
                    if not attr.startswith('_') and not callable(getattr(metadata, attr)):
                        metadata_dict[attr] = getattr(metadata, attr)
                metadata = metadata_dict
            except Exception as e:
                logger.warning(f"Could not convert metadata to dictionary for vector {vector_id}: {e}")
                metadata = {}
        
        # Check for common content fields
        content_fields = ["content", "text", "article_text", "chunk_text", "passage", "document", 
                         "body", "main_text", "full_text", "description"]
        
        for field in content_fields:
            content = None
            # Try direct access
            if isinstance(metadata, dict) and field in metadata and metadata[field]:
                content = metadata[field]
            # Try attribute access
            elif hasattr(metadata, field) and getattr(metadata, field):
                content = getattr(metadata, field)
                
            if content:
                # Ensure content is a string
                if not isinstance(content, str):
                    try:
                        content = str(content)
                    except Exception as e:
                        logger.warning(f"Could not convert content to string: {e}")
                        continue
                
                # Limit content length to avoid extremely large texts
                if len(content) > 10000:
                    logger.debug(f"Truncating very long content from {len(content)} to 10000 chars")
                    content = content[:10000]
                
                return content
        
        # If no content found in primary fields, try secondary approach with likely text fields
        if isinstance(metadata, dict):
            # Look for any field that has a string value of reasonable length
            for k, v in metadata.items():
                if isinstance(v, str) and len(v) > 50:  # Reasonable length for content
                    logger.debug(f"Found potential content in field '{k}'")
                    return v[:10000]  # Limit length for safety
        
        # If no content found, log error
        logger.warning(f"No content found in vector {vector_id}.")
        self.stats["empty_content_ids"] += 1
        return None
    

    
    def process_batch(self, batch_data: List) -> List[Dict[str, Any]]:
        """Process a batch of vectors"""
        processed_vectors = []
        error_count = 0
        
        if not batch_data:
            logger.warning("Empty batch received")
            return []
            
        try:
            # Extract text content for the batch
            texts = []
            valid_indices = []
            valid_vector_ids = []
            valid_metadata = []
            
            # First pass: extract the text content from each vector
            for i, vector_data in enumerate(batch_data):
                # Debug logging to check vector structure
                if i == 0:
                    logger.debug(f"Sample vector data structure: {type(vector_data)}")
                
                # Get vector ID - handle both dict and Pinecone Vector objects
                vector_id = getattr(vector_data, 'id', None) if hasattr(vector_data, 'id') else vector_data.get('id')
                if not vector_id:
                    logger.warning(f"Vector at index {i} has no ID, skipping")
                    error_count += 1
                    continue
                
                # Extract text content
                text = self.extract_text_content(vector_data)
                if text:
                    texts.append(text)
                    valid_indices.append(i)
                    valid_vector_ids.append(vector_id)
                    
                    # Get metadata - handle both dict and Pinecone Vector objects
                    if hasattr(vector_data, 'metadata'):
                        metadata = vector_data.metadata
                    else:
                        metadata = vector_data.get('metadata', {})
                    
                    # Ensure metadata is a dict
                    if not isinstance(metadata, dict):
                        metadata = {
                            "original_content": str(metadata),
                            "converted_from_non_dict": True
                        }
                    
                    # Add embedding model info to metadata
                    metadata['embedding_model'] = self.model_name
                    valid_metadata.append(metadata)
                else:
                    error_count += 1
                    logger.warning(f"Could not extract text from vector {vector_id}")
            
            if not texts:
                logger.warning("No valid texts found in batch")
                return []
            
            # Batch create embeddings for valid texts
            logger.debug(f"Creating embeddings for {len(texts)} texts")
            try:
                # Use the model to encode all texts at once
                embeddings = self.model.encode(
                    texts,
                    batch_size=min(len(texts), 32),  # Reasonable batch size
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    normalize_embeddings=True
                )
                
                if isinstance(embeddings, list) and len(embeddings) != len(texts):
                    logger.warning(f"Embedding count mismatch: got {len(embeddings)}, expected {len(texts)}")
                
                logger.debug(f"Created {len(embeddings)} embeddings")
                
                # Verify embedding dimensions
                if len(embeddings) > 0:
                    first_dim = embeddings[0].shape[0] if hasattr(embeddings[0], 'shape') else len(embeddings[0])
                    logger.debug(f"Embedding dimensions: {first_dim}")
                    if first_dim != 384:  # Expected dimension for E5-small-v2
                        logger.warning(f"Unexpected embedding dimension: {first_dim} (expected 384)")
                
            except Exception as e:
                logger.error(f"Error creating embeddings: {e}")
                logger.error(traceback.format_exc())
                return []
            
            # Create vector records with both 'values' and 'embedding' fields
            for i, (vector_id, embedding, metadata) in enumerate(zip(valid_vector_ids, embeddings, valid_metadata)):
                # Convert to list for serialization
                embedding_list = embedding.tolist() if hasattr(embedding, 'tolist') else list(embedding)
                
                vector_dict = {
                    'id': vector_id,
                    'values': embedding_list,      # For Pinecone
                    'embedding': embedding_list,   # For local storage consistency
                    'metadata': metadata
                }
                processed_vectors.append(vector_dict)
            
            logger.debug(f"Processed {len(processed_vectors)} vectors with {error_count} errors")
            return processed_vectors
            
        except Exception as e:
            logger.error(f"Error processing batch: {e}")
            logger.error(traceback.format_exc())
            return []
    
    def save_vectors_locally(self, vectors: List[Dict[str, Any]]) -> int:
        """Save vectors to local Parquet files with proper batching and error handling"""
        if not vectors:
            return 0

        # Debug vector structure before saving
        if vectors:
            logger.debug(f"First vector ID: {vectors[0]['id']}")
            logger.debug(f"First vector values type: {type(vectors[0]['values'])}")
            logger.debug(f"First vector values length: {len(vectors[0]['values'])}")
            logger.debug(f"First vector metadata keys: {list(vectors[0]['metadata'].keys())}")
        
        # Convert vectors to DataFrame with proper structure
        rows = []
        for vector in vectors:
            # Ensure we have both 'values' and 'embedding' fields
            if 'values' not in vector and 'embedding' in vector:
                vector['values'] = vector['embedding']
            elif 'embedding' not in vector and 'values' in vector:
                vector['embedding'] = vector['values']
            elif not ('values' in vector or 'embedding' in vector):
                logger.warning(f"Vector {vector.get('id', 'unknown')} missing both 'values' and 'embedding' fields")
                continue
                
            # Verify vector dimensions
            embedding = vector.get('embedding', vector.get('values', []))
            if len(embedding) != 384:
                logger.warning(f"Vector {vector.get('id', 'unknown')} has incorrect dimensions: {len(embedding)} (expected 384)")
                continue
            
            # Ensure metadata is a dictionary
            if 'metadata' not in vector or vector['metadata'] is None:
                vector['metadata'] = {}
            
            # Add source tracking to metadata
            vector['metadata']['migrated_at'] = datetime.now().isoformat()
            vector['metadata']['source_index'] = self.source_index
            vector['metadata']['target_index'] = self.target_index
            
            row = {
                'id': vector['id'],
                'embedding': vector['embedding'],
                'metadata': vector['metadata']
            }
            rows.append(row)
        
        if not rows:
            logger.warning("No valid rows to save")
            return 0
        
        try:
            # Create output directory if it doesn't exist
            os.makedirs(self.output_dir, exist_ok=True)
            
            # Check if we should start a new file based on vectors_per_file setting
            files = sorted(os.listdir(self.output_dir))
            if not files or self.current_file_vectors >= self.vectors_per_file:
                # Generate unique filename based on timestamp and batch number
                self.current_file_vectors = 0
                self.current_file_count += 1
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"vectors_{timestamp}_{self.current_file_count:04d}.parquet"
            else:
                # Use the most recent file if it's not at capacity
                filename = files[-1]
                
            filepath = os.path.join(self.output_dir, filename)
            
            # Create DataFrame
            df = pd.DataFrame(rows)
            
            # Check if we're appending to an existing file and it exists
            if os.path.exists(filepath) and self.current_file_vectors > 0:
                # If file exists, read it first to ensure schema compatibility
                try:
                    existing_df = pd.read_parquet(filepath)
                    # Append new data
                    df = pd.concat([existing_df, df], ignore_index=True)
                    logger.debug(f"Appended {len(rows)} vectors to existing file {filepath}")
                except Exception as e:
                    logger.warning(f"Error reading existing file {filepath}, creating new file: {e}")
                    # Generate a new filename since we couldn't append
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = f"vectors_{timestamp}_{self.current_file_count:04d}.parquet"
                    filepath = os.path.join(self.output_dir, filename)
            
            # Save to Parquet file with compression
            df.to_parquet(filepath, index=False, compression='snappy')
            
            # Update tracking variables
            self.current_file_vectors += len(rows)
            
            logger.info(f"Saved {len(rows)} vectors to {filepath}")
            
            # Mark these vectors as processed in the database
            vector_ids = [row['id'] for row in rows]
            self._mark_processed(vector_ids)
            
            return len(rows)
            
        except Exception as e:
            logger.error(f"Error saving vectors locally: {e}")
            logger.error(traceback.format_exc())
            return 0
    
    def migrate(self, limit: Optional[int] = None, memory_limit: int = 0):
        """
        Migrate vectors from source to target index
        
        Args:
            limit: Optional limit on number of vectors to process
            memory_limit: Memory threshold in MB to trigger garbage collection
        """
        try:
            logger.info("Starting migration process...")
            logger.info(f"Mode: {'Saving locally' if self.save_locally else 'Uploading to Pinecone'}")
            
            # Initialize SQLite database for tracking
            self._init_db()
            
            # Get total processed count
            processed_count = self._get_processed_count()
            logger.info(f"Found {processed_count} previously processed vectors")
            
            # Process vectors in batches
            batch_number = 0
            total_processed = 0
            
            # Use list and fetch instead of non-existent fetch_vectors
            for list_batch in self.source_idx.list(namespace=self.source_namespace):
                try:
                    batch_number += 1
                    
                    # Check memory usage and perform garbage collection if needed
                    if memory_limit > 0:
                        memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
                        if memory_mb > memory_limit:
                            logger.warning(f"Memory usage ({memory_mb:.1f}MB) exceeded limit ({memory_limit}MB)")
                            gc.collect()
                    
                    # Directly use the list_batch since it's already a list of IDs
                    vector_ids = list_batch
                    logger.info(f"Batch {batch_number}: Found {len(vector_ids)} IDs from Pinecone")
                    
                    # Skip already processed IDs
                    unprocessed_ids = []
                    for vid in vector_ids:
                        if not self._is_processed(vid):
                            unprocessed_ids.append(vid)
                    
                    if not unprocessed_ids:
                        logger.info(f"Batch {batch_number}: All {len(vector_ids)} vectors already processed, skipping")
                        continue
                        
                    logger.info(f"Batch {batch_number}: Processing {len(unprocessed_ids)} unprocessed vectors")
                    
                    # Fetch full vector data for unprocessed IDs
                    if unprocessed_ids:
                        # Process in sub-batches to avoid API limits
                        for i in range(0, len(unprocessed_ids), self.batch_size):
                            sub_batch_ids = unprocessed_ids[i:i + self.batch_size]
                            
                            try:
                                logger.debug(f"Fetching {len(sub_batch_ids)} vectors")
                                response = self.source_idx.fetch(
                                    ids=sub_batch_ids,
                                    namespace=self.source_namespace
                                )
                                
                                if not response or not hasattr(response, 'vectors') or not response.vectors:
                                    logger.warning(f"Empty response or no vectors returned for batch {batch_number}")
                                    continue
                                
                                # Get vectors from the response
                                batch_data = list(response.vectors.values())
                                logger.info(f"Batch {batch_number}: Fetched {len(batch_data)} vectors")
                                
                                # Process the vectors
                                processed_vectors = self.process_batch(batch_data)
                                if not processed_vectors:
                                    logger.warning("No vectors processed in batch")
                                    continue
                                
                                # Save locally if configured
                                if self.save_locally:
                                    saved_count = self.save_vectors_locally(processed_vectors)
                                    self.session_stats['saved_locally'] += saved_count
                                else:
                                    # Upload to Pinecone
                                    try:
                                        # Prepare vectors for Pinecone (use 'values' field)
                                        pinecone_vectors = []
                                        for v in processed_vectors:
                                            if 'values' not in v:
                                                # Ensure values field exists for Pinecone
                                                v['values'] = v.get('embedding', [])
                                            
                                            pinecone_vectors.append({
                                                'id': v['id'],
                                                'values': v['values'],
                                                'metadata': v['metadata']
                                            })
                                        
                                        # Upload batch to Pinecone
                                        upsert_response = self.target_idx.upsert(
                                            vectors=pinecone_vectors,
                                            namespace=self.target_namespace
                                        )
                                        self.session_stats['uploaded'] += len(pinecone_vectors)
                                        # Increment successful_upserts based on Pinecone's response
                                        if upsert_response and hasattr(upsert_response, 'upserted_count') and upsert_response.upserted_count:
                                            self.stats['successful_upserts'] += upsert_response.upserted_count
                                            # Basic WU estimation: 1 WU per vector upserted (this is a simplification)
                                            self.stats['wu_count'] += upsert_response.upserted_count
                                        else:
                                            # If no count, assume all were attempted for WU, but log a warning for successful_upserts
                                            self.stats['wu_count'] += len(pinecone_vectors) # Estimate based on attempt
                                            logger.warning(f"Pinecone upsert response did not contain upserted_count. Cannot confirm exact number of successful upserts for this batch.")

                                        logger.info(f"Uploaded {len(pinecone_vectors)} vectors to Pinecone (Confirmed: {upsert_response.upserted_count if upsert_response and hasattr(upsert_response, 'upserted_count') else 'N/A'})")
                                    except Exception as e:
                                        logger.error(f"Error uploading to Pinecone: {e}")
                                        self.session_stats['errors'] += 1
                                
                                # Mark vectors as processed and update stats
                                processed_ids = [v['id'] for v in processed_vectors]
                                self._mark_processed(processed_ids)
                                
                                self.session_stats['processed'] += len(processed_vectors)
                                total_processed += len(processed_vectors)
                                
                                # Print progress
                                self._print_progress()
                                
                                # Check limit
                                if limit and total_processed >= limit:
                                    logger.info(f"Reached processing limit of {limit} vectors")
                                    break
                                    
                            except Exception as e:
                                logger.error(f"Error processing sub-batch: {e}")
                                self.session_stats['errors'] += 1
                                continue
                            
                    # Check limit after each batch
                    if limit and total_processed >= limit:
                        break
                    
                except KeyboardInterrupt:
                    logger.info("Received interrupt signal, finishing current batch...")
                    break
                except Exception as e:
                    logger.error(f"Error processing batch: {e}")
                    self.session_stats['errors'] += 1
                    continue
            
            # Print final results
            self._print_final_results()
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise
    
    def _print_progress(self):
        """Print current migration progress."""
        session_elapsed_time = time.time() - self.stats["session_start_time"]
        total_script_elapsed_time = time.time() - self.stats["start_time"]

        session_rate_ids_per_sec = self.stats["processed_in_session"] / session_elapsed_time if session_elapsed_time > 0 else 0
        overall_avg_ids_per_sec = self.stats["processed_ids_cumulative"] / total_script_elapsed_time if total_script_elapsed_time > 0 else 0
        
        logger.info(
            f"Total Unique Processed (Overall): {self.stats['processed_ids_cumulative']} | "
            f"This Session: {self.stats['processed_in_session']} | "
            f"Rate: {session_rate_ids_per_sec:.2f} vectors/sec"
        )
        
        # Estimated costs based on RUs and WUs
        est_ru_cost = self.stats["ru_count"] * 8.25 / 1_000_000  # $8.25 per million RUs
        est_wu_cost = self.stats["wu_count"] * 2.00 / 1_000_000  # $2.00 per million WUs
        
        logger.info(
            f"Read Units: {self.stats['ru_count']} (est. ${est_ru_cost:.2f}) | "
            f"Write Units: {self.stats['wu_count']} (est. ${est_wu_cost:.2f}) | "
            f"Total est. cost: ${est_ru_cost + est_wu_cost:.2f}"
        )
    
    def _print_final_results(self):
        """Print final migration results."""
        elapsed_time = time.time() - self.stats["start_time"]
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        logger.info(f"Migration completed in {int(hours)}h {int(minutes)}m {seconds:.2f}s")
        logger.info(f"Total Unique IDs processed: {self.stats['processed_ids_cumulative']}")
        logger.info(f"Processed in this session: {self.stats['processed_in_session']}")
        logger.info(f"Successful upserts: {self.stats['successful_upserts']}")
        logger.info(f"Failed IDs: {self.stats['failed_ids']}")
        logger.info(f"IDs with empty content: {self.stats['empty_content_ids']}")
        
        # Estimated costs based on RUs and WUs
        est_ru_cost = self.stats["ru_count"] * 8.25 / 1_000_000  # $8.25 per million RUs
        est_wu_cost = self.stats["wu_count"] * 2.00 / 1_000_000  # $2.00 per million WUs
        
        logger.info(f"Total Read Units: {self.stats['ru_count']} (est. ${est_ru_cost:.2f})")
        logger.info(f"Total Write Units: {self.stats['wu_count']} (est. ${est_wu_cost:.2f})")
        logger.info(f"Total estimated cost: ${est_ru_cost + est_wu_cost:.2f}")

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description='Migrate vectors from OpenAI to E5 embeddings')
    parser.add_argument('--source-index', type=str, default='holocron-knowledge',
                      help='Source Pinecone index name')
    parser.add_argument('--target-index', type=str, default='holocron-sbert-e5',
                      help='Target Pinecone index name')
    parser.add_argument('--source-namespace', type=str, default='',
                      help='Source namespace in Pinecone')
    parser.add_argument('--target-namespace', type=str, default='',
                      help='Target namespace in Pinecone')                      
    parser.add_argument('--batch-size', type=int, default=1000,
                      help='Batch size for processing')
    parser.add_argument('--num-workers', type=int, default=4,
                      help='Number of worker processes')
    parser.add_argument('--save-locally', action='store_true',
                      help='Save vectors locally instead of uploading to Pinecone')
    parser.add_argument('--output-dir', type=str, default='e5_vectors',
                      help='Output directory for local vector storage')
    parser.add_argument('--vectors-per-file', type=int, default=5000,
                      help='Number of vectors to store in each Parquet file')
    parser.add_argument('--memory-limit', type=int, default=1000,
                      help='Memory threshold in MB to trigger garbage collection')
    parser.add_argument('--limit', type=int, default=None,
                      help='Limit the number of vectors to process')
    parser.add_argument('--force', action='store_true',
                      help='Force reprocessing of all vectors')
    
    args = parser.parse_args()
    
    # Initialize migrator
    migrator = E5Migrator(
        source_index=args.source_index,
        target_index=args.target_index,
        source_namespace=args.source_namespace,
        target_namespace=args.target_namespace,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        save_locally=args.save_locally,
        output_dir=args.output_dir,
        vectors_per_file=args.vectors_per_file,
        force=args.force
    )
    
    # Reset tracking if force flag is set
    if args.force:
        migrator._init_db()
    
    try:
        # Run migration
        migrator.migrate(
            limit=args.limit,
            memory_limit=args.memory_limit
        )
    except KeyboardInterrupt:
        logger.info("Migration interrupted by user")
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        raise

if __name__ == '__main__':
    main() 