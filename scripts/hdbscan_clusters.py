#!/usr/bin/env python3
"""
HDBSCAN clustering of BERT embeddings.
This script performs clustering analysis on E5-small-v2 embedding vectors
and visualizes the results with colored clusters.
"""

import os
import sys
import logging
import json
import argparse
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA
import hdbscan
from pinecone import Pinecone
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Custom JSON encoder to handle numpy types
class NumpyEncoder(json.JSONEncoder):
    """Custom encoder for numpy data types."""
    def default(self, obj):
        if isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float32, np.float64)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super(NumpyEncoder, self).default(obj)

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
                        # Extract metadata
                        metadata = {}
                        if hasattr(vector_data, 'metadata') and vector_data.metadata:
                            metadata = vector_data.metadata
                        
                        vectors[id] = {
                            'values': vector_data.values,
                            'metadata': metadata
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

def cluster_vectors(reduced_vectors, min_cluster_size=15, min_samples=5):
    """
    Cluster vectors using HDBSCAN.
    
    Args:
        reduced_vectors: Dimensionality-reduced vectors
        min_cluster_size: Minimum size of clusters
        min_samples: Minimum samples for core points
        
    Returns:
        Cluster labels for each vector
    """
    console.print(f"[bold]Clustering {reduced_vectors.shape[0]} vectors with HDBSCAN[/bold]")
    console.print(f"Min cluster size: {min_cluster_size}, Min samples: {min_samples}")
    
    # Create and fit HDBSCAN clusterer
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric='euclidean',
        cluster_selection_epsilon=0.5,
        prediction_data=True
    )
    
    # Fit clusterer
    cluster_labels = clusterer.fit_predict(reduced_vectors)
    
    # Get cluster statistics
    n_clusters = len(set(cluster_labels)) - (1 if -1 in cluster_labels else 0)
    n_noise = list(cluster_labels).count(-1)
    
    console.print(f"[green]Clustering complete: {n_clusters} clusters identified[/green]")
    console.print(f"Noise points: {n_noise} ({n_noise/len(cluster_labels)*100:.1f}%)")
    
    return cluster_labels, clusterer

def analyze_clusters(vectors, ids, cluster_labels):
    """
    Analyze cluster contents and print statistics.
    
    Args:
        vectors: Dictionary of vectors and metadata
        ids: List of vector IDs
        cluster_labels: Cluster assignment for each vector
    """
    # Count vectors in each cluster
    unique_clusters = sorted(set(cluster_labels))
    cluster_counts = {c: list(cluster_labels).count(c) for c in unique_clusters}
    
    # Create cluster statistics table
    table = Table(title="Cluster Statistics")
    table.add_column("Cluster ID", justify="right", style="cyan")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Percentage", justify="right", style="yellow")
    table.add_column("Top Categories", style="magenta")
    
    # Track categories in each cluster
    cluster_categories = {c: {} for c in unique_clusters}
    
    # Analyze metadata in each cluster
    for i, cluster_id in enumerate(cluster_labels):
        vector_id = ids[i]
        metadata = vectors[vector_id].get('metadata', {})
        
        # Extract categories from metadata
        categories = []
        if 'metadata' in metadata:
            # Try to extract nested metadata categories
            if isinstance(metadata['metadata'], dict) and 'category' in metadata['metadata']:
                categories.append(metadata['metadata']['category'])
        elif 'category' in metadata:
            categories.append(metadata['category'])
        
        # Add categories to cluster count
        for category in categories:
            if category:
                if category not in cluster_categories[cluster_id]:
                    cluster_categories[cluster_id][category] = 0
                cluster_categories[cluster_id][category] += 1
    
    # Add rows to table
    total_vectors = len(cluster_labels)
    for cluster_id in unique_clusters:
        count = cluster_counts[cluster_id]
        percentage = count / total_vectors * 100
        
        # Get top categories
        if cluster_categories[cluster_id]:
            top_cats = sorted(
                cluster_categories[cluster_id].items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]
            top_cats_str = ", ".join([f"{cat} ({count})" for cat, count in top_cats])
        else:
            top_cats_str = "N/A"
        
        # Special case for noise points
        if cluster_id == -1:
            table.add_row(
                "Noise",
                str(count),
                f"{percentage:.1f}%",
                top_cats_str
            )
        else:
            table.add_row(
                str(cluster_id),
                str(count),
                f"{percentage:.1f}%",
                top_cats_str
            )
    
    console.print(table)
    return cluster_categories

def plot_clusters(ids, reduced_vectors, vectors, cluster_labels, title="HDBSCAN Clusters"):
    """
    Plot clusters with different colors.
    
    Args:
        ids: List of vector IDs
        reduced_vectors: 2D reduced vectors
        vectors: Original vectors dictionary with metadata
        cluster_labels: Cluster assignment for each vector
        title: Plot title
    """
    plt.figure(figsize=(20, 16))
    
    # Generate colors for clusters
    unique_clusters = sorted(set(cluster_labels))
    n_clusters = len(unique_clusters)
    
    # Create colormap (excluding black which we'll use for noise points)
    colors = plt.cm.tab20(np.linspace(0, 1, n_clusters - (1 if -1 in unique_clusters else 0)))
    
    # Plot each cluster
    for i, cluster_id in enumerate(unique_clusters):
        if cluster_id == -1:
            # Plot noise points in black
            cluster_points = reduced_vectors[cluster_labels == cluster_id]
            plt.scatter(
                cluster_points[:, 0],
                cluster_points[:, 1],
                s=10,
                c='black',
                alpha=0.5,
                label=f'Noise'
            )
        else:
            # Plot cluster points with color
            cluster_points = reduced_vectors[cluster_labels == cluster_id]
            plt.scatter(
                cluster_points[:, 0],
                cluster_points[:, 1],
                s=30,
                c=[colors[i-1 if i > 0 else i]],  # Adjust for noise at index 0
                alpha=0.7,
                label=f'Cluster {cluster_id}'
            )
    
    # Add labels for some points in each cluster
    for cluster_id in unique_clusters:
        # Get indices of points in this cluster
        cluster_indices = np.where(cluster_labels == cluster_id)[0]
        
        # If cluster has few points, label them all
        # Otherwise sample a few points
        if len(cluster_indices) <= 5:
            label_indices = cluster_indices
        else:
            # Pick points near cluster center
            cluster_center = np.mean(reduced_vectors[cluster_indices], axis=0)
            distances = np.linalg.norm(
                reduced_vectors[cluster_indices] - cluster_center,
                axis=1
            )
            closest_indices = np.argsort(distances)[:3]
            label_indices = cluster_indices[closest_indices]
        
        # Add labels
        for idx in label_indices:
            vector_id = ids[idx]
            label = None
            
            # Try to get title from metadata
            metadata = vectors[vector_id].get('metadata', {})
            if 'title' in metadata:
                label = metadata['title']
            elif 'metadata' in metadata and isinstance(metadata['metadata'], dict) and 'title' in metadata['metadata']:
                label = metadata['metadata']['title']
            
            # Use ID if no title found
            if not label:
                label = vector_id
            
            # Truncate long titles
            if isinstance(label, str) and len(label) > 30:
                label = label[:27] + "..."
            
            plt.annotate(
                label,
                (reduced_vectors[idx, 0], reduced_vectors[idx, 1]),
                fontsize=8,
                alpha=0.9
            )
    
    # Add legend (only show first 10 clusters to avoid overcrowding)
    if len(unique_clusters) > 10:
        handles, labels = plt.gca().get_legend_handles_labels()
        plt.legend(handles[:10], labels[:10], loc='upper right', title="Top 10 Clusters")
    else:
        plt.legend(loc='upper right', title="Clusters")
    
    # Set title and labels
    plt.title(title, fontsize=16)
    plt.xlabel('Dimension 1')
    plt.ylabel('Dimension 2')
    plt.tight_layout()
    
    # Save figure
    output_file = f"hdbscan_clusters_{title.lower().replace(' ', '_')}.png"
    plt.savefig(output_file, dpi=300)
    console.print(f"[bold green]Cluster map saved to {output_file}[/bold green]")
    
    # Show plot
    plt.show()

def extract_cluster_data(vectors, ids, cluster_labels, cluster_id):
    """
    Extract data for a specific cluster.
    
    Args:
        vectors: Dictionary of vectors and metadata
        ids: List of vector IDs
        cluster_labels: Cluster labels
        cluster_id: Cluster ID to extract
        
    Returns:
        List of dictionaries with vector data for the cluster
    """
    cluster_data = []
    
    # Find indices of vectors in this cluster
    cluster_indices = np.where(cluster_labels == cluster_id)[0]
    
    for idx in cluster_indices:
        vector_id = ids[idx]
        vector_data = vectors[vector_id]
        
        # Extract title and metadata
        metadata = vector_data.get('metadata', {})
        title = metadata.get('title', None)
        
        if not title and 'metadata' in metadata and isinstance(metadata['metadata'], dict):
            title = metadata['metadata'].get('title', None)
        
        # Create data entry
        data_entry = {
            'id': vector_id,
            'title': title if title else f"Vector {vector_id}",
            'metadata': metadata
        }
        
        cluster_data.append(data_entry)
    
    return cluster_data

def export_cluster_data(vectors, ids, reduced_vectors, cluster_labels, output_dir="clusters"):
    """
    Export detailed information about each cluster to JSON files.
    
    Args:
        vectors: Dictionary of vectors and metadata
        ids: List of vector IDs
        reduced_vectors: 2D reduced vectors
        cluster_labels: Cluster labels
        output_dir: Directory to save cluster data
    """
    # Create output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Export data for each cluster
    unique_clusters = sorted(set(cluster_labels))
    
    # First, save the complete data including reduced vectors
    complete_data = {
        'vectors': vectors,
        'ids': ids,
        'reduced_vectors': reduced_vectors.tolist() if hasattr(reduced_vectors, 'tolist') else reduced_vectors,
        'cluster_labels': cluster_labels.tolist() if hasattr(cluster_labels, 'tolist') else cluster_labels
    }
    
    # Save complete data as a JSON file
    complete_data_file = os.path.join(output_dir, "complete_cluster_data.json")
    with open(complete_data_file, 'w') as f:
        json.dump(complete_data, f, cls=NumpyEncoder)
    
    console.print(f"[bold green]Complete cluster data saved to {complete_data_file}[/bold green]")
    
    # Now export individual cluster files
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Exporting cluster data...", total=len(unique_clusters))
        
        for cluster_id in unique_clusters:
            # Extract cluster data
            cluster_data = extract_cluster_data(vectors, ids, cluster_labels, cluster_id)
            
            # Skip empty clusters
            if not cluster_data:
                progress.update(task, advance=1)
                continue
            
            # Create cluster name (noise or cluster_X)
            if cluster_id == -1:
                cluster_name = "noise"
            else:
                cluster_name = f"cluster_{cluster_id}"
            
            # Save to JSON file
            output_file = os.path.join(output_dir, f"{cluster_name}.json")
            
            with open(output_file, 'w') as f:
                json.dump({
                    'cluster_id': cluster_id,
                    'size': len(cluster_data),
                    'vectors': cluster_data
                }, f, indent=2, cls=NumpyEncoder)
            
            progress.update(task, advance=1)
    
    console.print(f"[bold green]Cluster data exported to {output_dir}/[/bold green]")

def main():
    """Main function to cluster and visualize BERT embeddings."""
    parser = argparse.ArgumentParser(description='Perform HDBSCAN clustering on BERT embeddings')
    parser.add_argument('--index-name', type=str, default="holocron-sbert-e5",
                        help='Name of the Pinecone index')
    parser.add_argument('--max-vectors', type=int, default=10000,
                        help='Maximum number of vectors to visualize')
    parser.add_argument('--start-id', type=int, default=1000,
                        help='ID to start fetching from')
    parser.add_argument('--reduction-method', type=str, choices=['tsne', 'pca'], default='tsne',
                        help='Dimension reduction method')
    parser.add_argument('--perplexity', type=int, default=30,
                        help='Perplexity parameter for t-SNE')
    parser.add_argument('--min-cluster-size', type=int, default=15,
                        help='Minimum cluster size for HDBSCAN')
    parser.add_argument('--min-samples', type=int, default=5,
                        help='Minimum samples parameter for HDBSCAN')
    parser.add_argument('--title', type=str, default="E5-small-v2 HDBSCAN Clusters",
                        help='Title for the plot')
    parser.add_argument('--export-dir', type=str, default="clusters",
                        help='Directory to export cluster data')
    parser.add_argument('--save-complete-data', action='store_true',
                        help='Save complete data including vector positions')
    args = parser.parse_args()
    
    console.print("[bold magenta]BERT Embedding Clustering with HDBSCAN[/bold magenta]")
    
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
        method=args.reduction_method,
        perplexity=args.perplexity
    )
    
    # Cluster vectors
    cluster_labels, clusterer = cluster_vectors(
        reduced_vectors,
        min_cluster_size=args.min_cluster_size,
        min_samples=args.min_samples
    )
    
    # Analyze clusters
    analyze_clusters(vectors, ids, cluster_labels)
    
    # Plot clusters
    plot_clusters(
        ids,
        reduced_vectors,
        vectors,
        cluster_labels,
        title=args.title
    )
    
    # Export cluster data
    export_cluster_data(
        vectors,
        ids,
        reduced_vectors,
        cluster_labels,
        output_dir=args.export_dir
    )
    
    console.print("[bold green]HDBSCAN Clustering Complete[/bold green]")

if __name__ == "__main__":
    main() 