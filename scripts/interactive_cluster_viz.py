#!/usr/bin/env python3
"""
Interactive Cluster Visualization
This script creates an interactive visualization of HDBSCAN clusters using Plotly.
Features:
- Zoom and pan capabilities
- Hover tooltips with document information
- Color-coded clusters with custom colorscale
- Cluster centroid labels
- Sidebar with cluster statistics
"""

import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
from rich.console import Console
import plotly.graph_objects as go
import plotly.express as px
from sklearn.feature_extraction.text import CountVectorizer
from collections import Counter
from pathlib import Path

# Configure console
console = Console()

def load_cluster_data(cluster_file):
    """
    Load cluster data from a JSON file.
    
    Args:
        cluster_file: Path to cluster JSON file
        
    Returns:
        Dictionary with cluster data
    """
    console.print(f"[bold]Loading cluster data from {cluster_file}[/bold]")
    
    try:
        with open(cluster_file, 'r') as f:
            data = json.load(f)
        console.print(f"[green]Successfully loaded cluster data with {len(data['reduced_vectors'])} vectors[/green]")
        return data
    except Exception as e:
        console.print(f"[bold red]Error loading cluster data: {e}[/bold red]")
        sys.exit(1)

def extract_cluster_keywords(vectors, cluster_labels, num_keywords=5):
    """
    Extract the top keywords for each cluster.
    
    Args:
        vectors: Dictionary of vectors with metadata
        cluster_labels: Cluster assignments
        num_keywords: Number of top keywords to extract
        
    Returns:
        Dictionary mapping cluster IDs to top keywords
    """
    console.print("[bold]Extracting cluster keywords...[/bold]")
    
    # Group vectors by cluster
    clusters = {}
    for i, cluster_id in enumerate(cluster_labels):
        if cluster_id not in clusters:
            clusters[cluster_id] = []
        
        vector_id = list(vectors.keys())[i]
        clusters[cluster_id].append(vector_id)
    
    # Extract keywords for each cluster
    cluster_keywords = {}
    for cluster_id, vector_ids in clusters.items():
        if cluster_id == -1:  # Skip noise points
            cluster_keywords[cluster_id] = ["Noise"]
            continue
            
        # Collect text from vectors in this cluster
        texts = []
        for vector_id in vector_ids:
            metadata = vectors[vector_id].get('metadata', {})
            if isinstance(metadata, dict):
                # Try to get content from metadata
                content = metadata.get('content', '')
                if not content and isinstance(metadata.get('metadata'), dict):
                    content = metadata['metadata'].get('content', '')
                
                # Get title if no content
                if not content:
                    title = metadata.get('title', '')
                    if not title and isinstance(metadata.get('metadata'), dict):
                        title = metadata['metadata'].get('title', '')
                    texts.append(title)
                else:
                    texts.append(content)
        
        # Extract keywords from text
        if texts:
            # Use simple word frequency for keyword extraction
            words = ' '.join(texts).lower().split()
            # Filter out common stop words
            stop_words = {'the', 'and', 'of', 'to', 'in', 'a', 'for', 'with', 'on', 'at', 'from', 'by'}
            filtered_words = [w for w in words if w not in stop_words and len(w) > 2]
            word_counts = Counter(filtered_words)
            top_keywords = [word for word, _ in word_counts.most_common(num_keywords)]
            cluster_keywords[cluster_id] = top_keywords
        else:
            cluster_keywords[cluster_id] = [f"Cluster {cluster_id}"]
    
    console.print(f"[green]Extracted keywords for {len(cluster_keywords)} clusters[/green]")
    return cluster_keywords

def create_interactive_plot(reduced_vectors, cluster_labels, vectors, cluster_keywords, title="Interactive Cluster Visualization"):
    """
    Create an interactive plot of clusters using Plotly.
    
    Args:
        reduced_vectors: 2D reduced vectors
        cluster_labels: Cluster assignments
        vectors: Original vectors with metadata
        cluster_keywords: Keywords for each cluster
        title: Plot title
        
    Returns:
        Plotly figure
    """
    console.print("[bold]Creating interactive visualization...[/bold]")
    
    # Create DataFrame for plotting
    df = pd.DataFrame({
        'x': reduced_vectors[:, 0],
        'y': reduced_vectors[:, 1],
        'cluster': [str(label) if label != -1 else 'noise' for label in cluster_labels],
        'size': [5 if label != -1 else 3 for label in cluster_labels],
        'alpha': [0.8 if label != -1 else 0.4 for label in cluster_labels]
    })
    
    # Add metadata for hover text
    vector_ids = list(vectors.keys())
    hover_texts = []
    
    for i in range(len(reduced_vectors)):
        vector_id = vector_ids[i]
        cluster_id = cluster_labels[i]
        
        # Get metadata
        metadata = vectors[vector_id].get('metadata', {})
        
        # Try to get title from metadata
        title = "Unknown"
        if isinstance(metadata, dict):
            title = metadata.get('title', '')
            if not title and isinstance(metadata.get('metadata'), dict):
                title = metadata['metadata'].get('title', '')
        
        # Format hover text
        keywords = ', '.join(cluster_keywords.get(cluster_id, ['Unknown']))
        hover_text = f"Title: {title}<br>Cluster: {cluster_id}<br>Topics: {keywords}"
        hover_texts.append(hover_text)
    
    df['hover_text'] = hover_texts
    
    # Count points in each cluster
    cluster_counts = Counter(cluster_labels)
    
    # Get unique clusters (excluding noise)
    unique_clusters = sorted(set(cluster_labels))
    if -1 in unique_clusters:
        unique_clusters.remove(-1)
    
    # Generate colorscale that skips black (which will be used for noise)
    colorscale = px.colors.qualitative.Plotly
    if len(unique_clusters) > len(colorscale):
        # If we have more clusters than colors, use a continuous colorscale
        colorscale = px.colors.sequential.Viridis
    
    # Create figure
    fig = go.Figure()
    
    # Add noise points first
    noise_idx = np.where(np.array(cluster_labels) == -1)[0]
    if len(noise_idx) > 0:
        noise_x = df['x'].iloc[noise_idx]
        noise_y = df['y'].iloc[noise_idx]
        noise_text = df['hover_text'].iloc[noise_idx]
        
        fig.add_trace(go.Scattergl(
            x=noise_x,
            y=noise_y,
            mode='markers',
            marker=dict(
                size=3,
                color='black',
                opacity=0.4
            ),
            text=noise_text,
            hoverinfo='text',
            name='Noise'
        ))
    
    # Add each cluster
    for i, cluster_id in enumerate(unique_clusters):
        cluster_idx = np.where(np.array(cluster_labels) == cluster_id)[0]
        cluster_x = df['x'].iloc[cluster_idx]
        cluster_y = df['y'].iloc[cluster_idx]
        cluster_text = df['hover_text'].iloc[cluster_idx]
        color_idx = i % len(colorscale)
        
        # Get keywords for cluster
        keywords = cluster_keywords.get(cluster_id, [])
        keyword_text = ', '.join(keywords)
        cluster_name = f"Cluster {cluster_id} ({cluster_counts[cluster_id]} points): {keyword_text}"
        
        fig.add_trace(go.Scattergl(
            x=cluster_x,
            y=cluster_y,
            mode='markers',
            marker=dict(
                size=6,
                color=colorscale[color_idx] if isinstance(colorscale, list) else None,
                opacity=0.7
            ),
            text=cluster_text,
            hoverinfo='text',
            name=cluster_name
        ))
        
        # Add cluster centroid label for larger clusters
        if cluster_counts[cluster_id] > 100:
            centroid_x = np.mean(cluster_x)
            centroid_y = np.mean(cluster_y)
            
            fig.add_trace(go.Scatter(
                x=[centroid_x],
                y=[centroid_y],
                mode='markers+text',
                marker=dict(
                    size=10,
                    color=colorscale[color_idx] if isinstance(colorscale, list) else None,
                    symbol='star',
                    line=dict(width=2, color='white')
                ),
                text=f"Cluster {cluster_id}",
                textposition="top center",
                textfont=dict(size=12, color='black'),
                hoverinfo='text',
                showlegend=False
            ))
    
    # Update layout
    fig.update_layout(
        title=dict(
            text=title,
            x=0.5,
            font=dict(size=24)
        ),
        legend=dict(
            itemsizing='constant',
            font=dict(size=10),
            orientation='v',
            yanchor='top',
            y=0.99,
            xanchor='right',
            x=0.99
        ),
        hovermode='closest',
        margin=dict(l=0, r=0, b=0, t=50),
        plot_bgcolor='white',
        width=1200,
        height=800
    )
    
    # Update axes
    fig.update_xaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor='lightgray',
        zeroline=False,
        showticklabels=False
    )
    
    fig.update_yaxes(
        showgrid=True,
        gridwidth=1,
        gridcolor='lightgray',
        zeroline=False,
        showticklabels=False
    )
    
    return fig

def get_largest_clusters(cluster_labels, n=10):
    """
    Get the largest n clusters.
    
    Args:
        cluster_labels: Cluster assignments
        n: Number of clusters to return
        
    Returns:
        List of (cluster_id, count) tuples
    """
    counter = Counter(cluster_labels)
    if -1 in counter:
        del counter[-1]  # Remove noise
    return counter.most_common(n)

def main():
    """Main function to create interactive visualization."""
    parser = argparse.ArgumentParser(description='Create interactive visualization of HDBSCAN clusters')
    parser.add_argument('--cluster-file', type=str, required=True,
                        help='Path to cluster data JSON file')
    parser.add_argument('--output-dir', type=str, default='viz',
                        help='Directory to save visualization')
    parser.add_argument('--title', type=str, default="Interactive Cluster Visualization",
                        help='Title for the visualization')
    args = parser.parse_args()
    
    console.print("[bold magenta]Interactive Cluster Visualization[/bold magenta]")
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Load cluster data
    cluster_data = load_cluster_data(args.cluster_file)
    reduced_vectors = np.array(cluster_data['reduced_vectors'])
    cluster_labels = np.array(cluster_data['cluster_labels'])
    vectors = cluster_data['vectors']
    
    # Extract cluster keywords
    cluster_keywords = extract_cluster_keywords(vectors, cluster_labels)
    
    # Create interactive plot
    fig = create_interactive_plot(
        reduced_vectors,
        cluster_labels,
        vectors,
        cluster_keywords,
        title=args.title
    )
    
    # Save visualization to HTML
    output_file = os.path.join(args.output_dir, 'interactive_clusters.html')
    fig.write_html(output_file, include_plotlyjs='cdn')
    console.print(f"[bold green]Interactive visualization saved to {output_file}[/bold green]")
    
    # Print largest clusters
    console.print("\n[bold]Top 10 Largest Clusters:[/bold]")
    largest_clusters = get_largest_clusters(cluster_labels, n=10)
    for cluster_id, count in largest_clusters:
        keywords = ', '.join(cluster_keywords.get(cluster_id, ['Unknown']))
        console.print(f"Cluster {cluster_id}: {count} vectors - Keywords: {keywords}")

if __name__ == "__main__":
    main() 