#!/usr/bin/env python3
"""
Script to check Pinecone index status and perform a test query.
"""

import os
import logging
from pinecone import Pinecone
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    # Load environment variables
    load_dotenv()
    
    # Initialize Pinecone
    pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
    index = pc.Index('holocron-knowledge')
    
    # Get index stats
    stats = index.describe_index_stats()
    logger.info(f"Index Stats: {stats}")
    
    # Perform a test query
    test_query = [0.1] * 1536  # Dummy vector for testing
    results = index.query(
        vector=test_query,
        top_k=3,
        include_metadata=True
    )
    
    # Print results
    logger.info("\nSample Records:")
    for match in results.matches:
        logger.info(f"\nID: {match.id}")
        logger.info(f"Score: {match.score}")
        logger.info(f"Metadata: {match.metadata}")

if __name__ == "__main__":
    main() 