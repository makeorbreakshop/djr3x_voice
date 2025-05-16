#!/usr/bin/env python3
"""
Generate BERT embeddings from existing Pinecone data for Holocron Knowledge Base.
This script downloads documents from Pinecone, creates BERT embeddings, and uploads them back.
"""

import os
import json
import glob
import argparse
import time
import logging
import gc
import tempfile
from typing import List, Dict, Any, Optional, Tuple, Iterator
from datetime import datetime
from tqdm import tqdm
import pandas as pd
import numpy as np
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
from pinecone import Pinecone, Index

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
console = Console()

class BERTEmbedder:
    """Generate BERT embeddings from article text with improved memory management."""
    
    def __init__(self, model_name: str = "intfloat/e5-small-v2", 
                 batch_size: int = 32, device: Optional[str] = None):
        """
        Initialize the BERT embedder.
        
        Args:
            model_name: Name of the SentenceTransformer model to use
            batch_size: Batch size for embedding generation
            device: Device to use for computation (None for auto selection)
        """
        self.model_name = model_name
        # Initialize the model with explicit device selection
        self.model = SentenceTransformer(model_name, device=device)
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        self.batch_size = batch_size
        
        console.print(f"[bold cyan]Initialized BERT embedder with model: [/bold cyan][green]{model_name}[/green]")
        console.print(f"[bold cyan]Embedding dimensions: [/bold cyan][green]{self.embedding_dim}[/green]")
        console.print(f"[bold cyan]Using device: [/bold cyan][green]{self.model.device}[/green]")
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a batch of texts.
        
        Args:
            texts: Batch of text strings to embed
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
            
        try:
            # Print info about the first text being embedded
            if texts:
                console.print(f"[magenta]Embedding batch of {len(texts)} texts. First text sample: '{texts[0][:100]}...'[/magenta]")
            
            batch_embeddings = self.model.encode(texts)
            return batch_embeddings.tolist()
            
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            return [[] for _ in texts]
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of texts using batching.
        
        Args:
            texts: List of text strings to embed
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
            
        all_embeddings = []
        
        # Process in batches to avoid memory issues
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i+self.batch_size]
            batch_embeddings = self.embed_batch(batch)
            all_embeddings.extend(batch_embeddings)
            
            # Force garbage collection after each batch
            gc.collect()
            
        return all_embeddings

def fetch_documents_from_pinecone(source_index: Index, max_documents: int = 1000, 
                                  start_id: int = 0, batch_size: int = 100) -> List[Dict[str, Any]]:
    """
    Fetch documents from the source Pinecone index.
    
    Args:
        source_index: Pinecone index to fetch from
        max_documents: Maximum number of documents to fetch
        start_id: ID to start fetching from
        batch_size: How many documents to fetch in each batch
        
    Returns:
        List of documents with content and metadata
    """
    documents = []
    
    # Get index stats to determine total vectors
    stats = source_index.describe_index_stats()
    total_vectors = stats['total_vector_count']
    namespace = stats['namespaces']
    
    # Find most populated namespace if multiple exist
    target_namespace = ""
    if namespace and len(namespace) > 0:
        max_count = 0
        for ns, ns_stats in namespace.items():
            if ns_stats['vector_count'] > max_count:
                max_count = ns_stats['vector_count']
                target_namespace = ns
    
    console.print(f"[bold]Fetching up to {max_documents} documents from index[/bold]")
    console.print(f"Total vectors in index: {total_vectors}")
    console.print(f"Using namespace: '{target_namespace}'")
    
    # Determine ID range for fetching
    end_id = min(start_id + max_documents, total_vectors)
    
    # Create batch fetching progress
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
            ids = [str(i) for i in range(batch_start, batch_end)]
            
            try:
                # Fetch vectors in batch
                vectors = source_index.fetch(ids=ids, namespace=target_namespace)
                
                # Process each vector
                for id, vector in vectors.vectors.items():
                    metadata = vector.metadata if hasattr(vector, 'metadata') else {}
                    # Check for text content in various metadata fields
                    content = (metadata.get('text', '') or 
                               metadata.get('content', '') or 
                               metadata.get('chunk_text', ''))
                    
                    if content and len(content) > 20:  # Make sure it's non-empty and substantive
                        documents.append({
                            'id': id,
                            'content': content,
                            'metadata': metadata
                        })
            
            except Exception as e:
                logger.error(f"Error fetching batch {batch_start}-{batch_end}: {str(e)}")
            
            # Update progress
            progress.update(task, advance=batch_end-batch_start)
            
            # Add small delay to avoid rate limiting
            time.sleep(0.1)
    
    console.print(f"[green]Successfully fetched {len(documents)} documents[/green]")
    return documents

def build_bert_index(documents: List[Dict[str, Any]], bert_embedder: BERTEmbedder, 
                     target_index: Index, batch_size: int = 100):
    """
    Build BERT embeddings and upload to target index.
    
    Args:
        documents: List of documents with content and metadata
        bert_embedder: BERT embedder instance
        target_index: Pinecone index for upload
        batch_size: Batch size for processing
        
    Returns:
        Number of vectors uploaded
    """
    total_documents = len(documents)
    console.print(f"[bold]Building BERT index for {total_documents} documents[/bold]")
    
    vectors_uploaded = 0
    
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        embedding_task = progress.add_task("[cyan]Generating embeddings...", total=total_documents)
        upsert_task = progress.add_task("[green]Upserting vectors...", total=total_documents)
        
        # Process in batches
        for i in range(0, total_documents, batch_size):
            # Get batch of documents
            batch_docs = documents[i:min(i+batch_size, total_documents)]
            batch_contents = [doc['content'] for doc in batch_docs]
            
            # Generate embeddings for batch
            batch_embeddings = bert_embedder.embed_texts(batch_contents)
            progress.update(embedding_task, advance=len(batch_docs))
            
            # Prepare vectors for upsert
            vectors_to_upsert = []
            
            for j, doc in enumerate(batch_docs):
                # Extract metadata (exclude any existing embeddings to save space)
                metadata = {k: v for k, v in doc['metadata'].items() 
                           if k not in ['openai_embedding', 'bert_embedding']}
                
                # Create vector
                vectors_to_upsert.append({
                    'id': doc['id'],
                    'values': batch_embeddings[j],
                    'metadata': metadata
                })
            
            # Upsert vectors
            try:
                target_index.upsert(vectors=vectors_to_upsert)
                vectors_uploaded += len(vectors_to_upsert)
            except Exception as e:
                logger.error(f"Error upserting batch: {str(e)}")
            
            progress.update(upsert_task, advance=len(batch_docs))
            
            # Add small delay to avoid rate limiting
            time.sleep(0.2)
            
            # Force garbage collection
            gc.collect()
    
    console.print(f"[bold green]Successfully uploaded {vectors_uploaded} vectors to BERT index[/bold green]")
    return vectors_uploaded

def create_pinecone_index(pc: Pinecone, index_name: str, dimension: int) -> Optional[Index]:
    """
    Create a new Pinecone index for BERT embeddings.
    
    Args:
        pc: Pinecone client
        index_name: Name for the new index
        dimension: Vector dimension
        
    Returns:
        Pinecone index object if successful, None otherwise
    """
    try:
        # Check if index already exists
        indexes = pc.list_indexes()
        for index_info in indexes:
            if index_info.name == index_name:
                console.print(f"[yellow]Index {index_name} already exists[/yellow]")
                return pc.Index(name=index_name)
        
        # Create index
        pc.create_index(
            name=index_name,
            dimension=dimension,
            metric="cosine",
            spec={"serverless": {"cloud": "aws", "region": "us-east-1"}}
        )
        
        console.print(f"[bold green]Created new Pinecone index: {index_name}[/bold green]")
        
        # Wait for index to initialize
        console.print("[yellow]Waiting for index to initialize...[/yellow]")
        time.sleep(20)
        
        return pc.Index(name=index_name)
    
    except Exception as e:
        console.print(f"[bold red]Error creating Pinecone index: {str(e)}[/bold red]")
        return None

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='Generate BERT embeddings from Pinecone documents')
    parser.add_argument('--max-docs', type=int, default=5000, 
                        help='Maximum number of documents to process')
    parser.add_argument('--start-id', type=int, default=0,
                        help='ID to start fetching from')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Batch size for processing and upserting')
    parser.add_argument('--model-name', type=str, default='intfloat/e5-small-v2',
                        help='Name of the SentenceTransformer model to use')
    parser.add_argument('--source-index', type=str, default='holocron-knowledge',
                        help='Source Pinecone index to fetch documents from')
    parser.add_argument('--target-index', type=str, default='holocron-sbert-e5',
                        help='Target Pinecone index to store BERT embeddings')
    parser.add_argument('--device', type=str, default=None,
                        help='Device to use for computation (cpu, cuda, mps, or None for auto selection)')
    args = parser.parse_args()
    
    console.print("[bold cyan]Generating BERT Embeddings from Pinecone Documents[/bold cyan]")
    
    # Get Pinecone API key from environment
    pinecone_api_key = os.getenv('PINECONE_API_KEY')
    if not pinecone_api_key:
        console.print("[bold red]Error: Missing PINECONE_API_KEY in environment variables[/bold red]")
        return
    
    # Initialize Pinecone client
    pc = Pinecone(api_key=pinecone_api_key)
    
    # Initialize BERT embedder
    bert_embedder = BERTEmbedder(
        model_name=args.model_name,
        batch_size=args.batch_size,
        device=args.device
    )
    
    # Get source index
    try:
        source_index = pc.Index(name=args.source_index)
        console.print(f"[green]Connected to source index: {args.source_index}[/green]")
    except Exception as e:
        console.print(f"[bold red]Error connecting to source index: {str(e)}[/bold red]")
        return
    
    # Create target index
    target_index = create_pinecone_index(pc, args.target_index, bert_embedder.embedding_dim)
    if not target_index:
        console.print("[bold red]Failed to create or connect to target index[/bold red]")
        return
    
    # Record start time
    start_time = time.time()
    
    # Fetch documents from source index
    documents = fetch_documents_from_pinecone(
        source_index=source_index,
        max_documents=args.max_docs,
        start_id=args.start_id,
        batch_size=args.batch_size
    )
    
    if not documents:
        console.print("[bold red]No documents fetched from source index[/bold red]")
        return
    
    # Build BERT index
    vectors_uploaded = build_bert_index(
        documents=documents,
        bert_embedder=bert_embedder,
        target_index=target_index,
        batch_size=args.batch_size
    )
    
    # Calculate duration
    duration = time.time() - start_time
    
    # Final summary
    console.print("\n[bold]BERT Embeddings Generation Summary[/bold]")
    console.print(f"Source Index: [cyan]{args.source_index}[/cyan]")
    console.print(f"Target Index: [cyan]{args.target_index}[/cyan]")
    console.print(f"BERT Model: [cyan]{args.model_name}[/cyan]")
    console.print(f"Documents Processed: [cyan]{len(documents)}[/cyan]")
    console.print(f"Vectors Uploaded: [cyan]{vectors_uploaded}[/cyan]")
    console.print(f"Vector Dimensions: [cyan]{bert_embedder.embedding_dim}[/cyan]")
    console.print(f"Total processing time: [cyan]{duration:.2f} seconds[/cyan]")
    console.print(f"Average time per document: [cyan]{duration/len(documents):.4f} seconds[/cyan]")
    
    console.print("\n[bold green]BERT Embeddings Generation Complete[/bold green]")

if __name__ == "__main__":
    main() 