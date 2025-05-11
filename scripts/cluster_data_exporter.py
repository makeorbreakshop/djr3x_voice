#!/usr/bin/env python3
"""
Cluster Data Exporter
This script exports HDBSCAN cluster data to a JSON file for visualization.
"""

import os
import sys
import json
import argparse
import numpy as np
from rich.console import Console
from pathlib import Path

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

def export_cluster_data(cluster_file, output_file):
    """
    Export HDBSCAN cluster data to a JSON file.
    
    Args:
        cluster_file: Path to HDBSCAN pickle or npy file
        output_file: Path to output JSON file
    """
    console.print(f"[bold]Exporting cluster data from {cluster_file}[/bold]")
    
    try:
        # Check if file exists
        if not os.path.exists(cluster_file):
            console.print(f"[bold red]Error: File {cluster_file} not found[/bold red]")
            return False
        
        # Load data based on file extension
        ext = os.path.splitext(cluster_file)[1].lower()
        
        if ext == '.pickle' or ext == '.pkl':
            import pickle
            with open(cluster_file, 'rb') as f:
                data = pickle.load(f)
        elif ext == '.npy':
            data = np.load(cluster_file, allow_pickle=True).item()
        else:
            console.print(f"[bold red]Error: Unsupported file extension {ext}[/bold red]")
            return False
            
        # Extract required data
        export_data = {
            'reduced_vectors': data.get('reduced_vectors', []),
            'cluster_labels': data.get('cluster_labels', []),
            'vectors': data.get('vectors', {})
        }
        
        # Save to JSON
        with open(output_file, 'w') as f:
            json.dump(export_data, f, cls=NumpyEncoder)
            
        console.print(f"[green]Successfully exported cluster data to {output_file}[/green]")
        console.print(f"Exported {len(export_data['reduced_vectors'])} vectors with {len(set(export_data['cluster_labels']))} clusters")
        return True
        
    except Exception as e:
        console.print(f"[bold red]Error exporting cluster data: {e}[/bold red]")
        return False

def main():
    """Main function to export cluster data."""
    parser = argparse.ArgumentParser(description='Export HDBSCAN cluster data to JSON')
    parser.add_argument('--input-file', type=str, required=True,
                        help='Path to HDBSCAN pickle or npy file')
    parser.add_argument('--output-file', type=str, default=None,
                        help='Path to output JSON file (defaults to same name with .json extension)')
    args = parser.parse_args()
    
    console.print("[bold magenta]Cluster Data Exporter[/bold magenta]")
    
    # Set default output file if not provided
    if args.output_file is None:
        args.output_file = os.path.splitext(args.input_file)[0] + '.json'
    
    # Create output directory if it doesn't exist
    output_dir = os.path.dirname(args.output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    # Export data
    export_cluster_data(args.input_file, args.output_file)

if __name__ == "__main__":
    main() 