#!/usr/bin/env python3
"""
Generate a vector-to-cluster mapping from HDBSCAN clustering results.
This script creates a JSON file mapping vector IDs to their assigned cluster IDs
for use with the cluster-aware search system.
"""

import os
import sys
import json
import argparse
from typing import Dict, List
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn

# Configure console
console = Console()

def load_clusters(clusters_dir: str) -> Dict:
    """
    Load all cluster data from JSON files.
    
    Args:
        clusters_dir: Directory containing cluster JSON files
        
    Returns:
        Dictionary mapping cluster IDs to cluster data
    """
    clusters = {}
    cluster_files = []
    
    console.print(f"[bold]Loading clusters from {clusters_dir}[/bold]")
    
    # Find all cluster files
    for filename in os.listdir(clusters_dir):
        if filename.endswith('.json'):
            cluster_files.append(os.path.join(clusters_dir, filename))
    
    console.print(f"Found {len(cluster_files)} cluster files")
    
    # Load cluster data
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Loading cluster data...", total=len(cluster_files))
        
        for filepath in cluster_files:
            try:
                with open(filepath, 'r') as f:
                    cluster_data = json.load(f)
                    cluster_id = cluster_data['cluster_id']
                    clusters[cluster_id] = cluster_data
            except Exception as e:
                console.print(f"[bold red]Error loading {filepath}: {str(e)}[/bold red]")
            
            progress.update(task, advance=1)
    
    console.print(f"[green]Successfully loaded {len(clusters)} clusters[/green]")
    return clusters

def generate_vector_cluster_map(clusters: Dict) -> Dict[str, int]:
    """
    Generate a mapping from vector IDs to cluster IDs.
    
    Args:
        clusters: Dictionary mapping cluster IDs to cluster data
        
    Returns:
        Dictionary mapping vector IDs to cluster IDs
    """
    vector_cluster_map = {}
    
    console.print("[bold]Generating vector-to-cluster mapping...[/bold]")
    
    # Process each cluster
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Processing clusters...", total=len(clusters))
        
        for cluster_id, cluster_data in clusters.items():
            vectors = cluster_data.get('vectors', [])
            
            # Add each vector to the map
            for vector in vectors:
                vector_id = vector.get('id', '')
                if vector_id:
                    vector_cluster_map[vector_id] = cluster_id
            
            progress.update(task, advance=1)
    
    console.print(f"[green]Generated mapping for {len(vector_cluster_map)} vectors[/green]")
    return vector_cluster_map

def save_cluster_map(vector_cluster_map: Dict[str, int], output_file: str):
    """
    Save the vector-to-cluster mapping to a JSON file.
    
    Args:
        vector_cluster_map: Dictionary mapping vector IDs to cluster IDs
        output_file: Path to the output file
    """
    try:
        with open(output_file, 'w') as f:
            json.dump(vector_cluster_map, f, indent=2)
        
        console.print(f"[bold green]Saved vector-to-cluster mapping to {output_file}[/bold green]")
        console.print(f"Map contains {len(vector_cluster_map)} vector mappings")
    except Exception as e:
        console.print(f"[bold red]Error saving mapping: {str(e)}[/bold red]")

def main():
    """Main function to generate a vector-to-cluster mapping."""
    parser = argparse.ArgumentParser(description='Generate a vector-to-cluster mapping')
    parser.add_argument('--clusters-dir', type=str, default="clusters",
                      help='Directory containing cluster JSON files')
    parser.add_argument('--output', type=str, default="cluster_map.json",
                      help='Output file for the vector-to-cluster mapping')
    args = parser.parse_args()
    
    console.print("[bold magenta]Vector-to-Cluster Mapping Generator[/bold magenta]")
    
    # Verify clusters directory exists
    if not os.path.exists(args.clusters_dir):
        console.print(f"[bold red]Error:[/bold red] Clusters directory {args.clusters_dir} not found.")
        return
    
    # Load clusters
    clusters = load_clusters(args.clusters_dir)
    
    if not clusters:
        console.print("[bold red]Error:[/bold red] No clusters found.")
        return
    
    # Generate vector-to-cluster mapping
    vector_cluster_map = generate_vector_cluster_map(clusters)
    
    if not vector_cluster_map:
        console.print("[bold red]Error:[/bold red] Failed to generate mapping.")
        return
    
    # Save mapping
    save_cluster_map(vector_cluster_map, args.output)
    
    # Print stats about the mapping
    noise_vectors = sum(1 for cluster_id in vector_cluster_map.values() if cluster_id == -1)
    clustered_vectors = len(vector_cluster_map) - noise_vectors
    
    console.print(f"\n[bold]Mapping Statistics:[/bold]")
    console.print(f"Total vectors: {len(vector_cluster_map)}")
    console.print(f"Clustered vectors: {clustered_vectors} ({clustered_vectors / len(vector_cluster_map) * 100:.1f}%)")
    console.print(f"Noise vectors: {noise_vectors} ({noise_vectors / len(vector_cluster_map) * 100:.1f}%)")
    
    console.print("\n[bold green]Vector-to-Cluster Mapping Complete[/bold green]")
    console.print(f"Use with cluster-aware search: python scripts/cluster_aware_search.py --cluster-map {args.output} <query>")

if __name__ == "__main__":
    main() 