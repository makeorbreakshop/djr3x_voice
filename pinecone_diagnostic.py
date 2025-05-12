#!/usr/bin/env python3
"""
Analyze Pinecone vectors to understand content chunking and distribution.
"""

import os
import json
import logging
from datetime import datetime
from pinecone import Pinecone
from collections import defaultdict
import statistics
import tiktoken

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"pinecone_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def count_tokens(text: str) -> int:
    """Count tokens in text using OpenAI's tokenizer."""
    tokenizer = tiktoken.get_encoding("cl100k_base")
    return len(tokenizer.encode(text))

def analyze_vectors():
    """Analyze vectors in Pinecone index to understand content distribution."""
    
    # Initialize Pinecone
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("PINECONE_API_KEY environment variable not set")
    
    pc = Pinecone(api_key=api_key)
    index = pc.Index("holocron-knowledge")  # Updated index name
    
    # Get index stats
    stats = index.describe_index_stats()
    total_vectors = stats.total_vector_count
    logger.info(f"Total vectors in index: {total_vectors}")
    
    # Sample vectors for analysis
    BATCH_SIZE = 100
    content_lengths = []
    token_counts = []
    url_counts = defaultdict(int)
    chunk_counts = defaultdict(int)
    
    # Fetch vectors in batches
    for i in range(0, min(total_vectors, 1000), BATCH_SIZE):
        query_response = index.query(
            vector=[0] * 1536,  # Dummy vector for metadata fetch
            top_k=BATCH_SIZE,
            include_metadata=True
        )
        
        for match in query_response.matches:
            metadata = match.metadata
            if not metadata:
                continue
                
            # Analyze content length and tokens
            content = metadata.get('content', '')
            content_lengths.append(len(content))
            token_counts.append(count_tokens(content))
            
            # Track URLs and chunks
            url = metadata.get('url', '')
            chunk_id = metadata.get('chunk_id', '')
            
            if url:
                url_counts[url] += 1
            if chunk_id:
                chunk_counts[url] += 1
    
    # Calculate statistics
    if content_lengths and token_counts:
        avg_length = statistics.mean(content_lengths)
        median_length = statistics.median(content_lengths)
        std_dev = statistics.stdev(content_lengths) if len(content_lengths) > 1 else 0
        
        avg_tokens = statistics.mean(token_counts)
        median_tokens = statistics.median(token_counts)
        token_std_dev = statistics.stdev(token_counts) if len(token_counts) > 1 else 0
        
        logger.info(f"\nContent Statistics:")
        logger.info(f"Average length: {avg_length:.2f} characters")
        logger.info(f"Median length: {median_length:.2f} characters")
        logger.info(f"Character std dev: {std_dev:.2f}")
        logger.info(f"Min length: {min(content_lengths)} characters")
        logger.info(f"Max length: {max(content_lengths)} characters")
        
        logger.info(f"\nToken Statistics:")
        logger.info(f"Average tokens: {avg_tokens:.2f}")
        logger.info(f"Median tokens: {median_tokens:.2f}")
        logger.info(f"Token std dev: {token_std_dev:.2f}")
        logger.info(f"Min tokens: {min(token_counts)}")
        logger.info(f"Max tokens: {max(token_counts)}")
    
    # URL statistics
    logger.info(f"\nURL Statistics:")
    logger.info(f"Total unique URLs: {len(url_counts)}")
    logger.info(f"Average chunks per URL: {sum(url_counts.values()) / len(url_counts) if url_counts else 0:.2f}")
    
    # Sample some content for inspection
    logger.info(f"\nSample Content Analysis:")
    for i, (length, tokens) in enumerate(zip(content_lengths[:5], token_counts[:5])):
        logger.info(f"Sample {i+1}: {length} characters, {tokens} tokens")

if __name__ == "__main__":
    analyze_vectors() 