#!/usr/bin/env python3
"""
Compare search results between OpenAI and BERT embeddings.
This script allows querying both indexes and comparing the results side by side.
"""

import os
import sys
import logging
import json
import time
import argparse
from typing import List, Dict, Any, Tuple
import numpy as np
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import our embedding component
from holocron.knowledge.embeddings import OpenAIEmbeddings

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

class VectorSearchResult:
    """Structured container for vector search results."""
    def __init__(
        self,
        id: str,
        content: str,
        metadata: Dict[str, Any],
        similarity: float
    ):
        self.id = id
        self.content = content
        self.metadata = metadata
        self.similarity = similarity

def search_openai_index(query: str, index, openai_embeddings, limit: int = 10):
    """
    Search the OpenAI embeddings index for a query.
    
    Args:
        query: The search query
        index: Pinecone index
        openai_embeddings: OpenAIEmbeddings instance
        limit: Maximum number of results to return
        
    Returns:
        List of VectorSearchResult objects
    """
    console.print(f"\n[bold cyan]Searching OpenAI index for:[/bold cyan] {query}")
    
    # Generate query embedding
    query_embedding = openai_embeddings.embed_query(query)
    
    try:
        # Search the index
        results = index.query(
            vector=query_embedding,
            top_k=limit,
            include_metadata=True
        )
        
        # Convert to VectorSearchResult objects
        search_results = []
        
        for match in results.matches:
            content = match.metadata.get('content', '') if hasattr(match, 'metadata') else ''
            
            search_results.append(VectorSearchResult(
                id=match.id,
                content=content,
                metadata=match.metadata if hasattr(match, 'metadata') else {},
                similarity=match.score
            ))
        
        console.print(f"[green]Found {len(search_results)} results[/green]")
        return search_results
    
    except Exception as e:
        logger.error(f"Error searching OpenAI index: {str(e)}")
        return []

def search_bert_index(query: str, index, bert_embeddings, limit: int = 10):
    """
    Search the BERT embeddings index for a query.
    
    Args:
        query: The search query
        index: Pinecone index
        bert_embeddings: BERTEmbeddings instance
        limit: Maximum number of results to return
        
    Returns:
        List of VectorSearchResult objects
    """
    console.print(f"\n[bold blue]Searching BERT index for:[/bold blue] {query}")
    
    # Generate query embedding
    query_embedding = bert_embeddings.embed_query(query)
    
    try:
        # Search the index
        results = index.query(
            vector=query_embedding,
            top_k=limit,
            include_metadata=True
        )
        
        # Convert to VectorSearchResult objects
        search_results = []
        
        for match in results.matches:
            content = match.metadata.get('content', '') if hasattr(match, 'metadata') else ''
            
            search_results.append(VectorSearchResult(
                id=match.id,
                content=content,
                metadata=match.metadata if hasattr(match, 'metadata') else {},
                similarity=match.score
            ))
        
        console.print(f"[green]Found {len(search_results)} results[/green]")
        return search_results
    
    except Exception as e:
        logger.error(f"Error searching BERT index: {str(e)}")
        return []

def display_comparison(openai_results, bert_results, top_k=5):
    """
    Display a side-by-side comparison of OpenAI and BERT results.
    
    Args:
        openai_results: Results from OpenAI embeddings
        bert_results: Results from BERT embeddings
        top_k: Number of top results to display
    """
    # Create comparison table
    table = Table(title="Embedding Comparison (Top Results)")
    table.add_column("Rank", style="cyan")
    table.add_column("OpenAI ID", style="green")
    table.add_column("OpenAI Score", style="green")
    table.add_column("BERT ID", style="blue")
    table.add_column("BERT Score", style="blue")
    
    for i in range(min(top_k, len(openai_results), len(bert_results))):
        openai_result = openai_results[i]
        bert_result = bert_results[i]
        
        table.add_row(
            str(i+1),
            openai_result.id,
            f"{openai_result.similarity:.4f}",
            bert_result.id,
            f"{bert_result.similarity:.4f}"
        )
    
    console.print(table)
    
    # Common results analysis
    openai_ids = [r.id for r in openai_results[:top_k]]
    bert_ids = [r.id for r in bert_results[:top_k]]
    common_ids = set(openai_ids).intersection(set(bert_ids))
    
    console.print(f"\n[bold]Common results in top {top_k}:[/bold] {len(common_ids)}")
    if common_ids:
        console.print(f"IDs: {', '.join(common_ids)}")
    
    # Display detailed content for top results
    display_result_detail("OpenAI", openai_results[0], "cyan")
    display_result_detail("BERT", bert_results[0], "blue")

def display_result_detail(source, result, color="white"):
    """
    Display detailed information about a search result.
    
    Args:
        source: The source of the result (OpenAI/BERT)
        result: The VectorSearchResult to display
        color: Color for the panel border
    """
    # Extract metadata fields we want to display
    metadata = result.metadata
    if metadata:
        article_title = metadata.get('title', 'Unknown')
        article_url = metadata.get('url', 'No URL')
        source_type = metadata.get('source_type', 'Unknown')
    else:
        article_title = 'Unknown'
        article_url = 'No URL'
        source_type = 'Unknown'
    
    # Truncate content if too long
    content = result.content
    if len(content) > 500:
        content = content[:500] + "..."
    
    # Create panel with details
    console.print(f"\n[bold {color}]Top {source} Result:[/bold {color}]")
    console.print(Panel(
        f"[bold]ID:[/bold] {result.id}\n"
        f"[bold]Score:[/bold] {result.similarity:.4f}\n"
        f"[bold]Title:[/bold] {article_title}\n"
        f"[bold]Source:[/bold] {source_type}\n"
        f"[bold]URL:[/bold] {article_url}\n\n"
        f"{content}",
        title=f"{source} Result",
        border_style=color
    ))

def analyze_semantic_relationships(openai_results, bert_results, model_name):
    """
    Analyze and display semantic relationship differences between models.
    
    Args:
        openai_results: Results from OpenAI embeddings
        bert_results: Results from BERT embeddings
        model_name: Name of the BERT model for reference
    """
    # Extract document titles for analysis
    openai_titles = []
    for r in openai_results[:5]:
        if r.metadata and 'title' in r.metadata:
            openai_titles.append(r.metadata['title'])
        else:
            # Extract first line as pseudo-title
            content_lines = r.content.split('\n')
            first_line = content_lines[0] if content_lines else "Unknown"
            if len(first_line) > 50:
                first_line = first_line[:50] + "..."
            openai_titles.append(first_line)
    
    bert_titles = []
    for r in bert_results[:5]:
        if r.metadata and 'title' in r.metadata:
            bert_titles.append(r.metadata['title'])
        else:
            # Extract first line as pseudo-title
            content_lines = r.content.split('\n')
            first_line = content_lines[0] if content_lines else "Unknown"
            if len(first_line) > 50:
                first_line = first_line[:50] + "..."
            bert_titles.append(first_line)
    
    # Display analysis
    console.print("\n[bold magenta]Semantic Relationship Analysis[/bold magenta]")
    
    console.print(f"\n[bold cyan]OpenAI embedding model:[/bold cyan] text-embedding-ada-002")
    console.print("[cyan]Top result topics:[/cyan]")
    for i, title in enumerate(openai_titles, 1):
        console.print(f"  {i}. {title}")
    
    console.print(f"\n[bold blue]BERT embedding model:[/bold blue] {model_name}")
    console.print("[blue]Top result topics:[/blue]")
    for i, title in enumerate(bert_titles, 1):
        console.print(f"  {i}. {title}")
    
    # Provide model-specific insights
    console.print("\n[bold]Potential Model Differences:[/bold]")
    
    if "MiniLM" in model_name:
        console.print("- MiniLM models are distilled from larger BERT models, focusing on essential semantic relationships")
        console.print("- They often capture core concepts well but might miss nuanced relationships")
    elif "MPNet" in model_name:
        console.print("- MPNet models combine BERT and XLNet architectures for better understanding of dependencies")
        console.print("- They perform well with relative positioning and relationship extraction")
    elif "all-" in model_name:
        console.print("- 'all-*' models are trained on a broader range of data sources than specialized models")
        console.print("- They typically provide balanced performance across different domains")
    
    # Common vs. unique observations
    common_titles = set([t.lower() for t in openai_titles]).intersection(set([t.lower() for t in bert_titles]))
    console.print(f"\n[green]Common topics between models:[/green] {len(common_titles)}")

def main():
    """Main function to compare embedding search results."""
    parser = argparse.ArgumentParser(description='Compare OpenAI and BERT embeddings search')
    parser.add_argument('--query', type=str, 
                        help='Query to search for')
    parser.add_argument('--interactive', action='store_true',
                        help='Run in interactive mode for multiple queries')
    parser.add_argument('--bert-model', type=str, default="intfloat/e5-small-v2",
                        help='BERT model name')
    parser.add_argument('--limit', type=int, default=10,
                        help='Maximum number of results to return')
    parser.add_argument('--bert-index-name', type=str, default="holocron-sbert",
                        help='Name of the BERT embeddings index')
    args = parser.parse_args()
    
    console.print("[bold magenta]Comparing OpenAI and BERT Embeddings Search[/bold magenta]")
    
    # Get API keys
    pinecone_api_key = os.getenv("PINECONE_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")
    
    if not pinecone_api_key or not openai_api_key:
        console.print("[bold red]Error:[/bold red] Missing API keys. Check your .env file.")
        return
    
    # Initialize clients
    openai_client = OpenAI(api_key=openai_api_key)
    pc = Pinecone(api_key=pinecone_api_key)
    
    # Initialize embedding generators
    openai_embeddings = OpenAIEmbeddings()
    bert_embeddings = BERTEmbeddings(model_name=args.bert_model)
    
    # Get indexes
    openai_index_name = "holocron-knowledge"
    bert_index_name = args.bert_index_name
    
    openai_index = None
    bert_index = None
    
    # List available indexes
    indexes = pc.list_indexes()
    index_names = [index_info.name for index_info in indexes]
    
    console.print(f"Available indexes: {', '.join(index_names)}")
    
    # Connect to OpenAI index
    if openai_index_name in index_names:
        openai_index = pc.Index(name=openai_index_name)
        console.print(f"[green]Connected to OpenAI index: {openai_index_name}[/green]")
    else:
        console.print(f"[bold red]Error:[/bold red] OpenAI index '{openai_index_name}' not found.")
        return
    
    # Connect to BERT index
    if bert_index_name in index_names:
        bert_index = pc.Index(name=bert_index_name)
        console.print(f"[green]Connected to BERT index: {bert_index_name}[/green]")
    else:
        console.print(f"[bold red]Error:[/bold red] BERT index '{bert_index_name}' not found.")
        console.print(f"[yellow]Please run create_bert_index.py first to create the BERT index.[/yellow]")
        return
    
    # Run search
    if args.interactive:
        # Interactive mode
        console.print("\n[bold]Interactive Mode[/bold] - Type 'exit' to quit")
        
        while True:
            query = console.input("\n[bold]Enter search query:[/bold] ")
            
            if query.lower() in ['exit', 'quit', 'q']:
                break
            
            # Search both indexes
            openai_results = search_openai_index(query, openai_index, openai_embeddings, args.limit)
            bert_results = search_bert_index(query, bert_index, bert_embeddings, args.limit)
            
            # Display comparison
            if openai_results and bert_results:
                display_comparison(openai_results, bert_results)
                analyze_semantic_relationships(openai_results, bert_results, args.bert_model)
            else:
                console.print("[yellow]No results found in one or both indexes.[/yellow]")
    
    elif args.query:
        # Single query mode
        query = args.query
        
        # Search both indexes
        openai_results = search_openai_index(query, openai_index, openai_embeddings, args.limit)
        bert_results = search_bert_index(query, bert_index, bert_embeddings, args.limit)
        
        # Display comparison
        if openai_results and bert_results:
            display_comparison(openai_results, bert_results)
            analyze_semantic_relationships(openai_results, bert_results, args.bert_model)
        else:
            console.print("[yellow]No results found in one or both indexes.[/yellow]")
    
    else:
        # No query provided
        console.print("[yellow]No query provided. Use --query or --interactive.[/yellow]")
        parser.print_help()
    
    console.print("\n[bold green]Search Comparison Complete[/bold green]")

if __name__ == "__main__":
    main() 