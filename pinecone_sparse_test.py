#!/usr/bin/env python3
"""
Pinecone Sparse Vector Test Script

This script tests whether the Pinecone index supports hybrid search with sparse vectors
and compares performance between dense-only and hybrid search approaches.

Usage:
  python pinecone_sparse_test.py [query]

Example:
  python pinecone_sparse_test.py "Who is Luke Skywalker?"
"""

import os
import sys
import logging
import argparse
import json
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from pinecone import Pinecone
from dotenv import load_dotenv
from openai import OpenAI

# Configure logging with detailed formatting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"pinecone_sparse_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def generate_dense_embedding(text: str) -> List[float]:
    """
    Generate a dense embedding using OpenAI's text-embedding-ada-002 model.
    
    Args:
        text: The text to embed
        
    Returns:
        Dense embedding vector as a list of floats
    """
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        embedding = response.data[0].embedding
        logger.info(f"Generated dense embedding with dimension: {len(embedding)}")
        
        # Normalize the embedding for cosine similarity
        embedding_array = np.array(embedding)
        norm = np.linalg.norm(embedding_array)
        normalized = embedding_array / norm
        
        return normalized.tolist()
    except Exception as e:
        logger.error(f"Error generating dense embedding: {e}")
        sys.exit(1)

def generate_mock_sparse_embedding(text: str) -> Dict[str, List[int]]:
    """
    Generate a mock sparse embedding from text tokens.
    
    This is a simplified approach for testing - in production, you'd use
    a proper sparse encoding model like BM25 or SPLADE.
    
    Args:
        text: The text to create sparse embedding from
        
    Returns:
        Dictionary with indices and values for sparse vector
    """
    words = text.lower().split()
    # Remove duplicates while preserving order
    unique_words = list(dict.fromkeys(words))
    
    # Create sparse indices (word positions) and values (weights)
    indices = list(range(len(unique_words)))
    values = [1.0] * len(indices)  # Simple weighting for testing
    
    logger.info(f"Generated mock sparse embedding with {len(indices)} non-zero elements")
    return {
        "indices": indices,
        "values": values
    }

def check_sparse_support(index) -> bool:
    """
    Check if the Pinecone index supports sparse vectors.
    
    Args:
        index: Pinecone index object
        
    Returns:
        Boolean indicating if sparse vectors are supported
    """
    try:
        # Get index details
        stats = index.describe_index_stats()
        
        # Try to convert stats to a dictionary, handling different SDK versions
        stats_dict = {}
        try:
            # For newer SDK versions
            if hasattr(stats, 'model_dump'):
                stats_dict = stats.model_dump()
            elif hasattr(stats, 'to_dict'):
                stats_dict = stats.to_dict()
            elif hasattr(stats, '__dict__'):
                stats_dict = stats.__dict__
            else:
                # Fallback for older versions
                stats_dict = {
                    'dimension': getattr(stats, 'dimension', 'unknown'),
                    'index_fullness': getattr(stats, 'index_fullness', 'unknown'),
                    'total_vector_count': getattr(stats, 'total_vector_count', 'unknown'),
                    'namespaces': getattr(stats, 'namespaces', {})
                }
            logger.info(f"Index stats: {json.dumps(stats_dict, default=str)}")
        except Exception as e:
            logger.error(f"Could not convert stats to dictionary: {e}")
            logger.info(f"Raw index stats: {stats}")
        
        # For now, since we can't reliably check, assume support for testing
        logger.info("Assuming sparse vector support for testing purposes")
        return True
    except Exception as e:
        logger.error(f"Error checking sparse support: {e}")
        return False

def test_query_types(
    query_text: str, 
    top_k: int = 5,
    namespace: str = ""
):
    """
    Run queries using different vector types and compare results.
    
    Args:
        query_text: The text query
        top_k: Maximum number of results to return
        namespace: Pinecone namespace to search
    """
    try:
        # Initialize Pinecone
        logger.info(f"Initializing Pinecone connection")
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            logger.error("PINECONE_API_KEY not found in environment variables")
            sys.exit(1)
            
        pc = Pinecone(api_key=api_key)
        index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")
        
        # Check if index exists
        try:
            # This handles both old and new versions of the Pinecone SDK
            index = pc.Index(name=index_name)
        except Exception as e:
            logger.error(f"Error connecting to index '{index_name}': {e}")
            try:
                # Alternative way to get index
                logger.info("Trying alternative method to connect to index...")
                index = pc.Index(index_name)
            except Exception as e2:
                logger.error(f"Failed to connect to index: {e2}")
                sys.exit(1)
        
        # Check if index might support sparse vectors
        sparse_supported = check_sparse_support(index)
        
        # Generate dense embedding
        dense_vector = generate_dense_embedding(query_text)
        
        # Run dense-only query
        logger.info(f"\n=== Running dense-only query with top_k={top_k} ===")
        try:
            dense_results = index.query(
                namespace=namespace,
                vector=dense_vector,
                top_k=top_k,
                include_metadata=True
            )
        except TypeError as e:
            # Handle potential parameter mismatch in different SDK versions
            logger.error(f"TypeError in query: {e}")
            logger.info("Trying alternative query syntax...")
            try:
                # Try without namespace parameter (older versions)
                dense_results = index.query(
                    vector=dense_vector,
                    top_k=top_k,
                    include_metadata=True
                )
            except Exception as e2:
                logger.error(f"Query failed with alternative syntax: {e2}")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Error executing query: {e}")
            sys.exit(1)
        
        # Extract matches from results, handling different response formats
        dense_matches = []
        if hasattr(dense_results, 'matches'):
            dense_matches = dense_results.matches
        elif isinstance(dense_results, dict) and 'matches' in dense_results:
            dense_matches = dense_results['matches']
        else:
            logger.error(f"Unexpected results format: {dense_results}")
            sys.exit(1)
        
        # Print dense results
        logger.info(f"Retrieved {len(dense_matches)} matches for dense query")
        for i, match in enumerate(dense_matches):
            # Extract key information - handle both object and dict formats
            if isinstance(match, dict):
                score = match.get('score', 0)
                id = match.get('id', 'unknown')
                metadata = match.get('metadata', {})
            else:
                score = getattr(match, 'score', 0)
                id = getattr(match, 'id', 'unknown')
                metadata = getattr(match, 'metadata', {})
            
            # Handle metadata content
            if isinstance(metadata, dict):
                content = metadata.get('content', '')[:100] + '...' if metadata.get('content') else 'No content'
            else:
                content = getattr(metadata, 'content', '')[:100] + '...' if hasattr(metadata, 'content') else 'No content'
            
            logger.info(f"\n[{i+1}/{top_k}] ID: {id}")
            logger.info(f"Score: {score:.6f}")
            logger.info(f"Content: {content}")
        
        # Store IDs for comparison
        dense_ids = []
        for match in dense_matches:
            if isinstance(match, dict):
                dense_ids.append(match.get('id', 'unknown'))
            else:
                dense_ids.append(getattr(match, 'id', 'unknown'))
        
        # If sparse vectors might be supported, try hybrid search
        if sparse_supported:
            # Generate mock sparse embedding
            sparse_vector = generate_mock_sparse_embedding(query_text)
            
            # Try hybrid search (may fail if not actually supported)
            try:
                logger.info(f"\n=== Attempting hybrid query with top_k={top_k} ===")
                hybrid_results = index.query(
                    namespace=namespace,
                    vector=dense_vector,
                    sparse_vector=sparse_vector,
                    top_k=top_k,
                    include_metadata=True
                )
                
                # Extract matches from results, handling different response formats
                hybrid_matches = []
                if hasattr(hybrid_results, 'matches'):
                    hybrid_matches = hybrid_results.matches
                elif isinstance(hybrid_results, dict) and 'matches' in hybrid_results:
                    hybrid_matches = hybrid_results['matches']
                else:
                    logger.error(f"Unexpected hybrid results format: {hybrid_results}")
                    return
                
                # Print hybrid results
                logger.info(f"Retrieved {len(hybrid_matches)} matches for hybrid query")
                for i, match in enumerate(hybrid_matches):
                    # Extract key information - handle both object and dict formats
                    if isinstance(match, dict):
                        score = match.get('score', 0)
                        id = match.get('id', 'unknown')
                        metadata = match.get('metadata', {})
                    else:
                        score = getattr(match, 'score', 0)
                        id = getattr(match, 'id', 'unknown')
                        metadata = getattr(match, 'metadata', {})
                    
                    # Handle metadata content
                    if isinstance(metadata, dict):
                        content = metadata.get('content', '')[:100] + '...' if metadata.get('content') else 'No content'
                    else:
                        content = getattr(metadata, 'content', '')[:100] + '...' if hasattr(metadata, 'content') else 'No content'
                    
                    logger.info(f"\n[{i+1}/{top_k}] ID: {id}")
                    logger.info(f"Score: {score:.6f}")
                    logger.info(f"Content: {content}")
                
                # Extract IDs for comparison
                hybrid_ids = []
                for match in hybrid_matches:
                    if isinstance(match, dict):
                        hybrid_ids.append(match.get('id', 'unknown'))
                    else:
                        hybrid_ids.append(getattr(match, 'id', 'unknown'))
                
                # Compare result sets
                common_ids = set(dense_ids).intersection(set(hybrid_ids))
                
                logger.info("\n=== Results Comparison ===")
                logger.info(f"Dense query found {len(dense_ids)} results")
                logger.info(f"Hybrid query found {len(hybrid_ids)} results")
                logger.info(f"Results in common: {len(common_ids)}")
                
                # Calculate overlap percentage
                if len(dense_ids) > 0 and len(hybrid_ids) > 0:
                    overlap_pct = 100 * len(common_ids) / min(len(dense_ids), len(hybrid_ids))
                    logger.info(f"Overlap percentage: {overlap_pct:.1f}%")
                    
                    if overlap_pct < 100:
                        logger.info("\nUnique results in hybrid search:")
                        unique_to_hybrid = set(hybrid_ids) - set(dense_ids)
                        for unique_id in unique_to_hybrid:
                            # Find the match with this ID
                            for match in hybrid_matches:
                                match_id = match.get('id', 'unknown') if isinstance(match, dict) else getattr(match, 'id', 'unknown')
                                if match_id == unique_id:
                                    score = match.get('score', 0) if isinstance(match, dict) else getattr(match, 'score', 0)
                                    
                                    # Get content
                                    if isinstance(match, dict):
                                        metadata = match.get('metadata', {})
                                        content = metadata.get('content', '')[:100] + '...' if metadata.get('content') else 'No content'
                                    else:
                                        metadata = getattr(match, 'metadata', {})
                                        content = getattr(metadata, 'content', '')[:100] + '...' if hasattr(metadata, 'content') else 'No content'
                                    
                                    logger.info(f"ID: {unique_id}, Score: {score:.6f}")
                                    logger.info(f"Content: {content}")
                                    break
            
            except Exception as e:
                logger.error(f"Hybrid search failed: {e}")
                logger.info("This index may not support sparse vectors or hybrid search")
                import traceback
                logger.error(traceback.format_exc())
        else:
            logger.info("Sparse vector support not detected in this index")
    
    except Exception as e:
        logger.error(f"Error in test: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

def main():
    """Main function to parse arguments and run the tests."""
    parser = argparse.ArgumentParser(description="Test Pinecone sparse vector capabilities")
    parser.add_argument("query", nargs="?", default="Who is Luke Skywalker?", 
                      help="Query text to test (default: 'Who is Luke Skywalker?')")
    parser.add_argument("--top-k", type=int, default=5,
                      help="Maximum number of results to return (default: 5)")
    parser.add_argument("--namespace", default="",
                      help="Pinecone namespace to search (default: empty string)")
    
    args = parser.parse_args()
    
    logger.info(f"Starting Pinecone sparse vector test with query: '{args.query}'")
    test_query_types(
        query_text=args.query,
        top_k=args.top_k,
        namespace=args.namespace
    )
    logger.info("Test complete")

if __name__ == "__main__":
    main() 