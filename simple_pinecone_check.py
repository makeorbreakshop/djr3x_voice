#!/usr/bin/env python3
"""
Simple Pinecone Connection Check

Basic script to verify Pinecone connection and query functionality.
"""

import os
import sys
import logging
from dotenv import load_dotenv
import time

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def main():
    """Basic Pinecone connection and query test."""
    try:
        # Get API key from environment
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            logger.error("PINECONE_API_KEY not found in environment")
            sys.exit(1)
            
        # Report environment variables for debugging
        index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")
        logger.info(f"Using index name: {index_name}")
        
        # Try to import Pinecone
        logger.info("Importing Pinecone client...")
        try:
            from pinecone import Pinecone
            logger.info("Successfully imported Pinecone")
        except ImportError:
            logger.error("Could not import Pinecone. Please check your installation.")
            logger.info("Trying the older Pinecone client...")
            try:
                import pinecone
                logger.info("Successfully imported older Pinecone client")
                logger.info("Using older client initialization method...")
                pinecone.init(api_key=api_key)
                index = pinecone.Index(index_name)
                stats = index.describe_index_stats()
                logger.info(f"Successfully connected to index using older client")
                logger.info(f"Index stats: {stats}")
                
                # Run a basic query
                logger.info("\nRunning basic query with older client...")
                test_vector = [0.1] * 1536
                results = index.query(vector=test_vector, top_k=1, include_metadata=True)
                logger.info("Query successful")
                sys.exit(0)
            except Exception as e:
                logger.error(f"Error with older client: {e}")
                sys.exit(1)
            
        # Connect to Pinecone with newer client
        logger.info("Initializing Pinecone with newer client...")
        try:
            pc = Pinecone(api_key=api_key)
            
            # Try to connect to the index
            logger.info(f"Connecting to index: {index_name}")
            index = pc.Index(name=index_name)
            logger.info("Successfully connected to index")
            
            # Get stats
            logger.info("Getting index stats...")
            stats = index.describe_index_stats()
            logger.info(f"Successfully retrieved stats: {stats}")
            
            # Run a basic query
            logger.info("\nRunning basic query...")
            test_vector = [0.1] * 1536
            results = index.query(
                vector=test_vector,
                top_k=1,
                include_metadata=True
            )
            logger.info("Query successful")
            
            # Print a sample result if available
            if hasattr(results, 'matches') and len(results.matches) > 0:
                logger.info("Sample match found")
            else:
                logger.info("No matches found")
            
        except AttributeError as e:
            logger.error(f"AttributeError: {e}")
            logger.error("This usually indicates a version mismatch or API change")
            logger.error("Check that your Pinecone SDK version matches the documentation you're following")
            sys.exit(1)
            
        except Exception as e:
            logger.error(f"Error during Pinecone operations: {e}")
            import traceback
            logger.error(traceback.format_exc())
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main() 