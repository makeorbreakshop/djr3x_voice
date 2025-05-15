#!/usr/bin/env python3
"""
Migrate vectors from holocron-knowledge (OpenAI embeddings) to holocron-sbert-e5 (E5 embeddings).

This script:
1. Uses a direct approach to fetch vectors from the source index
2. Extracts text content from metadata
3. Generates new E5 embeddings using the intfloat/e5-small-v2 model
4. Upserts the new embeddings to holocron-sbert-e5 index
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
import random
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
import torch

# Pinecone imports
from pinecone import Pinecone

# SentenceTransformers for E5 embeddings
from sentence_transformers import SentenceTransformer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"e5_migration_{datetime.now().strftime('%Y%m%d%H%M%S')}.log"),
        logging.StreamHandler()
    ]
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
        output_dir: str = "e5_vectors"
    ):
        """
        Initialize the migrator.
        
        Args:
            source_index: Name of the source index (OpenAI embeddings)
            target_index: Name of the target index (E5 embeddings)
            source_namespace: Namespace in source index
            target_namespace: Namespace in target index
            model_name: E5 model name to use
            batch_size: Number of vectors to process in each batch
            num_workers: Number of worker processes for parallel embedding generation
            save_locally: Whether to save vectors locally instead of uploading to Pinecone
            output_dir: Directory to save vectors to if save_locally is True
        """
        self.source_index = source_index
        self.target_index = target_index
        self.source_namespace = source_namespace
        self.target_namespace = target_namespace
        self.model_name = model_name
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.processed_ids: Set[str] = set()
        self.checkpoint_file = "e5_migration_checkpoint.json"
        self.save_locally = save_locally
        self.output_dir = output_dir
        self.output_file = None
        
        # Create output directory if saving locally
        if self.save_locally:
            os.makedirs(self.output_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            self.output_file = os.path.join(self.output_dir, f"e5_vectors_{timestamp}.jsonl")
            logger.info(f"Vectors will be saved locally to: {self.output_file}")
        
        # Initialize Pinecone
        self.pc = Pinecone()
        self.source_idx = self.pc.Index(source_index)
        if not self.save_locally:
            self.target_idx = self.pc.Index(target_index)
        else:
            self.target_idx = None
        
        # Load model
        logger.info(f"Loading E5 model: {model_name}")
        self.model = SentenceTransformer(model_name)
        
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
        
        # Load checkpoint if exists
        self._load_checkpoint()
        
        # Statistics
        self.stats = {
            "total_vectors_on_source": 0,
            "processed_ids_cumulative": len(self.processed_ids),
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

    def _load_checkpoint(self):
        """Load processed IDs from checkpoint file if it exists."""
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file, 'r') as f:
                checkpoint = json.load(f)
                self.processed_ids = set(checkpoint.get('processed_ids', []))
            logger.info(f"Loaded {len(self.processed_ids)} processed IDs from checkpoint")
            # Print some sample IDs from the checkpoint
            sample_ids = list(self.processed_ids)[:5]
            logger.info(f"Sample IDs from checkpoint: {sample_ids}")
    
    def _save_checkpoint(self):
        """Save current progress to checkpoint file."""
        with open(self.checkpoint_file, 'w') as f:
            json.dump({
                'processed_ids': list(self.processed_ids),
                'timestamp': datetime.now().isoformat()
            }, f)
            
    def reset_checkpoint(self):
        """Reset the checkpoint file, clearing all processed IDs."""
        if os.path.exists(self.checkpoint_file):
            # Create a backup first
            backup_file = f"{self.checkpoint_file}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            os.rename(self.checkpoint_file, backup_file)
            logger.info(f"Created backup of checkpoint file: {backup_file}")
        
        # Clear processed IDs and save empty checkpoint
        self.processed_ids = set()
        self._save_checkpoint()
        logger.info("Checkpoint reset. All IDs will be processed again.")
    
    def extract_text_content(self, vector_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract text content from vector metadata.
        This function is simplified since we're now only processing vectors
        that we know have content.
        
        Args:
            vector_data: Vector data including metadata
            
        Returns:
            Extracted text content or None if not found
        """
        metadata = vector_data.get("metadata", {})
        vector_id = vector_data.get("id", "unknown")
        
        if "content" in metadata and metadata["content"]:
            return metadata["content"]
        
        # Log that we couldn't find content (shouldn't happen given our filtering)
        logger.warning(f"Expected content field not found in vector {vector_id}. Available fields: {list(metadata.keys())}")
        self.stats["empty_content_ids"] += 1
        return None
    
    def create_e5_embedding(self, text: str) -> np.ndarray:
        """
        Generate E5 embedding for a single text string.
        
        Args:
            text: Text string to embed
            
        Returns:
            E5 embedding (384 dimensions)
        """
        return self.model.encode(text)
    
    def process_batch(self, batch_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process a batch of vectors."""
        try:
            # Debug: Print metadata structure of first item
            if batch_data and len(batch_data) > 0:
                logger.info(f"Sample metadata structure: {json.dumps(batch_data[0].get('metadata', {}), indent=2)}")
            
            # Extract text content - handle both metadata formats
            texts = []
            for item in batch_data:
                metadata = item.get('metadata', {})
                # Try different possible text field names
                text = metadata.get('text') or metadata.get('content') or metadata.get('article_text')
                if not text:
                    logger.warning(f"No text found in metadata for ID {item['id']}")
                    continue
                texts.append(text)
            
            if not texts:
                return []
            
            # Batch encode all texts at once
            with torch.inference_mode():  # Faster than no_grad
                embeddings = self.model.encode(
                    texts,
                    batch_size=self.process_batch_size,
                    convert_to_numpy=True,  # Convert to numpy at the end
                )
            
            # Process vectors in parallel
            processed_vectors = []
            valid_items = [item for item in batch_data if item.get('metadata', {}).get('text') or 
                                                         item.get('metadata', {}).get('content') or 
                                                         item.get('metadata', {}).get('article_text')]
            
            for item, embedding in zip(valid_items, embeddings):
                vector_data = {
                    'id': item['id'],
                    'values': embedding.tolist(),
                    'metadata': item['metadata']
                }
                processed_vectors.append(vector_data)
            
            return processed_vectors
            
        except Exception as e:
            logger.error(f"Error processing batch: {str(e)}")
            logger.error(traceback.format_exc())
            return []
    
    def save_vectors_locally(self, vectors: List[Dict[str, Any]]) -> int:
        """Save vectors to local file."""
        if not vectors:
            return 0
            
        try:
            # Use buffered writing for better performance
            with open(self.output_file, 'a', buffering=8192) as f:
                for vector in vectors:
                    f.write(json.dumps(vector) + '\n')
                    self.processed_ids.add(vector['id'])
                    
            return len(vectors)
        except Exception as e:
            logger.error(f"Error saving vectors: {str(e)}")
            return 0
    
    def migrate(self, limit: Optional[int] = None):
        """
        Run the full migration using a streaming ID-based approach.
        
        Args:
            limit: Optional limit to the number of vectors to process
        """
        logger.info(f"Starting migration from {self.source_index} to {self.target_index}")
        logger.info(f"Using batch size: {self.batch_size}")
        
        if self.save_locally:
            logger.info(f"Vectors will be saved locally to: {self.output_file}")
        
        # Reset session-specific stats
        self.stats["session_start_time"] = time.time()
        self.stats["processed_in_session"] = 0

        # Determine total for this session
        remaining_on_source = max(0, self.stats.get("total_vectors_on_source", 0) - len(self.processed_ids))
        
        if limit is not None:
            tqdm_total = min(limit, remaining_on_source)
            if limit > remaining_on_source and self.stats.get("total_vectors_on_source", 0) > 0:
                logger.info(f"User limit ({limit}) exceeds estimated remaining vectors ({remaining_on_source})")
            elif limit <= 0:
                logger.info("Limit is 0 or less. No vectors will be processed.")
                self._print_final_results()
                return
        else:
            tqdm_total = remaining_on_source

        if tqdm_total <= 0:
            logger.info("No vectors to process in this session. Exiting.")
            self._print_final_results()
            return

        processed_in_session = 0
        vectors_to_process = limit if limit is not None else float('inf')

        try:
            # Use tqdm for progress tracking
            with tqdm(total=tqdm_total, unit="vector", desc="Migrating E5 Embeddings") as pbar:
                # Process in streaming batches
                id_batch_count = 0
                next_checkpoint_save = 100  # Save checkpoint after this many batches
                
                for id_batch in self.source_idx.list(namespace=self.source_namespace):
                    id_batch_count += 1
                    batch_size = len(id_batch) if id_batch else 0
                    
                    # Debug info for first few batches
                    if id_batch_count <= 3:
                        logger.info(f"Batch {id_batch_count}: Processing batch of {batch_size} IDs")
                        if batch_size > 0:
                            sample_ids = id_batch[:min(3, batch_size)]
                            logger.info(f"Sample IDs: {sample_ids}")
                    
                    # Filter already processed IDs
                    unprocessed_ids = [id for id in id_batch if id not in self.processed_ids]
                    
                    # Skip if all IDs already processed
                    if not unprocessed_ids:
                        if id_batch_count <= 5:
                            logger.info(f"Batch {id_batch_count}: All {batch_size} IDs already processed, skipping")
                        continue
                    
                    # Limit the number of IDs to process if needed
                    if len(unprocessed_ids) > vectors_to_process:
                        unprocessed_ids = unprocessed_ids[:int(vectors_to_process)]
                        logger.info(f"Reached processing limit, truncating batch to {len(unprocessed_ids)} IDs")
                    
                    # Fetch vectors by ID
                    try:
                        if id_batch_count <= 5:
                            logger.info(f"Batch {id_batch_count}: Fetching {len(unprocessed_ids)} vectors")
                        
                        response = self.source_idx.fetch(
                            ids=unprocessed_ids,
                            namespace=self.source_namespace
                        )
                        
                        if id_batch_count <= 5:
                            logger.info(f"Batch {id_batch_count}: Fetched {len(response.vectors)} vectors")
                        
                        # Track read units - assuming 1 RU per vector
                        self.stats["ru_count"] += len(response.vectors)
                        
                        if not response.vectors:
                            logger.warning(f"Batch {id_batch_count}: Fetch returned no vectors, skipping")
                            continue
                            
                    except Exception as e:
                        logger.error(f"Batch {id_batch_count}: Fetch failed: {str(e)}")
                        logger.error(traceback.format_exc())
                        logger.info("Waiting 5 seconds before continuing...")
                        time.sleep(5)
                        continue
                    
                    # Convert fetched vectors to the format expected by process_batch
                    vectors = []
                    for id, vector_data in response.vectors.items():
                        vectors.append({
                            "id": id,
                            "values": vector_data.values,
                            "metadata": vector_data.metadata
                        })
                    
                    # Process the batch
                    if id_batch_count <= 5:
                        logger.info(f"Batch {id_batch_count}: Processing {len(vectors)} vectors")
                    
                    try:
                        processed_vectors = self.process_batch(vectors)
                        if id_batch_count <= 5:
                            logger.info(f"Batch {id_batch_count}: Processed {len(processed_vectors)} vectors")
                    except Exception as e:
                        logger.error(f"Batch {id_batch_count}: Processing error: {str(e)}")
                        logger.error(traceback.format_exc())
                        continue
                    
                    if processed_vectors:
                        if self.save_locally:
                            # Save vectors to local file
                            try:
                                count = self.save_vectors_locally(processed_vectors)
                                if id_batch_count <= 5:
                                    logger.info(f"Batch {id_batch_count}: Saved {count} vectors locally")
                                    
                                # Update the successful upserts counter
                                self.stats["successful_upserts"] += count
                            except Exception as e:
                                logger.error(f"Batch {id_batch_count}: Error saving vectors: {str(e)}")
                                logger.error(traceback.format_exc())
                                continue
                        else:
                            # Upsert to target index
                            try:
                                logger.info(f"Batch {id_batch_count}: Upserting vectors to {self.target_index} with namespace '{self.target_namespace}'")
                                # Debug: print the first vector's structure
                                if processed_vectors and id_batch_count <= 2:
                                    sample_vector = processed_vectors[0]
                                    logger.info(f"Sample vector structure: id={sample_vector['id']}, values (shape)={len(sample_vector['values'])}, metadata keys={list(sample_vector['metadata'].keys() if sample_vector.get('metadata') else {})}")
                                    
                                self.target_idx.upsert(
                                    vectors=processed_vectors,
                                    namespace=self.target_namespace
                                )
                                if id_batch_count <= 5:
                                    logger.info(f"Batch {id_batch_count}: Upserted {len(processed_vectors)} vectors")
                                
                                # Update the successful upserts counter
                                self.stats["successful_upserts"] += len(processed_vectors)
                                # Track write units - assuming 1 WU per vector
                                self.stats["wu_count"] += len(processed_vectors)
                                
                            except Exception as e:
                                logger.error(f"Batch {id_batch_count}: Upsert error: {str(e)}")
                                logger.error(traceback.format_exc())
                                continue
                    
                        # Update progress and stats
                        batch_processed = len(processed_vectors)
                        processed_in_session += batch_processed
                        self.stats["processed_in_session"] += batch_processed
                        self.stats["processed_ids_cumulative"] = len(self.processed_ids)
                        vectors_to_process -= batch_processed
                        
                        # Update progress bar
                        pbar.update(batch_processed)
                        
                        # Save checkpoint periodically
                        if id_batch_count >= next_checkpoint_save:
                            self._save_checkpoint()
                            next_checkpoint_save += 100  # Save every 100 batches
                        
                        # Calculate and display current rate
                        session_elapsed = time.time() - self.stats["session_start_time"]
                        current_rate = self.stats["processed_in_session"] / session_elapsed if session_elapsed > 0 else 0
                        pbar.set_postfix_str(f"Rate: {current_rate:.2f} v/sec, Batch: {id_batch_count}")
                        
                        # Break if we've reached the processing limit
                        if vectors_to_process <= 0:
                            logger.info(f"Reached processing limit of {limit} vectors")
                            break
                
                # Save final checkpoint
                self._save_checkpoint()
                logger.info(f"Completed processing {processed_in_session} vectors in {id_batch_count} batches")
            
            # Final progress update
            self._print_final_results()
            
        except KeyboardInterrupt:
            logger.info("Migration interrupted by user")
            self._save_checkpoint()
            self._print_final_results()
        except Exception as e:
            logger.error(f"Error during migration: {e}")
            logger.error(traceback.format_exc())
            self._save_checkpoint()
            self._print_final_results()
    
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
    parser = argparse.ArgumentParser(description="Migrate vectors from OpenAI embeddings to E5 embeddings")
    
    parser.add_argument("--api-key", type=str, help="Pinecone API key")
    parser.add_argument("--source-index", type=str, default="holocron-knowledge", help="Source index name")
    parser.add_argument("--target-index", type=str, default="holocron-sbert-e5", help="Target index name")
    parser.add_argument("--source-namespace", type=str, default="", help="Source namespace")
    parser.add_argument("--target-namespace", type=str, default="", help="Target namespace")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for processing")
    parser.add_argument("--model-name", type=str, default="intfloat/e5-small-v2", help="E5 model name")
    parser.add_argument("--limit", type=int, help="Limit number of vectors to process")
    parser.add_argument("--num-workers", type=int, default=4, help="Number of worker processes")
    parser.add_argument("--reset", action="store_true", help="Reset checkpoint file and process all vectors again")
    parser.add_argument("--save-locally", action="store_true", help="Save vectors locally instead of uploading to Pinecone")
    parser.add_argument("--output-dir", type=str, default="e5_vectors", help="Directory to save vectors to if save-locally is True")
    
    args = parser.parse_args()
    
    # If API key not provided, try to get from environment
    api_key = args.api_key or os.environ.get("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("Pinecone API key must be provided through --api-key or PINECONE_API_KEY environment variable")
    
    # Inform user about the migration parameters
    logger.info(f"Starting migration from '{args.source_index}' to '{args.target_index}'")
    logger.info(f"Using model: {args.model_name} (384 dimensions)")
    logger.info(f"Batch size: {args.batch_size}")
    if args.limit:
        logger.info(f"Processing limit: {args.limit} vectors")
    if args.reset:
        logger.info("Reset flag set - will clear checkpoint and process all vectors again")
    
    # Create and run migrator
    migrator = E5Migrator(
        source_index=args.source_index,
        target_index=args.target_index,
        source_namespace=args.source_namespace,
        target_namespace=args.target_namespace,
        model_name=args.model_name,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        save_locally=args.save_locally,
        output_dir=args.output_dir
    )
    
    # Reset checkpoint if requested
    if args.reset:
        migrator.reset_checkpoint()
    
    migrator.migrate(limit=args.limit)

if __name__ == "__main__":
    main() 