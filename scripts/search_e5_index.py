#!/usr/bin/env python3
"""
Search the E5 index using text queries.
This script handles text search by generating E5 embeddings for queries
and searching the index with the embeddings.
"""

import os
import logging
from typing import List, Dict, Any
import argparse
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class E5Searcher:
    """Handles text search using E5 embeddings."""
    
    def __init__(
        self,
        api_key: str,
        index_name: str = "holocron-sbert-e5",
        model_name: str = "intfloat/e5-small-v2",
        namespace: str = ""
    ):
        """
        Initialize the searcher.
        
        Args:
            api_key: Pinecone API key
            index_name: Name of the index to search
            model_name: E5 model name to use
            namespace: Namespace to search in
        """
        self.index_name = index_name
        self.namespace = namespace
        
        # Initialize Pinecone client
        self.pc = Pinecone(api_key=api_key)
        self.index = self.pc.Index(self.index_name)
        
        # Load E5 model
        logger.info(f"Loading E5 model: {model_name}")
        self.model = SentenceTransformer(model_name)
    
    def generate_query_embedding(self, query: str) -> List[float]:
        """
        Generate E5 embedding for a query.
        
        Args:
            query: The text query
            
        Returns:
            List of embedding values
        """
        # E5 instruction prefix for queries
        query = f"query: {query}"
        embedding = self.model.encode(query)
        return embedding.tolist()
    
    def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Search for similar content using a text query.
        
        Args:
            query: Text query to search for
            top_k: Maximum number of results to return
            threshold: Minimum similarity threshold
            
        Returns:
            List of search results with content and metadata
        """
        # Generate query embedding
        query_embedding = self.generate_query_embedding(query)
        
        # Search index
        results = self.index.query(
            vector=query_embedding,
            top_k=top_k,
            namespace=self.namespace,
            include_metadata=True
        )
        
        # Process results
        search_results = []
        for match in results.matches:
            if match.score >= threshold:
                result = {
                    'id': match.id,
                    'content': match.metadata.get('content', ''),
                    'title': match.metadata.get('title', ''),
                    'url': match.metadata.get('url', ''),
                    'similarity': match.score
                }
                search_results.append(result)
        
        return search_results

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Search E5 index using text queries")
    
    parser.add_argument("--api-key", type=str, help="Pinecone API key")
    parser.add_argument("--index", type=str, default="holocron-sbert-e5", help="Index name")
    parser.add_argument("--namespace", type=str, default="", help="Namespace")
    parser.add_argument("--model", type=str, default="intfloat/e5-small-v2", help="E5 model name")
    parser.add_argument("--query", type=str, required=True, help="Text query to search for")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results to return")
    parser.add_argument("--threshold", type=float, default=0.3, help="Minimum similarity threshold")
    
    args = parser.parse_args()
    
    # If API key not provided, try to get from environment
    api_key = args.api_key or os.environ.get("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("Pinecone API key must be provided through --api-key or PINECONE_API_KEY environment variable")
    
    # Create searcher
    searcher = E5Searcher(
        api_key=api_key,
        index_name=args.index,
        model_name=args.model,
        namespace=args.namespace
    )
    
    # Search and display results
    results = searcher.search(
        query=args.query,
        top_k=args.top_k,
        threshold=args.threshold
    )
    
    # Print results
    print(f"\nFound {len(results)} results for query: '{args.query}'\n")
    for i, result in enumerate(results, 1):
        print(f"[{i}] Score: {result['similarity']:.4f}")
        print(f"Title: {result['title']}")
        print(f"URL: {result['url']}")
        print(f"Content: {result['content'][:200]}...")
        print()

if __name__ == "__main__":
    main() 