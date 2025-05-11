#!/usr/bin/env python3
"""
Compile Cluster Data
This script compiles individual cluster JSON files into a single file for visualization.
"""

import os
import sys
import json
import argparse
import glob
import numpy as np
from rich.console import Console

# Configure console
console = Console()

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

def compile_cluster_data(cluster_dir, output_file):
    """
    Compile individual cluster JSON files into a single file.
    
    Args:
        cluster_dir: Directory containing cluster JSON files
        output_file: Path to output JSON file
    """
    console.print(f"[bold]Compiling cluster data from {cluster_dir}[/bold]")
    
    # Find all cluster JSON files
    cluster_files = glob.glob(os.path.join(cluster_dir, "cluster_*.json"))
    noise_file = os.path.join(cluster_dir, "noise.json")
    
    if os.path.exists(noise_file):
        cluster_files.append(noise_file)
    
    if not cluster_files:
        console.print(f"[bold red]Error: No cluster files found in {cluster_dir}[/bold red]")
        return False
    
    console.print(f"Found {len(cluster_files)} cluster files")
    
    # Initialize compilation data
    compiled_data = {
        'reduced_vectors': [],
        'cluster_labels': [],
        'vectors': {}
    }
    
    vector_positions = {}  # Maps vector ID to position in reduced_vectors
    position_count = 0
    
    # Process each cluster file
    for cluster_file in cluster_files:
        try:
            with open(cluster_file, 'r') as f:
                cluster_data = json.load(f)
            
            cluster_id = cluster_data.get('cluster_id', 0)
            vectors = cluster_data.get('vectors', [])
            
            for vector in vectors:
                vector_id = vector.get('id')
                if vector_id:
                    # Store vector with metadata
                    compiled_data['vectors'][vector_id] = {
                        'metadata': vector.get('metadata', {})
                    }
                    
                    # Remember position for this vector ID
                    vector_positions[vector_id] = position_count
                    position_count += 1
                    
                    # Add placeholder for reduced vector (will be replaced later)
                    compiled_data['reduced_vectors'].append([0, 0])
                    
                    # Add cluster label
                    compiled_data['cluster_labels'].append(cluster_id)
        
        except Exception as e:
            console.print(f"[bold yellow]Warning: Error processing {cluster_file}: {e}[/bold yellow]")
    
    # Now let's try to extract the reduced vectors from PNG files
    try:
        # Find the primary PNG file
        png_files = glob.glob("hdbscan_clusters_*.png")
        
        if png_files:
            console.print(f"[yellow]Note: Extracting vectors from PNG is not possible. Using placeholder positions.[/yellow]")
            
            # Instead, we'll compute simple grid positions for visualization
            # This won't match the original t-SNE, but will at least group by clusters
            positions = compute_grid_positions(compiled_data['cluster_labels'])
            
            # Update reduced vectors with grid positions
            for i, pos in enumerate(positions):
                compiled_data['reduced_vectors'][i] = pos
    except Exception as e:
        console.print(f"[bold yellow]Warning: Could not extract reduced vectors: {e}[/bold yellow]")
        console.print("[yellow]Using placeholder positions for visualization[/yellow]")
    
    # Save to output file
    try:
        with open(output_file, 'w') as f:
            json.dump(compiled_data, f, cls=NumpyEncoder)
        
        console.print(f"[bold green]Successfully compiled cluster data to {output_file}[/bold green]")
        console.print(f"Compiled {len(compiled_data['cluster_labels'])} vectors with {len(set(compiled_data['cluster_labels']))} clusters")
        return True
    
    except Exception as e:
        console.print(f"[bold red]Error saving compiled data: {e}[/bold red]")
        return False

def compute_grid_positions(cluster_labels):
    """Compute grid positions based on cluster labels."""
    # Get unique clusters and their sizes
    clusters = {}
    for i, label in enumerate(cluster_labels):
        if label not in clusters:
            clusters[label] = []
        clusters[label].append(i)
    
    # Compute positions
    positions = [[0, 0] for _ in range(len(cluster_labels))]
    
    # Layout in a grid with each cluster getting a section
    grid_size = int(np.ceil(np.sqrt(len(clusters))))
    
    # Generate cluster positions
    for i, (cluster_id, indices) in enumerate(sorted(clusters.items())):
        # Place cluster in a grid cell
        grid_x = (i % grid_size) * 10
        grid_y = (i // grid_size) * 10
        
        # Compute size of mini-grid for this cluster
        cluster_size = len(indices)
        mini_grid = int(np.ceil(np.sqrt(cluster_size)))
        
        # Place each point in the cluster
        for j, idx in enumerate(indices):
            mini_x = j % mini_grid
            mini_y = j // mini_grid
            
            # Add some randomness to avoid perfect grid
            jitter_x = np.random.normal(0, 0.1)
            jitter_y = np.random.normal(0, 0.1)
            
            positions[idx] = [
                grid_x + mini_x + jitter_x,
                grid_y + mini_y + jitter_y
            ]
    
    return positions

def main():
    """Main function to compile cluster data."""
    parser = argparse.ArgumentParser(description='Compile cluster data for visualization')
    parser.add_argument('--cluster-dir', type=str, default="clusters",
                        help='Directory containing cluster JSON files')
    parser.add_argument('--output-file', type=str, default="cluster_data.json",
                        help='Path to output JSON file')
    args = parser.parse_args()
    
    console.print("[bold magenta]Cluster Data Compiler[/bold magenta]")
    
    # Compile data
    compile_cluster_data(args.cluster_dir, args.output_file)

if __name__ == "__main__":
    main() 