#!/usr/bin/env python3
"""
Analyze generated HDBSCAN clusters and extract key information.
This script examines the clusters exported by hdbscan_clusters.py and
identifies key topics, representative documents, and potential applications.
"""

import os
import sys
import json
import argparse
import re
from collections import Counter, defaultdict
from typing import Dict, List, Tuple, Set
import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, TextColumn, BarColumn
from rich.markdown import Markdown

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

def extract_key_terms(text: str, min_length: int = 4, max_terms: int = 50) -> List[str]:
    """
    Extract key terms from text.
    
    Args:
        text: Text to analyze
        min_length: Minimum term length
        max_terms: Maximum number of terms to return
        
    Returns:
        List of key terms
    """
    # Remove punctuation and lowercase
    text = re.sub(r'[^\w\s]', ' ', text.lower())
    
    # Split into words
    words = text.split()
    
    # Filter words
    filtered_words = [word for word in words if len(word) >= min_length and not word.isdigit()]
    
    # Count occurrences
    word_counts = Counter(filtered_words)
    
    # Get most common terms
    return [word for word, _ in word_counts.most_common(max_terms)]

def analyze_cluster_topics(cluster: Dict) -> Dict:
    """
    Analyze topics within a cluster.
    
    Args:
        cluster: Cluster data dictionary
        
    Returns:
        Dictionary with topic analysis
    """
    vectors = cluster.get('vectors', [])
    
    # Extract all text content
    all_text = ""
    titles = []
    
    for vector in vectors:
        metadata = vector.get('metadata', {})
        
        # Extract title
        title = vector.get('title', '')
        if title:
            titles.append(title)
        
        # Extract content
        content = ""
        if 'content' in metadata:
            content = metadata['content']
        elif 'metadata' in metadata and isinstance(metadata['metadata'], dict) and 'content' in metadata['metadata']:
            content = metadata['metadata']['content']
        
        if content:
            all_text += " " + content
    
    # Extract key terms
    key_terms = extract_key_terms(all_text)
    
    # Analyze titles for common patterns
    title_terms = Counter()
    for title in titles:
        if title:
            # Extract terms from title
            title = re.sub(r'[^\w\s]', ' ', title.lower())
            terms = [term for term in title.split() if len(term) >= 3]
            title_terms.update(terms)
    
    # Get most common title terms
    common_title_terms = [term for term, count in title_terms.most_common(10) if count > 1]
    
    return {
        'key_terms': key_terms,
        'title_terms': common_title_terms,
        'total_vectors': len(vectors),
        'titles': titles[:10]  # Just include first 10 titles
    }

def find_representative_documents(cluster: Dict, top_n: int = 5) -> List[Dict]:
    """
    Find the most representative documents in a cluster.
    
    Args:
        cluster: Cluster data dictionary
        top_n: Number of documents to return
        
    Returns:
        List of representative documents
    """
    vectors = cluster.get('vectors', [])
    
    if not vectors:
        return []
    
    # For now, use the first few vectors as representative
    # In a more advanced version, we could find vectors closest to cluster centroid
    representative_docs = []
    
    for vector in vectors[:top_n]:
        metadata = vector.get('metadata', {})
        title = vector.get('title', 'Untitled')
        
        # Extract content
        content = ""
        if 'content' in metadata:
            content = metadata['content']
        elif 'metadata' in metadata and isinstance(metadata['metadata'], dict) and 'content' in metadata['metadata']:
            content = metadata['metadata']['content']
        
        # Create representative doc entry
        doc = {
            'id': vector.get('id', ''),
            'title': title,
            'content_preview': content[:200] + '...' if content else 'No content'
        }
        
        representative_docs.append(doc)
    
    return representative_docs

def suggest_cluster_application(cluster_id: int, cluster_analysis: Dict) -> str:
    """
    Suggest potential applications for a cluster based on its content.
    
    Args:
        cluster_id: Cluster ID
        cluster_analysis: Cluster analysis data
        
    Returns:
        Suggested application
    """
    key_terms = cluster_analysis.get('key_terms', [])
    total_vectors = cluster_analysis.get('total_vectors', 0)
    titles = cluster_analysis.get('titles', [])
    
    character_terms = {'skywalker', 'solo', 'vader', 'kenobi', 'yoda', 'sidious', 'palpatine', 'anakin'}
    location_terms = {'tatooine', 'coruscant', 'naboo', 'kashyyyk', 'geonosis', 'hoth', 'endor'}
    vehicle_terms = {'falcon', 'destroyer', 'cruiser', 'fighter', 'speeder', 'shuttle', 'freighter'}
    event_terms = {'battle', 'attack', 'invasion', 'duel', 'mission', 'siege', 'assault'}
    
    # Check for term overlap
    key_terms_set = set(key_terms)
    character_overlap = key_terms_set.intersection(character_terms)
    location_overlap = key_terms_set.intersection(location_terms)
    vehicle_overlap = key_terms_set.intersection(vehicle_terms)
    event_overlap = key_terms_set.intersection(event_terms)
    
    # Check titles for patterns
    title_text = ' '.join(titles).lower()
    
    # Generate suggestion based on cluster content
    if len(character_overlap) >= 2 or 'character' in title_text:
        return f"Character Information Retrieval - This cluster contains detailed character information and could be used for answering character-specific questions."
    elif len(location_overlap) >= 2 or 'planet' in title_text or 'location' in title_text:
        return f"Location/Planet Information - This cluster focuses on Star Wars locations and could enhance spatial context in responses."
    elif len(vehicle_overlap) >= 2 or 'ship' in title_text or 'vehicle' in title_text:
        return f"Vehicle/Ship Database - This cluster contains information about Star Wars vehicles and ships, useful for technical questions."
    elif len(event_overlap) >= 2 or 'battle' in title_text or 'war' in title_text:
        return f"Historical Events - This cluster focuses on key events in Star Wars history, useful for timeline and historical questions."
    elif total_vectors > 200:
        return f"General Knowledge Domain - Large cluster with diverse content, could serve as a general knowledge source for broad queries."
    elif total_vectors < 50:
        return f"Specialized Knowledge - Small, focused cluster that may contain niche information for specific detailed queries."
    else:
        return f"Mixed Content Cluster - Contains various content types that could supplement other knowledge domains."

def print_cluster_report(cluster_id: int, cluster: Dict, analysis: Dict) -> None:
    """
    Print a report for a single cluster.
    
    Args:
        cluster_id: Cluster ID
        cluster: Original cluster data
        analysis: Cluster analysis data
    """
    # Determine cluster name/title
    if cluster_id == -1:
        cluster_title = "NOISE POINTS"
    else:
        cluster_title = f"CLUSTER {cluster_id}"
    
    # Get representative documents
    representative_docs = find_representative_documents(cluster)
    
    # Generate application suggestion
    application = suggest_cluster_application(cluster_id, analysis)
    
    # Create panel with cluster information
    cluster_panel = Panel(
        f"[bold cyan]Size:[/bold cyan] {analysis.get('total_vectors', 0)} vectors\n\n"
        f"[bold yellow]Key Terms:[/bold yellow] {', '.join(analysis.get('key_terms', [])[:15])}\n\n"
        f"[bold green]Title Patterns:[/bold green] {', '.join(analysis.get('title_terms', []))}\n\n"
        f"[bold magenta]Application:[/bold magenta] {application}",
        title=f"[bold white on blue]{cluster_title}[/bold white on blue]",
        border_style="blue"
    )
    
    console.print(cluster_panel)
    
    # Print representative documents
    if representative_docs:
        console.print("[bold]Representative Documents:[/bold]")
        
        for i, doc in enumerate(representative_docs, 1):
            doc_panel = Panel(
                f"[bold cyan]ID:[/bold cyan] {doc.get('id', '')}\n"
                f"[bold yellow]Title:[/bold yellow] {doc.get('title', 'Untitled')}\n\n"
                f"{doc.get('content_preview', '')}",
                title=f"[bold white on green]Document {i}[/bold white on green]",
                border_style="green"
            )
            
            console.print(doc_panel)
    
    console.print("\n" + "-" * 80 + "\n")

def print_overall_report(clusters: Dict, analyses: Dict) -> None:
    """
    Print an overall report of all clusters.
    
    Args:
        clusters: Dictionary of all clusters
        analyses: Dictionary of all cluster analyses
    """
    # Sort clusters by size (excluding noise)
    sorted_clusters = sorted(
        [(cid, clusters[cid]) for cid in clusters if cid != -1],
        key=lambda x: x[1].get('size', 0),
        reverse=True
    )
    
    # Create table of clusters
    table = Table(title="Cluster Overview")
    table.add_column("Cluster ID", justify="right", style="cyan")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Top Terms", style="yellow")
    table.add_column("Suggested Application", style="magenta")
    
    # Add clusters to table
    for cluster_id, cluster in sorted_clusters:
        analysis = analyses.get(cluster_id, {})
        size = cluster.get('size', 0)
        top_terms = ", ".join(analysis.get('key_terms', [])[:5])
        application = suggest_cluster_application(cluster_id, analysis)
        
        table.add_row(
            str(cluster_id),
            str(size),
            top_terms,
            application.split(" - ")[0]  # Just use the first part of the application
        )
    
    # Add noise points if present
    if -1 in clusters:
        noise = clusters[-1]
        noise_analysis = analyses.get(-1, {})
        noise_size = noise.get('size', 0)
        noise_terms = ", ".join(noise_analysis.get('key_terms', [])[:5])
        
        table.add_row(
            "Noise",
            str(noise_size),
            noise_terms,
            "Outliers and unique content"
        )
    
    console.print(table)

def generate_readme(clusters_dir: str, clusters: Dict, analyses: Dict) -> None:
    """
    Generate a README.md file with cluster information.
    
    Args:
        clusters_dir: Directory containing cluster data
        clusters: Dictionary of all clusters
        analyses: Dictionary of all cluster analyses
    """
    # Sort clusters by size (excluding noise)
    sorted_clusters = sorted(
        [(cid, clusters[cid]) for cid in clusters if cid != -1],
        key=lambda x: x[1].get('size', 0),
        reverse=True
    )
    
    # Create markdown content
    markdown = "# Holocron Knowledge Cluster Analysis\n\n"
    markdown += "## Overview\n\n"
    markdown += f"This analysis contains {len(clusters)} clusters derived from BERT/E5 vector embeddings using HDBSCAN clustering.\n\n"
    
    # Add top clusters section
    markdown += "## Top Clusters by Size\n\n"
    markdown += "| Cluster ID | Size | Top Terms | Suggested Application |\n"
    markdown += "|-----------|------|-----------|------------------------|\n"
    
    for cluster_id, cluster in sorted_clusters[:10]:  # Just top 10
        analysis = analyses.get(cluster_id, {})
        size = cluster.get('size', 0)
        top_terms = ", ".join(analysis.get('key_terms', [])[:5])
        application = suggest_cluster_application(cluster_id, analysis)
        
        markdown += f"| {cluster_id} | {size} | {top_terms} | {application.split(' - ')[0]} |\n"
    
    # Add noise points if present
    if -1 in clusters:
        markdown += "\n## Noise Points\n\n"
        noise = clusters[-1]
        noise_analysis = analyses.get(-1, {})
        noise_size = noise.get('size', 0)
        noise_terms = ", ".join(noise_analysis.get('key_terms', [])[:8])
        
        markdown += f"**Size:** {noise_size} vectors\n\n"
        markdown += f"**Top Terms:** {noise_terms}\n\n"
        markdown += "These points represent outliers that didn't fit well into any cluster. They may contain unique or specialized knowledge that could be valuable for specific queries.\n\n"
    
    # Add cluster details for top clusters
    markdown += "\n## Detailed Cluster Analysis\n\n"
    
    for cluster_id, cluster in sorted_clusters[:5]:  # Just top 5 for detail
        analysis = analyses.get(cluster_id, {})
        size = cluster.get('size', 0)
        application = suggest_cluster_application(cluster_id, analysis)
        
        markdown += f"### Cluster {cluster_id}\n\n"
        markdown += f"**Size:** {size} vectors\n\n"
        markdown += f"**Key Terms:** {', '.join(analysis.get('key_terms', [])[:15])}\n\n"
        markdown += f"**Title Patterns:** {', '.join(analysis.get('title_terms', []))}\n\n"
        markdown += f"**Application:** {application}\n\n"
        
        # Add representative documents
        markdown += "#### Representative Documents\n\n"
        representative_docs = find_representative_documents(cluster, top_n=3)
        
        for i, doc in enumerate(representative_docs, 1):
            markdown += f"**Document {i}:** {doc.get('title', 'Untitled')}\n\n"
            markdown += f"```\n{doc.get('content_preview', '')}\n```\n\n"
    
    # Write markdown to file
    readme_path = os.path.join(clusters_dir, "README.md")
    with open(readme_path, 'w') as f:
        f.write(markdown)
    
    console.print(f"[bold green]Generated README at {readme_path}[/bold green]")

def main():
    """Main function to analyze cluster data."""
    parser = argparse.ArgumentParser(description='Analyze HDBSCAN cluster data')
    parser.add_argument('--clusters-dir', type=str, default="clusters",
                      help='Directory containing cluster JSON files')
    parser.add_argument('--cluster-id', type=int,
                      help='Specific cluster ID to analyze (optional)')
    parser.add_argument('--generate-readme', action='store_true',
                      help='Generate a README.md file with cluster analysis')
    args = parser.parse_args()
    
    console.print("[bold magenta]HDBSCAN Cluster Analysis[/bold magenta]")
    
    # Verify clusters directory exists
    if not os.path.exists(args.clusters_dir):
        console.print(f"[bold red]Error:[/bold red] Clusters directory {args.clusters_dir} not found.")
        return
    
    # Load clusters
    clusters = load_clusters(args.clusters_dir)
    
    if not clusters:
        console.print("[bold red]Error:[/bold red] No clusters found.")
        return
    
    # Analyze clusters
    analyses = {}
    
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Analyzing clusters...", total=len(clusters))
        
        for cluster_id, cluster in clusters.items():
            analyses[cluster_id] = analyze_cluster_topics(cluster)
            progress.update(task, advance=1)
    
    # Print reports
    if args.cluster_id is not None and args.cluster_id in clusters:
        # Print report for specific cluster
        print_cluster_report(args.cluster_id, clusters[args.cluster_id], analyses[args.cluster_id])
    else:
        # Print overall report
        print_overall_report(clusters, analyses)
        
        # Print individual reports for top clusters
        sorted_clusters = sorted(
            [(cid, clusters[cid]) for cid in clusters if cid != -1],
            key=lambda x: x[1].get('size', 0),
            reverse=True
        )
        
        # Print details for top 5 clusters
        console.print("[bold]Detailed Analysis of Top Clusters:[/bold]\n")
        
        for cluster_id, cluster in sorted_clusters[:5]:
            print_cluster_report(cluster_id, cluster, analyses[cluster_id])
    
    # Generate README if requested
    if args.generate_readme:
        generate_readme(args.clusters_dir, clusters, analyses)
    
    console.print("[bold green]Cluster Analysis Complete[/bold green]")

if __name__ == "__main__":
    main() 