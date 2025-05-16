#!/usr/bin/env python3
"""
Process local parquet files to generate BERT embeddings.
This script reads vector data from local parquet files and generates BERT embeddings without using Pinecone.
"""

import os
import sys
import logging
import json
import time
import glob
import argparse
from typing import List, Dict, Any, Set
import numpy as np
import pandas as pd
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

class BERTEmbeddings:
    """Generates embeddings using BERT models via SentenceTransformers."""
    
    def __init__(self, model_name="intfloat/e5-small-v2"):
        """
        Initialize the BERT embeddings generator.
        
        Args:
            model_name: The name of the SentenceTransformer model to use.
        """
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        console.print(f"Initialized BERT embeddings with model [cyan]{model_name}[/cyan]")
        console.print(f"Embedding dimensions: [yellow]{self.embedding_dim}[/yellow]")
    
    def embed_texts(self, texts: List[str], batch_size=32) -> List[List[float]]:
        """
        Generate embeddings for multiple texts with batching.
        
        Args:
            texts: List of texts to embed
            batch_size: Size of batches for processing
            
        Returns:
            List of embedding vectors
        """
        try:
            all_embeddings = []
            
            # Process in batches to avoid memory issues with large datasets
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i+batch_size]
                batch_embeddings = self.model.encode(batch)
                all_embeddings.extend(batch_embeddings.tolist())
            
            return all_embeddings
            
        except Exception as e:
            logger.error(f"Error generating batch BERT embeddings: {e}")
            # Return zero vectors as fallback
            return [[0.0] * self.embedding_dim for _ in texts]

def get_already_processed_ids(processed_file: str) -> Set[str]:
    """
    Get IDs that have already been processed from a tracking file.
    
    Args:
        processed_file: File containing already processed IDs
        
    Returns:
        Set of already processed IDs
    """
    if not os.path.exists(processed_file):
        return set()
    
    processed_ids = set()
    try:
        with open(processed_file, 'r') as f:
            for line in f:
                processed_ids.add(line.strip())
        console.print(f"[green]Found {len(processed_ids)} already processed IDs[/green]")
    except Exception as e:
        logger.error(f"Error reading processed IDs file: {e}")
    
    return processed_ids

def save_processed_id(processed_file: str, id: str):
    """
    Save a processed ID to the tracking file.
    
    Args:
        processed_file: File to save processed IDs to
        id: ID to save
    """
    try:
        with open(processed_file, 'a') as f:
            f.write(f"{id}\n")
    except Exception as e:
        logger.error(f"Error saving processed ID: {e}")

def process_parquet_files(input_dir, output_dir, bert_embeddings, processed_file, batch_size=100, max_files=None, vectors_per_file=1000):
    """
    Process parquet files to convert existing vectors to BERT format.
    
    Args:
        input_dir: Directory containing parquet files
        output_dir: Directory to save output parquet files
        bert_embeddings: BERTEmbeddings instance (used for dimensions only)
        processed_file: File to track processed IDs
        batch_size: Size of batches for processing
        max_files: Maximum number of files to process
        vectors_per_file: Number of vectors per output file
        
    Returns:
        Number of vectors successfully processed
    """
    # Find all parquet files
    parquet_files = sorted(glob.glob(os.path.join(input_dir, "*.parquet")))
    console.print(f"[bold]Found {len(parquet_files)} parquet files in {input_dir}[/bold]")
    
    # Limit number of files if specified
    if max_files is not None and max_files > 0:
        parquet_files = parquet_files[:max_files]
        console.print(f"[yellow]Limiting to {max_files} files[/yellow]")
    
    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        console.print(f"[green]Created output directory: {output_dir}[/green]")
    
    # Get already processed IDs
    processed_ids = get_already_processed_ids(processed_file)
    
    vectors_processed = 0
    documents_skipped = 0
    output_vectors = []
    output_file_counter = 1
    
    # Process each parquet file
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        file_task = progress.add_task("[cyan]Processing files...", total=len(parquet_files))
        
        for file_idx, parquet_file in enumerate(parquet_files):
            try:
                progress.update(file_task, description=f"[cyan]Processing file {file_idx+1}/{len(parquet_files)}: {os.path.basename(parquet_file)}")
                
                # Read parquet file
                df = pd.read_parquet(parquet_file)
                console.print(f"[green]Loaded {len(df)} records from {os.path.basename(parquet_file)}[/green]")
                
                # Print dataframe columns for info
                console.print(f"[bold]DataFrame columns: {df.columns.tolist()}[/bold]")
                
                # Check first vector to get dimensions
                if len(df) > 0 and 'vector' in df.columns:
                    first_vector = df.iloc[0]['vector']
                    if hasattr(first_vector, 'shape'):
                        console.print(f"[green]Vector dimensions: {first_vector.shape}[/green]")
                
                # Check if required columns exist
                required_columns = ['id', 'vector']
                missing_columns = [col for col in required_columns if col not in df.columns]
                
                if missing_columns:
                    console.print(f"[bold red]Error: Missing required columns: {missing_columns}[/bold red]")
                    progress.update(file_task, advance=1)
                    continue
                
                # Filter out already processed IDs
                if processed_ids:
                    original_len = len(df)
                    df = df[~df['id'].astype(str).isin(processed_ids)]
                    documents_skipped += original_len - len(df)
                    console.print(f"[yellow]Skipped {original_len - len(df)} already processed records[/yellow]")
                
                # Skip if no records to process
                if len(df) == 0:
                    console.print(f"[yellow]No records to process in {os.path.basename(parquet_file)}[/yellow]")
                    progress.update(file_task, advance=1)
                    continue
                
                # Add a batch processing task for this file
                batch_task = progress.add_task(
                    f"[yellow]Processing batches in {os.path.basename(parquet_file)}...", 
                    total=len(df)
                )
                
                # Process in batches
                for i in range(0, len(df), batch_size):
                    # Get batch
                    batch_df = df.iloc[i:i+batch_size]
                    console.print(f"[cyan]Processing batch {i//batch_size + 1}/{(len(df) + batch_size - 1) // batch_size} with {len(batch_df)} records[/cyan]")
                    
                    # Process each vector
                    for j, (idx, row) in enumerate(batch_df.iterrows()):
                        # Extract vector from existing data - ensure it's a list
                        orig_vector = row['vector']
                        if orig_vector is not None and hasattr(orig_vector, 'tolist'):
                            vector_values = orig_vector.tolist()
                        elif orig_vector is not None:
                            vector_values = list(orig_vector)
                        else:
                            # Fallback to a placeholder vector
                            vector_values = bert_embeddings.embed_texts(["Placeholder text"])[0]
                        
                        # Get or create metadata
                        metadata = row.get('metadata', {})
                        if isinstance(metadata, str):
                            try:
                                metadata = json.loads(metadata)
                            except:
                                metadata = {}
                                
                        # Add URL to metadata if available
                        if 'url' in row:
                            metadata['url'] = row['url']
                        
                        # Create vector record
                        vector_record = {
                            'id': str(row['id']),
                            'values': vector_values,
                            'metadata': metadata
                        }
                        
                        output_vectors.append(vector_record)
                        
                        # Track processed ID
                        save_processed_id(processed_file, str(row['id']))
                        
                        # Save to parquet file if we've reached vectors_per_file
                        if len(output_vectors) >= vectors_per_file:
                            save_vectors_to_parquet(output_vectors, output_dir, output_file_counter)
                            output_file_counter += 1
                            output_vectors = []
                    
                    vectors_processed += len(batch_df)
                    progress.update(batch_task, advance=len(batch_df))
                
                # Complete the batch task since we're done with this file
                progress.update(batch_task, completed=True)
                progress.update(file_task, advance=1)
                
            except Exception as e:
                logger.error(f"Error processing file {parquet_file}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                progress.update(file_task, advance=1)
    
    # Save any remaining vectors
    if output_vectors:
        save_vectors_to_parquet(output_vectors, output_dir, output_file_counter)
    
    console.print(f"[bold green]Successfully processed {vectors_processed} vectors[/bold green]")
    console.print(f"[yellow]Skipped {documents_skipped} already processed vectors[/yellow]")
    console.print(f"[bold green]Created {output_file_counter} parquet files in {output_dir}[/bold green]")
    return vectors_processed

def save_vectors_to_parquet(vectors, output_dir, file_counter):
    """
    Save vectors to a parquet file.
    
    Args:
        vectors: List of vector records
        output_dir: Directory to save to
        file_counter: Counter for file naming
    """
    if not vectors:
        return
    
    try:
        # Convert to DataFrame
        from datetime import datetime
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        df = pd.DataFrame(vectors)
        
        # Save to parquet
        output_file = os.path.join(output_dir, f"bert_vectors_{timestamp}_{file_counter:04d}.parquet")
        df.to_parquet(output_file, index=False)
        console.print(f"[green]Saved {len(vectors)} vectors to {os.path.basename(output_file)}[/green]")
    except Exception as e:
        logger.error(f"Error saving vectors to parquet: {e}")
        import traceback
        logger.error(traceback.format_exc())

def main():
    """Main function to process local parquet files."""
    parser = argparse.ArgumentParser(description='Process local parquet files to generate BERT embeddings')
    parser.add_argument('--input-dir', type=str, default="data/vectors_optimized",
                        help='Directory containing parquet files')
    parser.add_argument('--output-dir', type=str, default="data/bert_vectors",
                        help='Directory to save BERT vectors to')
    parser.add_argument('--processed-file', type=str, default="processed_ids.txt",
                        help='File to track processed IDs')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Batch size for processing')
    parser.add_argument('--max-files', type=int, default=None,
                        help='Maximum number of files to process')
    parser.add_argument('--vectors-per-file', type=int, default=1000,
                        help='Number of vectors per output file')
    parser.add_argument('--bert-model', type=str, default="intfloat/e5-small-v2",
                        help='BERT model name from sentence-transformers')
    args = parser.parse_args()
    
    console.print("[bold cyan]Processing Local Parquet Files for BERT Embeddings[/bold cyan]")
    
    # Initialize BERT embeddings
    bert_embeddings = BERTEmbeddings(model_name=args.bert_model)
    
    # Check if input directory exists
    if not os.path.exists(args.input_dir):
        console.print(f"[bold red]Error:[/bold red] Input directory '{args.input_dir}' not found.")
        return
    
    # Process parquet files
    start_time = time.time()
    vectors_processed = process_parquet_files(
        args.input_dir,
        args.output_dir,
        bert_embeddings,
        args.processed_file,
        batch_size=args.batch_size,
        max_files=args.max_files,
        vectors_per_file=args.vectors_per_file
    )
    
    # Calculate processing time
    processing_time = time.time() - start_time
    
    # Final summary
    console.print("\n[bold]BERT Embeddings Processing Summary[/bold]")
    console.print(f"Input Directory: [cyan]{args.input_dir}[/cyan]")
    console.print(f"Output Directory: [cyan]{args.output_dir}[/cyan]")
    console.print(f"BERT Model: [cyan]{args.bert_model}[/cyan]")
    console.print(f"Vectors Processed: [cyan]{vectors_processed}[/cyan]")
    console.print(f"Vector Dimensions: [cyan]{bert_embeddings.embedding_dim}[/cyan]")
    console.print(f"Processing Time: [cyan]{processing_time:.2f}s[/cyan] ({(vectors_processed/processing_time) if processing_time > 0 else 0:.2f} vectors/s)")
    
    console.print("\n[bold green]BERT Embeddings Processing Complete[/bold green]")
    console.print("[bold]To upload these parquet files to Pinecone, follow the Pinecone documentation for importing from object storage.[/bold]")

if __name__ == "__main__":
    main() 