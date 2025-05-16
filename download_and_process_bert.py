#!/usr/bin/env python3
"""
Download vectors from Pinecone, generate BERT embeddings locally, and save to a file.
This script processes the vectors without uploading them back to Pinecone.
It also skips any vectors that have already been processed in the BERT index.
"""

import os
import sys
import logging
import json
import time
import argparse
from typing import List, Dict, Any, Set
import numpy as np
from tqdm import tqdm
from pinecone import Pinecone
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

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

def get_already_processed_ids(bert_index, batch_size=1000, max_to_fetch=100000) -> Set[str]:
    """
    Get IDs that have already been processed in the BERT index.
    
    Args:
        bert_index: Pinecone index to check
        batch_size: Size of batches for fetching IDs
        max_to_fetch: Maximum number of IDs to fetch
        
    Returns:
        Set of IDs that are already in the BERT index
    """
    console.print("[bold]Checking for already processed vectors in BERT index...[/bold]")
    
    # Get index stats
    stats = bert_index.describe_index_stats()
    total_vectors = stats['total_vector_count']
    console.print(f"Total vectors in BERT index: {total_vectors}")
    
    processed_ids = set()
    
    # If the index is empty, return an empty set
    if total_vectors == 0:
        return processed_ids
    
    # Create a dummy vector for querying
    dimension = stats['dimension']
    dummy_vector = [0.1] * dimension
    
    # Fetch IDs in batches with progress bar
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        # Limit the number of IDs to fetch to avoid excessive API calls
        fetch_count = min(total_vectors, max_to_fetch)
        task = progress.add_task("[cyan]Fetching existing IDs...", total=fetch_count)
        
        # Try some common ID patterns first for efficiency
        common_ids = [str(i) for i in range(1, 40000, 1000)]
        common_ids.extend([str(i) for i in range(39000, 40000)])
        
        try:
            results = bert_index.fetch(ids=common_ids)
            for id in results.vectors:
                processed_ids.add(id)
            progress.update(task, advance=len(processed_ids))
        except Exception as e:
            logger.error(f"Error fetching common IDs: {e}")
        
        console.print(f"Found {len(processed_ids)} processed IDs through common patterns")
        
        # Check complete ranges if needed
        if len(processed_ids) < 1000:  # If we found very few IDs, do a more thorough check
            console.print("[yellow]Few IDs found through patterns, performing thorough check...[/yellow]")
            
            # Try complete ranges in batches
            for start_id in range(1, 50000, batch_size):
                end_id = min(start_id + batch_size, 50000)
                id_batch = [str(i) for i in range(start_id, end_id)]
                
                try:
                    results = bert_index.fetch(ids=id_batch)
                    for id in results.vectors:
                        processed_ids.add(id)
                    
                    # If we're getting very few results, we've probably found most IDs
                    if len(results.vectors) < 10:
                        break
                        
                    progress.update(task, advance=len(results.vectors))
                    time.sleep(0.1)  # Avoid rate limiting
                    
                except Exception as e:
                    logger.error(f"Error fetching ID batch {start_id}-{end_id}: {e}")
    
    console.print(f"[green]Found {len(processed_ids)} already processed IDs[/green]")
    return processed_ids

def fetch_documents(source_index, max_documents=1000, start_id=1, end_id=None, skip_ids=None):
    """
    Fetch documents from the source Pinecone index.
    
    Args:
        source_index: Pinecone index to fetch from
        max_documents: Maximum number of documents to fetch
        start_id: ID to start fetching from
        end_id: ID to stop fetching at (optional)
        skip_ids: Set of IDs to skip (already processed)
        
    Returns:
        List of documents with content and metadata
    """
    documents = []
    
    if skip_ids is None:
        skip_ids = set()
    
    # Get index stats to determine total vectors
    stats = source_index.describe_index_stats()
    total_vectors = stats['total_vector_count']
    
    if end_id is None:
        end_id = total_vectors + 1
    
    # Adjust end_id based on max_documents
    if start_id + max_documents < end_id:
        end_id = start_id + max_documents
    
    console.print(f"[bold]Fetching up to {max_documents} documents from index[/bold]")
    console.print(f"Total vectors in source index: {total_vectors}")
    console.print(f"ID range: {start_id} to {end_id}")
    console.print(f"Skipping {len(skip_ids)} already processed IDs")
    
    # Create batch fetching progress
    batch_size = 100
    fetched_count = 0
    skipped_count = 0
    
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Fetching documents...", total=end_id-start_id)
        
        for batch_start in range(start_id, end_id, batch_size):
            batch_end = min(batch_start + batch_size, end_id)
            
            # Generate IDs for this batch
            batch_ids = [str(i) for i in range(batch_start, batch_end)]
            
            # Filter out IDs that should be skipped
            batch_ids_filtered = [id for id in batch_ids if id not in skip_ids]
            skipped_in_batch = len(batch_ids) - len(batch_ids_filtered)
            skipped_count += skipped_in_batch
            
            # If all IDs in this batch should be skipped, update progress and continue
            if not batch_ids_filtered:
                progress.update(task, advance=batch_end-batch_start)
                continue
            
            try:
                # Fetch vectors in batch
                vectors = source_index.fetch(ids=batch_ids_filtered)
                
                # Process each vector
                for id, vector in vectors.vectors.items():
                    if hasattr(vector, 'metadata') and vector.metadata:
                        content = vector.metadata.get('content', '')
                        if content and len(content) > 50:  # Make sure it's non-empty and substantial
                            documents.append({
                                'id': id,
                                'content': content,
                                'metadata': vector.metadata
                            })
                            fetched_count += 1
            
            except Exception as e:
                logger.error(f"Error fetching batch {batch_start}-{batch_end}: {str(e)}")
            
            # Update progress
            progress.update(task, advance=batch_end-batch_start)
            
            # Add small delay to avoid rate limiting
            time.sleep(0.1)
    
    console.print(f"[green]Successfully fetched {fetched_count} documents[/green]")
    console.print(f"[yellow]Skipped {skipped_count} already processed documents[/yellow]")
    return documents

def process_and_save_bert_embeddings(documents, bert_embeddings, output_file, batch_size=100):
    """
    Process documents with BERT embeddings and save to a file.
    
    Args:
        documents: List of documents to process
        bert_embeddings: BERTEmbeddings instance
        output_file: File to save the vectors to
        batch_size: Size of batches for processing
        
    Returns:
        Number of vectors successfully processed
    """
    total_documents = len(documents)
    console.print(f"[bold]Processing BERT embeddings for {total_documents} documents[/bold]")
    
    vectors_processed = 0
    results = []
    
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        embedding_task = progress.add_task("[cyan]Generating embeddings...", total=total_documents)
        
        # Process in batches
        for i in range(0, total_documents, batch_size):
            # Get batch of documents
            batch_docs = documents[i:min(i+batch_size, total_documents)]
            batch_contents = [doc['content'] for doc in batch_docs]
            
            # Generate embeddings for batch
            batch_embeddings = bert_embeddings.embed_texts(batch_contents)
            progress.update(embedding_task, advance=len(batch_docs))
            
            # Prepare vectors for saving
            for j, doc in enumerate(batch_docs):
                # Extract metadata (exclude any existing embeddings)
                metadata = {k: v for k, v in doc['metadata'].items() 
                           if k not in ['openai_embedding', 'bert_embedding']}
                
                # Create vector object
                vector_obj = {
                    'id': doc['id'],
                    'values': batch_embeddings[j],
                    'metadata': metadata
                }
                
                results.append(vector_obj)
            
            vectors_processed += len(batch_docs)
            
            # Periodically save results to avoid memory issues
            if len(results) > 10000:
                with open(output_file, 'a') as f:
                    for result in results:
                        f.write(json.dumps(result) + '\n')
                results = []
                
            # Add small delay to prevent system overload
            time.sleep(0.05)
    
    # Save any remaining results
    if results:
        with open(output_file, 'a') as f:
            for result in results:
                f.write(json.dumps(result) + '\n')
    
    console.print(f"[bold green]Successfully processed {vectors_processed} vectors[/bold green]")
    console.print(f"[bold green]Results saved to {output_file}[/bold green]")
    return vectors_processed

def main():
    """Main function to download and process BERT embeddings."""
    parser = argparse.ArgumentParser(description='Download and process BERT embeddings')
    parser.add_argument('--max-docs', type=int, default=5000, 
                        help='Maximum number of documents to process')
    parser.add_argument('--start-id', type=int, default=1,
                        help='ID to start fetching from')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Batch size for processing')
    parser.add_argument('--bert-model', type=str, default="intfloat/e5-small-v2",
                        help='BERT model name from sentence-transformers')
    parser.add_argument('--output-file', type=str, default="bert_embeddings.jsonl",
                        help='File to save BERT embeddings to')
    parser.add_argument('--source-index-name', type=str, default="holocron-knowledge",
                        help='Name of the source Pinecone index')
    parser.add_argument('--target-index-name', type=str, default="holocron-sbert-e5",
                        help='Name of the target BERT Pinecone index')
    parser.add_argument('--skip-processed', action='store_true',
                        help='Skip vectors that are already in the target index')
    args = parser.parse_args()
    
    console.print("[bold cyan]Downloading and Processing BERT Embeddings[/bold cyan]")
    
    # Get API keys
    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    
    if not pinecone_api_key:
        console.print("[bold red]Error:[/bold red] Missing Pinecone API key. Check your .env file.")
        return
    
    # Initialize Pinecone client
    pc = Pinecone(api_key=pinecone_api_key)
    
    # Initialize BERT embeddings
    bert_embeddings = BERTEmbeddings(model_name=args.bert_model)
    
    # Get source Holocron index
    source_index_name = args.source_index_name
    source_index = None
    
    indexes = pc.list_indexes()
    for index_info in indexes:
        if index_info.name == source_index_name:
            source_index = pc.Index(name=source_index_name)
            break
    
    if not source_index:
        console.print(f"[bold red]Error:[/bold red] Source index '{source_index_name}' not found.")
        return
    
    # Get target BERT index to check for already processed IDs
    target_index_name = args.target_index_name
    target_index = None
    skip_ids = set()
    
    for index_info in indexes:
        if index_info.name == target_index_name:
            target_index = pc.Index(name=target_index_name)
            break
    
    # Get already processed IDs if requested
    if args.skip_processed and target_index:
        skip_ids = get_already_processed_ids(target_index)
    
    # Initialize output file
    output_file = args.output_file
    if os.path.exists(output_file):
        # Create a backup of existing file
        backup_file = f"{output_file}.bak"
        os.rename(output_file, backup_file)
        console.print(f"[yellow]Existing file renamed to {backup_file}[/yellow]")
    
    # Create empty output file
    with open(output_file, 'w') as f:
        pass
    
    # Fetch documents from source index
    documents = fetch_documents(
        source_index, 
        max_documents=args.max_docs,
        start_id=args.start_id,
        skip_ids=skip_ids
    )
    
    if not documents:
        console.print("[bold red]Error or no new documents to process.[/bold red]")
        return
    
    # Process BERT embeddings and save to file
    vectors_processed = process_and_save_bert_embeddings(
        documents,
        bert_embeddings,
        output_file,
        batch_size=args.batch_size
    )
    
    # Final summary
    console.print("\n[bold]BERT Embeddings Processing Summary[/bold]")
    console.print(f"Source Index: [cyan]{source_index_name}[/cyan]")
    console.print(f"BERT Model: [cyan]{args.bert_model}[/cyan]")
    console.print(f"Already Processed IDs: [cyan]{len(skip_ids)}[/cyan]")
    console.print(f"New Documents Processed: [cyan]{len(documents)}[/cyan]")
    console.print(f"Vectors Generated: [cyan]{vectors_processed}[/cyan]")
    console.print(f"Vector Dimensions: [cyan]{bert_embeddings.embedding_dim}[/cyan]")
    console.print(f"Output File: [cyan]{output_file}[/cyan]")
    
    console.print("\n[bold green]BERT Embeddings Processing Complete[/bold green]")
    console.print("[bold]To upload these embeddings to Pinecone, use the upload_bert_embeddings.py script[/bold]")

if __name__ == "__main__":
    main() 