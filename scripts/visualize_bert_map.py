#!/usr/bin/env python3
"""
Visualize BERT embeddings in a 2D map.
This script generates a visual representation of semantic relationships in the vector space.
"""

import os
import sys
import logging
import json
import argparse
import numpy as np
import matplotlib.pyplot as plt
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA
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

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def fetch_vectors(index, max_vectors=10000, start_id=1000):
    """
    Fetch vectors from the Pinecone index.
    
    Args:
        index: Pinecone index to fetch from
        max_vectors: Maximum number of vectors to fetch
        start_id: ID to start fetching from
        
    Returns:
        Dictionary mapping ID to vector and metadata
    """
    vectors = {}
    
    # Get index stats
    stats = index.describe_index_stats()
    total_vector_count = stats.get('total_vector_count', 0)
    
    console.print(f"[bold]Fetching up to {max_vectors} vectors from index[/bold]")
    console.print(f"Total vectors in index: {total_vector_count}")
    console.print(f"Starting from ID: {start_id}")
    
    # Create batch fetching progress
    batch_size = 100
    end_id = min(start_id + max_vectors, total_vector_count)
    
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Fetching vectors...", total=min(max_vectors, end_id-start_id))
        
        for batch_start in range(start_id, end_id, batch_size):
            batch_end = min(batch_start + batch_size, end_id)
            ids = [str(i) for i in range(batch_start, batch_end)]
            
            try:
                # Fetch vectors in batch
                result = index.fetch(ids=ids)
                
                # Process each vector
                for id, vector_data in result.vectors.items():
                    if hasattr(vector_data, 'values') and vector_data.values:
                        # Extract metadata title if available
                        title = None
                        if hasattr(vector_data, 'metadata') and vector_data.metadata:
                            title = vector_data.metadata.get('title', None)
                        
                        vectors[id] = {
                            'values': vector_data.values,
                            'title': title
                        }
            
            except Exception as e:
                logger.error(f"Error fetching batch {batch_start}-{batch_end}: {str(e)}")
            
            # Update progress
            progress.update(task, advance=batch_end-batch_start)
    
    console.print(f"[green]Successfully fetched {len(vectors)} vectors[/green]")
    return vectors

def dimension_reduction(vectors, method='tsne', perplexity=30, n_components=2):
    """
    Reduce dimensionality of vectors for visualization.
    
    Args:
        vectors: Dictionary of vectors to reduce
        method: 'tsne' or 'pca'
        perplexity: Perplexity parameter for t-SNE
        n_components: Number of components in output (2 or 3)
        
    Returns:
        Numpy array of reduced vectors
    """
    # Extract vectors and IDs
    ids = list(vectors.keys())
    vector_values = np.array([vectors[id]['values'] for id in ids])
    
    console.print(f"[bold]Reducing dimensionality of {len(vector_values)} vectors using {method.upper()}[/bold]")
    console.print(f"Original dimension: {vector_values.shape[1]}")
    console.print(f"Target dimension: {n_components}D")
    
    # Apply dimension reduction
    if method.lower() == 'tsne':
        reducer = TSNE(
            n_components=n_components,
            perplexity=perplexity,
            random_state=42,
            n_iter=1000,
            verbose=1
        )
    else:  # PCA
        reducer = PCA(n_components=n_components)
    
    # Reduce dimensions
    reduced_vectors = reducer.fit_transform(vector_values)
    
    console.print(f"[green]Dimensionality reduction complete: {reduced_vectors.shape}[/green]")
    
    return ids, reduced_vectors

def plot_vectors_2d(ids, reduced_vectors, vectors, selected_categories=None, title="BERT Embedding Map"):
    """
    Plot vectors in 2D with labels.
    
    Args:
        ids: List of vector IDs
        reduced_vectors: 2D reduced vectors
        vectors: Original vectors dictionary with metadata
        selected_categories: Categories to highlight (not implemented yet)
        title: Plot title
    """
    plt.figure(figsize=(20, 16))
    
    # Create scatter plot
    plt.scatter(
        reduced_vectors[:, 0],
        reduced_vectors[:, 1],
        alpha=0.5,
        s=10
    )
    
    # Add labels for a subset of points
    label_step = max(1, len(ids) // 40)  # Label about 40 points
    
    for i in range(0, len(ids), label_step):
        label = vectors[ids[i]].get('title', ids[i])
        if label:
            # Truncate long titles
            if isinstance(label, str) and len(label) > 30:
                label = label[:27] + "..."
            plt.annotate(
                label,
                (reduced_vectors[i, 0], reduced_vectors[i, 1]),
                fontsize=8,
                alpha=0.7
            )
    
    # Set title and labels
    plt.title(title, fontsize=16)
    plt.xlabel('Dimension 1')
    plt.ylabel('Dimension 2')
    plt.tight_layout()
    
    # Save figure
    output_file = f"bert_map_{title.lower().replace(' ', '_')}.png"
    plt.savefig(output_file, dpi=300)
    console.print(f"[bold green]Map saved to {output_file}[/bold green]")
    
    # Show plot
    plt.show()

def main():
    """Main function to create and visualize BERT embedding map."""
    parser = argparse.ArgumentParser(description='Visualize BERT embeddings in a 2D map')
    parser.add_argument('--index-name', type=str, default="holocron-sbert-e5",
                        help='Name of the Pinecone index')
    parser.add_argument('--max-vectors', type=int, default=10000,
                        help='Maximum number of vectors to visualize')
    parser.add_argument('--start-id', type=int, default=1000,
                        help='ID to start fetching from')
    parser.add_argument('--method', type=str, choices=['tsne', 'pca'], default='tsne',
                        help='Dimension reduction method')
    parser.add_argument('--perplexity', type=int, default=30,
                        help='Perplexity parameter for t-SNE')
    parser.add_argument('--title', type=str, default="BERT E5-small-v2 Embedding Map",
                        help='Title for the plot')
    args = parser.parse_args()
    
    console.print("[bold magenta]BERT Embedding Map Visualization[/bold magenta]")
    
    # Get API key
    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    
    if not pinecone_api_key:
        console.print("[bold red]Error:[/bold red] Missing Pinecone API key. Check your .env file.")
        return
    
    # Initialize Pinecone client
    pc = Pinecone(api_key=pinecone_api_key)
    
    # List available indexes
    indexes = pc.list_indexes()
    index_names = [index_info.name for index_info in indexes]
    
    console.print(f"Available indexes: {', '.join(index_names)}")
    
    # Connect to index
    if args.index_name in index_names:
        index = pc.Index(name=args.index_name)
        console.print(f"[green]Connected to index: {args.index_name}[/green]")
    else:
        console.print(f"[bold red]Error:[/bold red] Index '{args.index_name}' not found.")
        return
    
    # Fetch vectors
    vectors = fetch_vectors(
        index,
        max_vectors=args.max_vectors,
        start_id=args.start_id
    )
    
    if not vectors:
        console.print("[bold red]Error:[/bold red] No vectors fetched from index.")
        return
    
    # Reduce dimensionality
    ids, reduced_vectors = dimension_reduction(
        vectors,
        method=args.method,
        perplexity=args.perplexity
    )
    
    # Plot vectors
    plot_vectors_2d(
        ids,
        reduced_vectors,
        vectors,
        title=args.title
    )
    
    console.print("[bold green]BERT Embedding Map Visualization Complete[/bold green]")

if __name__ == "__main__":
    main() 