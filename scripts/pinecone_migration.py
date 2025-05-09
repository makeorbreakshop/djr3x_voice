#!/usr/bin/env python3

import os
import pinecone
import pandas as pd
from dotenv import load_dotenv
import asyncio
import logging
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
if not PINECONE_API_KEY:
    raise ValueError("PINECONE_API_KEY not found in environment variables")

async def init_pinecone() -> None:
    """Initialize Pinecone and create index if it doesn't exist."""
    # Initialize Pinecone
    pinecone.init(api_key=PINECONE_API_KEY, environment="gcp-starter")
    
    # Check if our index already exists
    if "holocron-knowledge" not in pinecone.list_indexes():
        # Create a new index
        pinecone.create_index(
            name="holocron-knowledge",
            dimension=1536,  # OpenAI embeddings dimension
            metric="cosine"
        )
        logger.info("Created new Pinecone index: holocron-knowledge")
    
    # Update environment variables
    with open(".env", "a") as f:
        f.write("\nPINECONE_INDEX_NAME=holocron-knowledge")
        f.write(f"\nPINECONE_INDEX_HOST={pinecone.describe_index('holocron-knowledge').host}")

async def load_data(csv_path: str) -> pd.DataFrame:
    """Load the exported CSV data."""
    return pd.read_csv(csv_path)

async def prepare_vectors(df: pd.DataFrame) -> List[Dict[Any, Any]]:
    """Prepare vectors for Pinecone import."""
    vectors = []
    for _, row in df.iterrows():
        vector = {
            'id': str(row['id']),  # Ensure ID is string
            'values': eval(row['embedding']),  # Convert string representation to list
            'metadata': {
                'content': row['content'],
                'url': row['url'],
                'title': row['title'],
                'priority': row['priority']
            }
        }
        vectors.append(vector)
    return vectors

async def upload_to_pinecone(vectors: List[Dict[Any, Any]], batch_size: int = 100) -> None:
    """Upload vectors to Pinecone in batches."""
    index = pinecone.Index("holocron-knowledge")
    
    total_vectors = len(vectors)
    for i in range(0, total_vectors, batch_size):
        batch = vectors[i:i + batch_size]
        try:
            index.upsert(vectors=batch)
            logger.info(f"Uploaded batch {i//batch_size + 1}/{(total_vectors + batch_size - 1)//batch_size}")
        except Exception as e:
            logger.error(f"Error uploading batch {i//batch_size + 1}: {e}")
            raise

async def main():
    try:
        # Initialize Pinecone and create index
        await init_pinecone()
        
        # Load the exported data
        df = await load_data("data/holocron_knowledge_export.csv")
        logger.info(f"Loaded {len(df)} records from CSV")
        
        # Prepare vectors for import
        vectors = await prepare_vectors(df)
        logger.info("Prepared vectors for import")
        
        # Upload to Pinecone
        await upload_to_pinecone(vectors)
        logger.info("Successfully completed Pinecone migration")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main()) 