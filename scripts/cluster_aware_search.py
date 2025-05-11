#!/usr/bin/env python3
"""
Cluster-aware search leveraging HDBSCAN clustering for the Holocron Knowledge System.
This script implements a semantic search enhanced by awareness of knowledge domains
discovered through clustering analysis.
"""

import os
import sys
import json
import argparse
import re
import logging
from typing import Dict, List, Any, Tuple, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, TextColumn, BarColumn
from rich.markdown import Markdown
import openai

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Knowledge domain definitions based on clustering analysis
KNOWLEDGE_DOMAINS = {
    "character_info": {
        "clusters": [34, 22, 37, 45, 58, 74],
        "description": "Character Information", 
        "keywords": ["skywalker", "solo", "vader", "character", "jedi", "sith", "rey", "who is", "biography"]
    },
    "location_info": {
        "clusters": [67, 29, 30],
        "description": "Locations & Establishments",
        "keywords": ["planet", "location", "cantina", "city", "system", "where is", "galaxy", "sector"]
    },
    "droid_tech": {
        "clusters": [39, 72, 60],
        "description": "Droids & Technology",
        "keywords": ["droid", "technology", "model", "series", "astromech", "protocol", "machine"]
    },
    "media_references": {
        "clusters": [15, 12, 8],
        "description": "Media & Entertainment",
        "keywords": ["lego", "show", "episode", "game", "novel", "book", "movie", "film"]
    },
    "events_history": {
        "clusters": [26, 31, 36, 62],
        "description": "Historical Events",
        "keywords": ["battle", "war", "event", "rebellion", "empire", "attack", "mission", "when did"]
    }
}

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
            # For E5 models, prefix with "query: " for asymmetric search
            if "e5" in self.model_name.lower():
                text = f"query: {text}"
                
            embedding = self.model.encode(text)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Error generating BERT embedding: {e}")
            # Return zero vector as fallback
            return [0.0] * self.embedding_dim

class OpenAIEmbeddings:
    """Generates embeddings using OpenAI API."""
    
    def __init__(self, model_name="text-embedding-ada-002"):
        """
        Initialize the OpenAI embeddings generator.
        
        Args:
            model_name: The name of the OpenAI embedding model to use.
        """
        self.model_name = model_name
        self.embedding_dim = 1536  # Hardcoded for text-embedding-ada-002
        
        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API key not found. Please set OPENAI_API_KEY in your environment variables.")
        
        self.client = openai.OpenAI(api_key=api_key)
        console.print(f"Initialized OpenAI embeddings with model [cyan]{model_name}[/cyan]")
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate embeddings for a single query text using OpenAI API.
        
        Args:
            text: The text to embed
            
        Returns:
            List of embedding values
        """
        try:
            response = self.client.embeddings.create(
                model=self.model_name,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating OpenAI embedding: {e}")
            # Return zero vector as fallback
            return [0.0] * self.embedding_dim

class SearchResult:
    """Container for a single search result."""
    
    def __init__(
        self,
        id: str,
        content: str,
        title: Optional[str] = None,
        similarity: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
        cluster_id: Optional[int] = None,
        domain: Optional[str] = None
    ):
        self.id = id
        self.content = content
        self.title = title
        self.similarity = similarity
        self.metadata = metadata or {}
        self.cluster_id = cluster_id
        self.domain = domain
    
    def __repr__(self) -> str:
        return f"SearchResult(id={self.id}, title={self.title}, similarity={self.similarity:.4f}, cluster={self.cluster_id}, domain={self.domain})"

class ClusterAwareSearch:
    """Implements cluster-aware semantic search using Pinecone."""
    
    def __init__(
        self,
        bert_index_name: str = "holocron-sbert-e5",
        ada_index_name: str = "holocron-knowledge",
        bert_model: str = "intfloat/e5-small-v2",
        use_openai: bool = False,
        cluster_map_file: Optional[str] = None
    ):
        """
        Initialize cluster-aware search.
        
        Args:
            bert_index_name: Name of the Pinecone index for BERT embeddings
            ada_index_name: Name of the Pinecone index for OpenAI embeddings
            bert_model: BERT model name for generating embeddings
            use_openai: Whether to use OpenAI for embeddings
            cluster_map_file: Path to cluster map file
        """
        # Initialize Pinecone client
        pinecone_api_key = os.getenv("PINECONE_API_KEY")
        if not pinecone_api_key:
            raise ValueError("Pinecone API key not found. Please set PINECONE_API_KEY in your environment variables.")
        
        self.pc = Pinecone(api_key=pinecone_api_key)
        console.print("Connecting to Pinecone indexes...")
        
        # Get BERT index
        self.bert_index = self.pc.Index(bert_index_name)
        console.print(f"Connected to BERT index: [green]{bert_index_name}[/green]")
        
        # Get OpenAI index if needed
        if use_openai:
            self.ada_index = self.pc.Index(ada_index_name)
            console.print(f"Connected to OpenAI index: [green]{ada_index_name}[/green]")
        else:
            self.ada_index = None
        
        # Initialize embedding generators
        if use_openai:
            self.openai_embeddings = OpenAIEmbeddings()
        else:
            self.openai_embeddings = None
            
        self.bert_embeddings = BERTEmbeddings(model_name=bert_model)
        
        # Load cluster map if provided
        self.cluster_map = self._load_cluster_map(cluster_map_file)
        self.domains = KNOWLEDGE_DOMAINS
    
    def _load_cluster_map(self, cluster_map_file: Optional[str]) -> Dict[str, int]:
        """
        Load mapping from vector ID to cluster ID.
        
        Args:
            cluster_map_file: Path to cluster map file
            
        Returns:
            Dictionary mapping vector ID to cluster ID
        """
        if not cluster_map_file or not os.path.exists(cluster_map_file):
            console.print("[yellow]Cluster map file not found. Running without explicit cluster mapping.[/yellow]")
            return {}
        
        try:
            with open(cluster_map_file, 'r') as f:
                cluster_map = json.load(f)
            
            console.print(f"[green]Loaded cluster map with {len(cluster_map)} entries[/green]")
            return cluster_map
        except Exception as e:
            console.print(f"[bold red]Error loading cluster map: {str(e)}[/bold red]")
            return {}
    
    def _extract_cluster_id(self, vector_id: str) -> Optional[int]:
        """
        Extract cluster ID for a vector ID.
        
        Args:
            vector_id: Vector ID to look up
            
        Returns:
            Cluster ID if found, None otherwise
        """
        return self.cluster_map.get(vector_id)
    
    def _determine_knowledge_domain(self, query: str, cluster_id: Optional[int] = None) -> Optional[str]:
        """
        Determine the knowledge domain for a query.
        
        Args:
            query: Search query
            cluster_id: Optional cluster ID
            
        Returns:
            Knowledge domain name if found, None otherwise
        """
        # First check if cluster_id maps to a domain
        if cluster_id is not None:
            for domain_name, domain_info in self.domains.items():
                if cluster_id in domain_info["clusters"]:
                    return domain_name
        
        # If no match by cluster, check keywords in query
        query_lower = query.lower()
        for domain_name, domain_info in self.domains.items():
            for keyword in domain_info["keywords"]:
                if keyword in query_lower:
                    return domain_name
        
        # No domain identified
        return None
    
    def _enhance_results(
        self, 
        results: List[SearchResult],
        query: str
    ) -> List[SearchResult]:
        """
        Enhance search results with cluster and domain information.
        
        Args:
            results: List of search results
            query: Original search query
            
        Returns:
            Enhanced list of search results
        """
        # Add cluster IDs and domains to results
        for result in results:
            # Determine cluster ID
            vector_id = result.id
            cluster_id = self._extract_cluster_id(vector_id)
            result.cluster_id = cluster_id
            
            # Determine domain
            result.domain = self._determine_knowledge_domain(query, cluster_id)
        
        # Rerank results based on domain relevance
        return self._rerank_by_domain_relevance(results, query)
    
    def _rerank_by_domain_relevance(
        self, 
        results: List[SearchResult],
        query: str
    ) -> List[SearchResult]:
        """
        Rerank results based on domain relevance to the query.
        
        Args:
            results: List of search results
            query: Original search query
            
        Returns:
            Reranked list of search results
        """
        # First, determine the likely domain for the query
        query_domain = self._determine_knowledge_domain(query)
        
        if not query_domain:
            # If no domain identified, return results as is
            return results
        
        # Define boost factors for ranking
        domain_boost = 0.2  # Boost for matching domain
        
        # Calculate adjusted scores
        for result in results:
            adjusted_score = result.similarity
            
            # Apply domain boost
            if result.domain == query_domain:
                adjusted_score += domain_boost
            
            # Update result with adjusted score
            result.adjusted_score = min(adjusted_score, 1.0)  # Cap at 1.0
        
        # Sort by adjusted score
        results.sort(key=lambda x: x.adjusted_score if hasattr(x, 'adjusted_score') else x.similarity, reverse=True)
        
        return results
    
    def search(
        self,
        query: str,
        use_bert: bool = True,
        use_openai: bool = False,
        top_k: int = 5,
        threshold: float = 0.2,
        cluster_filter: Optional[List[int]] = None,
        domain_filter: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Perform semantic search with cluster awareness.
        
        Args:
            query: Search query
            use_bert: Whether to use BERT embeddings
            use_openai: Whether to use OpenAI embeddings
            top_k: Number of results to return
            threshold: Similarity threshold
            cluster_filter: Optional filter for specific clusters
            domain_filter: Optional filter for specific domain
            
        Returns:
            List of search results
        """
        results = []
        
        # Determine domain filter based on query if none provided
        if not domain_filter:
            domain_filter = self._determine_knowledge_domain(query)
        
        # Convert domain filter to cluster filter if provided
        if domain_filter and not cluster_filter:
            domain_info = self.domains.get(domain_filter)
            if domain_info:
                cluster_filter = domain_info["clusters"]
        
        # Generate embeddings
        bert_embedding = None
        openai_embedding = None
        
        if use_bert:
            bert_embedding = self.bert_embeddings.embed_query(query)
            
        if use_openai and self.openai_embeddings:
            openai_embedding = self.openai_embeddings.embed_query(query)
        
        # Search with BERT embeddings
        if bert_embedding:
            bert_results = self._search_pinecone(
                self.bert_index,
                bert_embedding,
                top_k=top_k * 2,  # Get more results for filtering
                threshold=threshold
            )
            results.extend(bert_results)
        
        # Search with OpenAI embeddings
        if openai_embedding and self.ada_index:
            openai_results = self._search_pinecone(
                self.ada_index,
                openai_embedding,
                top_k=top_k * 2,  # Get more results for filtering
                threshold=threshold
            )
            results.extend(openai_results)
        
        # Enhance results with cluster and domain information
        results = self._enhance_results(results, query)
        
        # Apply filters
        if cluster_filter:
            results = [r for r in results if r.cluster_id in cluster_filter]
        
        # Return top results
        return results[:top_k]
    
    def _search_pinecone(
        self,
        index,
        embedding: List[float],
        top_k: int = 10,
        threshold: float = 0.2
    ) -> List[SearchResult]:
        """
        Search Pinecone index with an embedding.
        
        Args:
            index: Pinecone index
            embedding: Query embedding
            top_k: Number of results to return
            threshold: Similarity threshold
            
        Returns:
            List of search results
        """
        try:
            # Query index
            response = index.query(
                vector=embedding,
                top_k=top_k,
                include_metadata=True,
                include_values=False
            )
            
            # Convert to SearchResult objects
            results = []
            for match in response.matches:
                # Extract metadata
                metadata = match.metadata if hasattr(match, 'metadata') else {}
                
                # Extract title
                title = None
                if metadata and 'title' in metadata:
                    title = metadata['title']
                elif metadata and 'metadata' in metadata and isinstance(metadata['metadata'], dict) and 'title' in metadata['metadata']:
                    title = metadata['metadata']['title']
                
                # Extract content
                content = ""
                if metadata and 'content' in metadata:
                    content = metadata['content']
                elif metadata and 'metadata' in metadata and isinstance(metadata['metadata'], dict) and 'content' in metadata['metadata']:
                    content = metadata['metadata']['content']
                
                # Create result
                if match.score >= threshold:
                    result = SearchResult(
                        id=match.id,
                        content=content,
                        title=title,
                        similarity=match.score,
                        metadata=metadata
                    )
                    results.append(result)
            
            return results
        
        except Exception as e:
            logger.error(f"Error searching Pinecone: {e}")
            return []

def print_results(results: List[SearchResult], query: str):
    """
    Print search results in a formatted way.
    
    Args:
        results: List of search results
        query: Original search query
    """
    console.print(f"\n[bold magenta]Search Results for:[/bold magenta] {query}\n")
    
    # Create table of results
    table = Table(title="Search Results Overview")
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Title", style="green")
    table.add_column("Similarity", justify="right", style="yellow")
    table.add_column("Cluster", justify="right", style="magenta")
    table.add_column("Domain", style="blue")
    
    # Add results to table
    for i, result in enumerate(results, 1):
        cluster_str = str(result.cluster_id) if result.cluster_id is not None else "N/A"
        domain_str = result.domain if result.domain else "N/A"
        
        table.add_row(
            result.id,
            result.title if result.title else f"Document {i}",
            f"{result.similarity:.4f}",
            cluster_str,
            domain_str
        )
    
    console.print(table)
    
    # Print detailed result content
    for i, result in enumerate(results, 1):
        # Determine panel style based on domain
        if result.domain == "character_info":
            border_style = "green"
        elif result.domain == "location_info":
            border_style = "blue"
        elif result.domain == "droid_tech":
            border_style = "yellow"
        elif result.domain == "media_references":
            border_style = "magenta"
        elif result.domain == "events_history":
            border_style = "red"
        else:
            border_style = "white"
        
        # Create document panel
        doc_panel = Panel(
            result.content[:500] + "..." if len(result.content) > 500 else result.content,
            title=f"[bold white on {border_style}]Result {i}: {result.title}[/bold white on {border_style}]",
            border_style=border_style
        )
        
        console.print(doc_panel)
        console.print("\n")

def main():
    """Main function to run cluster-aware search."""
    parser = argparse.ArgumentParser(description='Cluster-aware semantic search')
    parser.add_argument('query', type=str, nargs='?', help='Search query')
    parser.add_argument('--interactive', '-i', action='store_true', help='Run in interactive mode')
    parser.add_argument('--openai', '-o', action='store_true', help='Use OpenAI embeddings')
    parser.add_argument('--bert', '-b', action='store_true', help='Use BERT embeddings (default)')
    parser.add_argument('--top-k', '-k', type=int, default=5, help='Number of results to return')
    parser.add_argument('--threshold', '-t', type=float, default=0.2, help='Similarity threshold')
    parser.add_argument('--domain', '-d', type=str, choices=KNOWLEDGE_DOMAINS.keys(), help='Filter by domain')
    parser.add_argument('--cluster-map', type=str, help='Path to cluster map file')
    args = parser.parse_args()
    
    console.print("[bold magenta]Cluster-Aware Semantic Search[/bold magenta]")
    
    # Determine search mode
    use_bert = True  # Default
    use_openai = args.openai
    
    if args.bert and args.openai:
        console.print("[bold]Using both BERT and OpenAI embeddings[/bold]")
    elif args.bert:
        console.print("[bold]Using BERT embeddings[/bold]")
    elif args.openai:
        console.print("[bold]Using OpenAI embeddings[/bold]")
        use_bert = False
    
    # Initialize search
    try:
        search = ClusterAwareSearch(
            use_openai=use_openai,
            cluster_map_file=args.cluster_map
        )
    except Exception as e:
        console.print(f"[bold red]Error initializing search: {str(e)}[/bold red]")
        return
    
    # Print domain information
    console.print("\n[bold cyan]Available Knowledge Domains:[/bold cyan]")
    for domain_name, domain_info in KNOWLEDGE_DOMAINS.items():
        console.print(f"[cyan]{domain_name}[/cyan]: {domain_info['description']} - Clusters {domain_info['clusters']}")
    
    # Interactive mode
    if args.interactive:
        console.print("\n[bold]Interactive Search Mode[/bold] (type 'exit' to quit)")
        
        while True:
            query = input("\nEnter search query: ")
            
            if query.lower() in ('exit', 'quit', 'q'):
                break
            
            # Perform search
            results = search.search(
                query,
                use_bert=use_bert,
                use_openai=use_openai,
                top_k=args.top_k,
                threshold=args.threshold,
                domain_filter=args.domain
            )
            
            # Print results
            print_results(results, query)
    
    # Single query mode
    elif args.query:
        # Perform search
        results = search.search(
            args.query,
            use_bert=use_bert,
            use_openai=use_openai,
            top_k=args.top_k,
            threshold=args.threshold,
            domain_filter=args.domain
        )
        
        # Print results
        print_results(results, args.query)
    
    else:
        console.print("[yellow]No query provided. Use --interactive for interactive mode or provide a query.[/yellow]")

if __name__ == "__main__":
    main() 