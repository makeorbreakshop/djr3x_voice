#!/usr/bin/env python3
"""
Create a BERT embeddings index for the Holocron Knowledge Base.
This script builds a new Pinecone index with BERT embeddings from existing data.
"""

import os
import sys
import logging
import json
import time
import argparse
from typing import List, Dict, Any
import numpy as np
from tqdm import tqdm
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate embeddings for a single query text.
        
        Args:
            text: The text to embed
            
        Returns:
            List of embedding values
        """
        try:
            embedding = self.model.encode(text)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Error generating BERT embedding: {e}")
            # Return zero vector as fallback
            return [0.0] * self.embedding_dim
    
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

def create_pinecone_index(pc, index_name, dimension):
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

def fetch_documents(source_index, max_documents=1000, start_id=1, end_id=None):
    """
    Fetch documents from the source Pinecone index.
    
    Args:
        source_index: Pinecone index to fetch from
        max_documents: Maximum number of documents to fetch
        start_id: ID to start fetching from
        end_id: ID to stop fetching at (optional)
        
    Returns:
        List of documents with content and metadata
    """
    documents = []
    
    # Get index stats to determine total vectors
    stats = source_index.describe_index_stats()
    total_vectors = stats.get('total_vector_count', 0)
    
    if end_id is None:
        end_id = total_vectors + 1
    
    # Adjust end_id based on max_documents
    if start_id + max_documents < end_id:
        end_id = start_id + max_documents
    
    console.print(f"[bold]Fetching up to {max_documents} documents from index[/bold]")
    console.print(f"Total vectors in index: {total_vectors}")
    console.print(f"ID range: {start_id} to {end_id}")
    
    # Create batch fetching progress
    batch_size = 100
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
                vectors = source_index.fetch(ids=ids)
                
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
            
            except Exception as e:
                logger.error(f"Error fetching batch {batch_start}-{batch_end}: {str(e)}")
            
            # Update progress
            progress.update(task, advance=batch_end-batch_start)
            
            # Add small delay to avoid rate limiting
            time.sleep(0.1)
    
    console.print(f"[green]Successfully fetched {len(documents)} documents[/green]")
    return documents

def build_bert_index(documents, bert_embeddings, target_index, batch_size=100):
    """
    Build the BERT embeddings index by generating embeddings and upserting to Pinecone.
    
    Args:
        documents: List of documents to process
        bert_embeddings: BERTEmbeddings instance
        target_index: Pinecone index to upsert to
        batch_size: Size of batches for processing and upserting
        
    Returns:
        Number of vectors successfully uploaded
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
            batch_embeddings = bert_embeddings.embed_texts(batch_contents)
            progress.update(embedding_task, advance=len(batch_docs))
            
            # Prepare vectors for upsert
            vectors_to_upsert = []
            
            for j, doc in enumerate(batch_docs):
                # Extract metadata (exclude any existing embeddings)
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
            time.sleep(0.1)
    
    console.print(f"[bold green]Successfully uploaded {vectors_uploaded} vectors to BERT index[/bold green]")
    return vectors_uploaded

def main():
    """Main function to create BERT embeddings index."""
    parser = argparse.ArgumentParser(description='Create BERT embeddings index for Holocron')
    parser.add_argument('--max-docs', type=int, default=5000, 
                        help='Maximum number of documents to process')
    parser.add_argument('--start-id', type=int, default=1,
                        help='ID to start fetching from')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Batch size for processing and upserting')
    parser.add_argument('--bert-model', type=str, default="intfloat/e5-small-v2",
                        help='BERT model name from sentence-transformers')
    parser.add_argument('--target-index-name', type=str, default="holocron-sbert",
                        help='Name for the target Pinecone index')
    args = parser.parse_args()
    
    console.print("[bold cyan]Creating BERT Embeddings Index for Holocron Knowledge Base[/bold cyan]")
    
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
    source_index_name = "holocron-knowledge"
    source_index = None
    
    indexes = pc.list_indexes()
    for index_info in indexes:
        if index_info.name == source_index_name:
            source_index = pc.Index(name=source_index_name)
            break
    
    if not source_index:
        console.print(f"[bold red]Error:[/bold red] Source index '{source_index_name}' not found.")
        return
    
    # Create target BERT index
    target_index_name = args.target_index_name
    target_index = create_pinecone_index(pc, target_index_name, bert_embeddings.embedding_dim)
    
    if not target_index:
        console.print("[bold red]Error:[/bold red] Failed to create or access target index.")
        return
    
    # Fetch documents from source index
    documents = fetch_documents(
        source_index, 
        max_documents=args.max_docs,
        start_id=args.start_id
    )
    
    if not documents:
        console.print("[bold red]Error:[/bold red] No documents fetched from source index.")
        return
    
    # Build BERT index
    vectors_uploaded = build_bert_index(
        documents,
        bert_embeddings,
        target_index,
        batch_size=args.batch_size
    )
    
    # Final summary
    console.print("\n[bold]BERT Index Creation Summary[/bold]")
    console.print(f"Source Index: [cyan]{source_index_name}[/cyan]")
    console.print(f"Target Index: [cyan]{target_index_name}[/cyan]")
    console.print(f"BERT Model: [cyan]{args.bert_model}[/cyan]")
    console.print(f"Documents Processed: [cyan]{len(documents)}[/cyan]")
    console.print(f"Vectors Uploaded: [cyan]{vectors_uploaded}[/cyan]")
    console.print(f"Vector Dimensions: [cyan]{bert_embeddings.embedding_dim}[/cyan]")
    
    console.print("\n[bold green]BERT Index Creation Complete[/bold green]")

if __name__ == "__main__":
    main() 