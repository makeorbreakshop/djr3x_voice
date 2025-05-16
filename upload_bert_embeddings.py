#!/usr/bin/env python3
"""
Upload BERT embeddings from a file to Pinecone.
This script loads BERT embeddings from a file and uploads them to a Pinecone index.
"""

import os
import sys
import logging
import json
import time
import argparse
from typing import List, Dict, Any
import numpy as np
from pinecone import Pinecone
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

def create_pinecone_index(pc, index_name, dimension):
    """
    Create a new Pinecone index for BERT embeddings if it doesn't exist.
    
    Args:
        pc: Pinecone client
        index_name: Name for the index
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

def load_and_upload_embeddings(input_file, target_index, batch_size=100):
    """
    Load BERT embeddings from a file and upload them to Pinecone.
    
    Args:
        input_file: File containing BERT embeddings
        target_index: Pinecone index to upload to
        batch_size: Size of batches for upserting
        
    Returns:
        Number of vectors successfully uploaded
    """
    console.print(f"[bold]Loading and uploading embeddings from {input_file}[/bold]")
    
    # Count lines in the file first to set up progress bar
    with open(input_file, 'r') as f:
        total_lines = sum(1 for _ in f)
    
    vectors_uploaded = 0
    
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        upload_task = progress.add_task("[green]Uploading vectors...", total=total_lines)
        
        # Process file in batches
        with open(input_file, 'r') as f:
            batch_vectors = []
            
            for line in f:
                # Parse vector from line
                try:
                    vector_obj = json.loads(line.strip())
                    batch_vectors.append(vector_obj)
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON: {e}")
                    continue
                
                # If batch is full, upsert to Pinecone
                if len(batch_vectors) >= batch_size:
                    try:
                        target_index.upsert(vectors=batch_vectors)
                        vectors_uploaded += len(batch_vectors)
                    except Exception as e:
                        logger.error(f"Error upserting batch: {str(e)}")
                    
                    batch_vectors = []
                    
                    # Add small delay to avoid rate limiting
                    time.sleep(0.1)
                
                # Update progress
                progress.update(upload_task, advance=1)
            
            # Upsert any remaining vectors
            if batch_vectors:
                try:
                    target_index.upsert(vectors=batch_vectors)
                    vectors_uploaded += len(batch_vectors)
                except Exception as e:
                    logger.error(f"Error upserting final batch: {str(e)}")
    
    console.print(f"[bold green]Successfully uploaded {vectors_uploaded} vectors to Pinecone[/bold green]")
    return vectors_uploaded

def main():
    """Main function to upload BERT embeddings to Pinecone."""
    parser = argparse.ArgumentParser(description='Upload BERT embeddings to Pinecone')
    parser.add_argument('--input-file', type=str, default="bert_embeddings.jsonl",
                        help='File containing BERT embeddings')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Batch size for upserting')
    parser.add_argument('--target-index-name', type=str, default="holocron-sbert-e5",
                        help='Name for the target Pinecone index')
    parser.add_argument('--dimension', type=int, default=384,
                        help='Dimension of BERT embeddings')
    args = parser.parse_args()
    
    console.print("[bold cyan]Uploading BERT Embeddings to Pinecone[/bold cyan]")
    
    # Check if input file exists
    if not os.path.exists(args.input_file):
        console.print(f"[bold red]Error:[/bold red] Input file '{args.input_file}' not found.")
        return
    
    # Get API keys
    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    
    if not pinecone_api_key:
        console.print("[bold red]Error:[/bold red] Missing Pinecone API key. Check your .env file.")
        return
    
    # Initialize Pinecone client
    pc = Pinecone(api_key=pinecone_api_key)
    
    # Create or access target BERT index
    target_index_name = args.target_index_name
    target_index = create_pinecone_index(pc, target_index_name, args.dimension)
    
    if not target_index:
        console.print("[bold red]Error:[/bold red] Failed to create or access target index.")
        return
    
    # Load and upload embeddings
    vectors_uploaded = load_and_upload_embeddings(
        args.input_file,
        target_index,
        batch_size=args.batch_size
    )
    
    # Final summary
    console.print("\n[bold]BERT Embeddings Upload Summary[/bold]")
    console.print(f"Input File: [cyan]{args.input_file}[/cyan]")
    console.print(f"Target Index: [cyan]{target_index_name}[/cyan]")
    console.print(f"Vector Dimensions: [cyan]{args.dimension}[/cyan]")
    console.print(f"Vectors Uploaded: [cyan]{vectors_uploaded}[/cyan]")
    
    console.print("\n[bold green]BERT Embeddings Upload Complete[/bold green]")

if __name__ == "__main__":
    main() 