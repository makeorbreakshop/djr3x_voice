#!/usr/bin/env python3
"""
Pinecone Diagnostic Script

This script performs a manual query against the Pinecone vector database
and analyzes the results for relevance and similarity patterns.

Usage:
  python pinecone_diagnostic.py [query]

Example:
  python pinecone_diagnostic.py "Who is Luke Skywalker?"
  python pinecone_diagnostic.py "Luke Skywalker" --metadata-filter='{"id":{"$in":["12250","12253"]}}'
  python pinecone_diagnostic.py "Luke Skywalker" --id-filter 12250,12253
  python pinecone_diagnostic.py "Luke Skywalker" --post-filter-ids 12250,12253
"""

import os
import sys
import logging
import argparse
import json
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Union, Optional
from pinecone import Pinecone
from dotenv import load_dotenv
from openai import OpenAI

# Configure logging with detailed formatting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"pinecone_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def generate_embedding(text: str) -> List[float]:
    """
    Generate an embedding for the query text using OpenAI's API.
    
    Args:
        text: The text to embed
        
    Returns:
        Embedding vector as a list of floats
    """
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        embedding = response.data[0].embedding
        logger.info(f"Generated embedding with dimension: {len(embedding)}")
        
        # Check for zero values in the embedding
        zero_count = embedding.count(0.0)
        if zero_count > 0:
            logger.warning(f"Embedding contains {zero_count} zero values")
            
        # Calculate embedding norm to check if it's normalized
        norm = np.linalg.norm(embedding)
        logger.info(f"Embedding L2 norm: {norm:.6f}")
        
        # Normalize the embedding for cosine similarity
        normalized = np.array(embedding) / norm
        
        return normalized.tolist()
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        sys.exit(1)

def build_id_filter(id_list: List[str]) -> Dict[str, Any]:
    """
    Build a metadata filter for IDs that works across Pinecone versions.
    
    Args:
        id_list: List of IDs to filter by
        
    Returns:
        A filter dictionary compatible with Pinecone
    """
    # Try both common patterns for ID filtering in different Pinecone versions
    if len(id_list) == 1:
        # Single ID - use exact match which works in most versions
        return {"id": id_list[0]}
    else:
        # Multiple IDs - try modern $in operator first
        return {"id": {"$in": id_list}}

def test_query(
    query_text: str, 
    top_k: int = 5, 
    threshold: float = 0.01,
    namespace: str = "",
    metadata_filter: Optional[Dict[str, Any]] = None,
    id_filter: Optional[List[str]] = None,
    post_filter_ids: Optional[List[str]] = None
):
    """
    Run a test query against Pinecone and analyze the results.
    
    Args:
        query_text: The text query
        top_k: Maximum number of results to return
        threshold: Minimum similarity threshold
        namespace: Pinecone namespace to search
        metadata_filter: Optional metadata filter
        id_filter: Optional list of IDs to filter results
        post_filter_ids: Optional list of IDs to manually filter results after query
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
        
        # Log index stats - handle different Pinecone SDK versions
        try:
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
        except Exception as e:
            logger.error(f"Error getting index stats: {e}")
        
        # Generate embedding for query
        logger.info(f"Generating embedding for query: '{query_text}'")
        query_embedding = generate_embedding(query_text)
        
        # Log metadata filter if provided
        combined_filter = None
        
        # Use post-filtering approach if requested
        if post_filter_ids:
            logger.info(f"Will manually filter results for IDs: {post_filter_ids}")
            # Need to increase top_k for post-filtering to ensure we get enough results
            effective_top_k = 100  # Fetch more results to ensure we can find our IDs
            logger.info(f"Using increased top_k={effective_top_k} for post-filtering")
        else:
            effective_top_k = None  # Will use loop values
            
            # Otherwise use standard filtering
            if id_filter:
                id_filter_dict = build_id_filter(id_filter)
                logger.info(f"Using ID filter: {json.dumps(id_filter_dict)}")
                combined_filter = id_filter_dict
            elif metadata_filter:
                logger.info(f"Using metadata filter: {json.dumps(metadata_filter)}")
                combined_filter = metadata_filter
        
        # Run query with increasing top_k values to check relevance drop-off
        for k in [5, 10, 20]:
            actual_k = effective_top_k or k
            logger.info(f"\n=== Testing with top_k={k} ===")
            
            # Try different filter approaches for compatibility with different Pinecone versions
            error_count = 0
            results = None
            
            # Skip filtering if we're doing post-filtering
            if post_filter_ids:
                try:
                    results = index.query(
                        namespace=namespace,
                        vector=query_embedding,
                        top_k=actual_k,
                        include_metadata=True
                    )
                except Exception as e:
                    logger.error(f"Error executing query: {e}")
                    continue
            else:
                # Use standard filtering approaches
                for filter_attempt in range(3):
                    try:
                        if filter_attempt == 0:
                            # Standard approach
                            results = index.query(
                                namespace=namespace,
                                vector=query_embedding,
                                top_k=actual_k,
                                include_metadata=True,
                                filter=combined_filter
                            )
                            break
                        elif filter_attempt == 1 and id_filter:
                            # Alternative ID filter approach for older versions
                            if len(id_filter) == 1:
                                alt_filter = {"id": id_filter[0]}
                            else:
                                # Some versions use "eq" instead of "$eq"
                                alt_filter = {"id": {"eq": id_filter}}
                            
                            logger.info(f"Trying alternative ID filter: {json.dumps(alt_filter)}")
                            results = index.query(
                                namespace=namespace,
                                vector=query_embedding,
                                top_k=actual_k,
                                include_metadata=True,
                                filter=alt_filter
                            )
                            break
                        elif filter_attempt == 2:
                            # Last resort - no filter
                            logger.warning("Trying query without filter after previous failures")
                            results = index.query(
                                namespace=namespace,
                                vector=query_embedding,
                                top_k=actual_k,
                                include_metadata=True
                            )
                            break
                    except TypeError as e:
                        # Handle potential parameter mismatch in different SDK versions
                        logger.error(f"TypeError in query attempt {filter_attempt}: {e}")
                        error_count += 1
                        if filter_attempt == 2:
                            logger.error("All query attempts failed")
                            continue
                    except Exception as e:
                        logger.error(f"Error executing query (attempt {filter_attempt}): {e}")
                        error_count += 1
                        if filter_attempt == 2:
                            logger.error("All query attempts failed")
                            continue
                
                if error_count == 3:
                    logger.error("All query approaches failed, skipping this top_k value")
                    continue
            
            if not results:
                logger.error("No results object available, skipping this top_k value")
                continue
                
            # Analyze results
            if not hasattr(results, 'matches'):
                logger.error("Query results don't have 'matches' attribute")
                if isinstance(results, dict) and 'matches' in results:
                    matches = results['matches']
                else:
                    logger.error(f"Unexpected results format: {results}")
                    continue
            else:
                matches = results.matches
                
            logger.info(f"Retrieved {len(matches)} matches")
            
            # Post-filter by ID if requested
            if post_filter_ids:
                logger.info(f"Applying post-query ID filter for IDs: {post_filter_ids}")
                
                # Handle both object and dict formats for matches
                filtered_matches = []
                for match in matches:
                    match_id = match.get('id', None) if isinstance(match, dict) else getattr(match, 'id', None)
                    if match_id in post_filter_ids:
                        filtered_matches.append(match)
                
                logger.info(f"Post-filtering kept {len(filtered_matches)} of {len(matches)} matches")
                matches = filtered_matches
                
                # Limit to requested top_k after filtering
                if len(matches) > k:
                    matches = matches[:k]
                    logger.info(f"Limited to top {k} matches after filtering")
            
            # Check if no matches were found after filtering
            if not matches:
                logger.warning("No matches found after filtering")
                continue
                
            # Check if sparse vectors are present in results
            has_sparse = False
            for match in matches:
                if (hasattr(match, 'sparse_values') and match.sparse_values) or \
                   (isinstance(match, dict) and 'sparse_values' in match and match['sparse_values']):
                    has_sparse = True
                    break
                    
            if has_sparse:
                logger.info("Results contain sparse vector values (hybrid search)")
            else:
                logger.info("Results contain only dense vector values (standard search)")
            
            # Print each match with details
            logger.info("\nMatches by relevance:")
            for i, match in enumerate(matches):
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
                    content = metadata.get('content', '')[:200] + '...' if metadata.get('content') else 'No content'
                else:
                    content = getattr(metadata, 'content', '')[:200] + '...' if hasattr(metadata, 'content') else 'No content'
                
                # Calculate percentile to see where this result ranks
                percentile = 100 * (k - i) / k
                
                logger.info(f"\n[{i+1}/{len(matches)}] ID: {id}")
                logger.info(f"Score: {score:.6f} (Top {percentile:.1f}%)")
                
                # Check if score is above threshold
                if score < threshold:
                    logger.warning(f"Score below threshold ({threshold})")
                
                # Check for sparse values if present
                sparse_values = None
                if isinstance(match, dict) and 'sparse_values' in match:
                    sparse_values = match['sparse_values']
                elif hasattr(match, 'sparse_values'):
                    sparse_values = match.sparse_values
                    
                if sparse_values:
                    if isinstance(sparse_values, dict) and 'indices' in sparse_values:
                        logger.info(f"Sparse values: {len(sparse_values['indices'])} non-zero elements")
                    else:
                        logger.info(f"Sparse values present in non-standard format: {sparse_values}")
                
                # Print metadata fields
                if isinstance(metadata, dict):
                    for key, value in metadata.items():
                        if key != 'content':  # Content is handled separately
                            if isinstance(value, str) and len(value) > 50:
                                value = value[:50] + '...'
                            logger.info(f"{key}: {value}")
                else:
                    logger.info(f"Metadata: {metadata}")
                
                # Print content snippet
                logger.info(f"Content: {content}")
            
            # Results analysis
            if len(matches) > 0:
                # Extract scores - handle both object and dict formats
                scores = []
                for match in matches:
                    if isinstance(match, dict):
                        scores.append(match.get('score', 0))
                    else:
                        scores.append(getattr(match, 'score', 0))
                
                max_score = max(scores)
                min_score = min(scores)
                avg_score = sum(scores) / len(scores)
                
                logger.info("\nResults Analysis:")
                logger.info(f"Score range: {min_score:.6f} to {max_score:.6f}")
                logger.info(f"Average score: {avg_score:.6f}")
                
                # Check drop-off pattern (how quickly relevance declines)
                if len(scores) > 1:
                    drop_offs = [scores[i] - scores[i+1] for i in range(len(scores)-1)]
                    avg_drop = sum(drop_offs) / len(drop_offs)
                    max_drop = max(drop_offs)
                    logger.info(f"Average score drop between positions: {avg_drop:.6f}")
                    logger.info(f"Maximum score drop: {max_drop:.6f}")
                    
                    # Check if there's a steep cliff (indicates potential relevance boundary)
                    if max_drop > 3 * avg_drop:
                        cliff_position = drop_offs.index(max_drop) + 1
                        logger.info(f"Potential relevance cliff after position {cliff_position} (drop: {max_drop:.6f})")
            else:
                logger.warning("No results found for query")
        
    except Exception as e:
        logger.error(f"Error testing query: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

def parse_json_arg(json_str):
    """Parse a JSON string argument."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {e}")

def parse_id_list(id_str):
    """Parse a comma-separated list of IDs."""
    return [id.strip() for id in id_str.split(',')]

def main():
    """Main function to parse arguments and run the query test."""
    parser = argparse.ArgumentParser(description="Test Pinecone vector database queries")
    parser.add_argument("query", nargs="?", default="Who is Luke Skywalker?", 
                      help="Query text to test (default: 'Who is Luke Skywalker?')")
    parser.add_argument("--top-k", type=int, default=5,
                      help="Maximum number of results to return (default: 5)")
    parser.add_argument("--threshold", type=float, default=0.01,
                      help="Minimum similarity threshold (default: 0.01)")
    parser.add_argument("--namespace", default="",
                      help="Pinecone namespace to search (default: empty string)")
    parser.add_argument("--metadata-filter", type=parse_json_arg, default=None,
                      help="Metadata filter as JSON string. Example: '{\"id\":{\"$in\":[\"12250\",\"12253\"]}}'")
    parser.add_argument("--id-filter", type=parse_id_list, default=None,
                      help="Comma-separated list of IDs to filter results. Example: '12250,12253'")
    parser.add_argument("--post-filter-ids", type=parse_id_list, default=None,
                      help="Comma-separated list of IDs to filter results after the query. Example: '12250,12253'")
    
    args = parser.parse_args()
    
    # Validate that only one filter type is provided
    filter_count = sum(1 for x in [args.metadata_filter, args.id_filter, args.post_filter_ids] if x is not None)
    if filter_count > 1:
        logger.error("Cannot use multiple filter types at the same time")
        sys.exit(1)
    
    logger.info(f"Starting Pinecone diagnostic with query: '{args.query}'")
    test_query(
        query_text=args.query,
        top_k=args.top_k,
        threshold=args.threshold,
        namespace=args.namespace,
        metadata_filter=args.metadata_filter,
        id_filter=args.id_filter,
        post_filter_ids=args.post_filter_ids
    )
    logger.info("Diagnostic complete")

if __name__ == "__main__":
    main() 