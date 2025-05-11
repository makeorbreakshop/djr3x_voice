#!/usr/bin/env python3
"""
Improved Interactive Cluster Visualization
This script creates an interactive visualization of HDBSCAN clusters using Plotly.
Features:
- Uses actual t-SNE coordinates for a natural graph layout
- Zoom and pan capabilities
- Hover tooltips with document information
- Color-coded clusters with custom colorscale
- Cluster centroid labels with keywords
- Interactive filtering and selection
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
            # Filter out common stop words and short words
            stop_words = {'the', 'and', 'of', 'to', 'in', 'a', 'for', 'with', 'on', 'at', 'from', 'by', 
                         'is', 'was', 'were', 'be', 'this', 'that', 'have', 'has', 'had', 'are', 'not'}
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
        reduced_vectors: 2D reduced vectors from t-SNE
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
        'x': [vec[0] for vec in reduced_vectors],
        'y': [vec[1] for vec in reduced_vectors],
        'cluster': [str(label) if label != -1 else 'noise' for label in cluster_labels],
        'size': [5 if label != -1 else 3 for label in cluster_labels],
        'alpha': [0.8 if label != -1 else 0.3 for label in cluster_labels]
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
    
    # Generate colorscale
    if len(unique_clusters) <= 10:
        colorscale = px.colors.qualitative.G10
    elif len(unique_clusters) <= 20:
        colorscale = px.colors.qualitative.Light24
    else:
        # For more clusters, use a continuous colorscale with discrete colors
        colorscale = px.colors.sequential.Viridis
    
    # Create figure
    fig = go.Figure()
    
    # Add noise points first (in gray)
    noise_idx = np.where(np.array(cluster_labels) == -1)[0]
    if len(noise_idx) > 0:
        noise_x = [reduced_vectors[i][0] for i in noise_idx]
        noise_y = [reduced_vectors[i][1] for i in noise_idx]
        noise_text = [hover_texts[i] for i in noise_idx]
        
        fig.add_trace(go.Scattergl(
            x=noise_x,
            y=noise_y,
            mode='markers',
            marker=dict(
                size=3,
                color='lightgray',
                opacity=0.3
            ),
            text=noise_text,
            hoverinfo='text',
            name='Noise'
        ))
    
    # Add each cluster with a unique color
    for i, cluster_id in enumerate(unique_clusters):
        cluster_idx = np.where(np.array(cluster_labels) == cluster_id)[0]
        
        if len(cluster_idx) == 0:
            continue
            
        cluster_x = [reduced_vectors[i][0] for i in cluster_idx]
        cluster_y = [reduced_vectors[i][1] for i in cluster_idx]
        cluster_text = [hover_texts[i] for i in cluster_idx]
        
        # Get color for this cluster
        if isinstance(colorscale, list):
            color = colorscale[i % len(colorscale)]
        else:
            # Generate color from a continuous scale
            color = px.colors.sample_colorscale(
                colorscale, 
                [i / max(1, len(unique_clusters) - 1)]
            )[0]
        
        # Get keywords for cluster
        keywords = cluster_keywords.get(cluster_id, [])
        keyword_text = ', '.join(keywords)
        cluster_name = f"Cluster {cluster_id} ({cluster_counts[cluster_id]} docs): {keyword_text}"
        
        # Add the cluster
        fig.add_trace(go.Scattergl(
            x=cluster_x,
            y=cluster_y,
            mode='markers',
            marker=dict(
                size=6,
                color=color,
                opacity=0.7,
                line=dict(width=0.5, color='white')
            ),
            text=cluster_text,
            hoverinfo='text',
            name=cluster_name,
            legendgroup=f"cluster_{cluster_id}"
        ))
        
        # For larger clusters, add a label at the centroid
        if cluster_counts[cluster_id] > 150:
            # Calculate centroid
            centroid_x = sum(cluster_x) / len(cluster_x)
            centroid_y = sum(cluster_y) / len(cluster_y)
            
            # Add cluster label
            label_text = f"Cluster {cluster_id}: {keyword_text}"
            
            fig.add_trace(go.Scatter(
                x=[centroid_x],
                y=[centroid_y],
                mode='markers+text',
                marker=dict(
                    size=12,
                    color=color,
                    symbol='star',
                    line=dict(width=1.5, color='white')
                ),
                text=label_text,
                textposition="top center",
                textfont=dict(size=10, color='black'),
                hoverinfo='text',
                legendgroup=f"cluster_{cluster_id}",
                showlegend=False
            ))
    
    # Update layout for better appearance
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
            x=0.99,
            bgcolor='rgba(255, 255, 255, 0.7)'
        ),
        hovermode='closest',
        margin=dict(l=20, r=20, b=20, t=60),
        plot_bgcolor='white',
        width=1400,
        height=1000,
        autosize=True
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
    
    # Add buttons to show/hide clusters by size
    buttons = [
        dict(
            label="Show All",
            method="update",
            args=[{"visible": [True] * len(fig.data)}]
        ),
        dict(
            label="Hide Noise",
            method="update",
            args=[{"visible": [not trace.name == "Noise" for trace in fig.data]}]
        ),
        dict(
            label="Large Clusters Only (>100)",
            method="update",
            args=[{"visible": [
                (trace.name is not None and "Cluster" in trace.name and 
                 "(" in trace.name and 
                 int(trace.name.split("(")[1].split()[0]) > 100) or 
                (hasattr(trace, 'legendgroup') and 
                 trace.legendgroup is not None and 
                 trace.legendgroup.startswith("cluster_") and 
                 cluster_counts[int(trace.legendgroup.split("_")[1])] > 100)
                for trace in fig.data
            ]}]
        )
    ]
    
    # Add dropdown menu
    fig.update_layout(
        updatemenus=[
            dict(
                buttons=buttons,
                direction="down",
                pad={"r": 10, "t": 10},
                showactive=True,
                x=0.05,
                xanchor="left",
                y=1.1,
                yanchor="top"
            )
        ]
    )
    
    # Add annotation explaining the visualization
    fig.add_annotation(
        text="Interactive Star Wars Knowledge Clusters<br>Hover for details, click legend items to show/hide",
        align="left",
        showarrow=False,
        xref="paper",
        yref="paper",
        x=0.5,
        y=-0.05,
        bordercolor="black",
        borderwidth=1,
        bgcolor="white",
        opacity=0.8
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
    parser.add_argument('--title', type=str, default="Star Wars Knowledge Map",
                        help='Title for the visualization')
    args = parser.parse_args()
    
    console.print("[bold magenta]Interactive Cluster Visualization[/bold magenta]")
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Load cluster data
    cluster_data = load_cluster_data(args.cluster_file)
    
    # Extract data
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
    
    # Save visualization
    output_file = os.path.join(args.output_dir, 'knowledge_map.html')
    fig.write_html(
        output_file, 
        include_plotlyjs='cdn',
        config={
            'scrollZoom': True,
            'displayModeBar': True,
            'modeBarButtonsToAdd': ['select2d', 'lasso2d']
        }
    )
    console.print(f"[bold green]Interactive visualization saved to {output_file}[/bold green]")
    
    # Print largest clusters
    console.print("\n[bold]Top 10 Largest Clusters:[/bold]")
    largest_clusters = get_largest_clusters(cluster_labels, n=10)
    for cluster_id, count in largest_clusters:
        keywords = ', '.join(cluster_keywords.get(cluster_id, ['Unknown']))
        console.print(f"Cluster {cluster_id}: {count} vectors - Keywords: {keywords}")

if __name__ == "__main__":
    main() 