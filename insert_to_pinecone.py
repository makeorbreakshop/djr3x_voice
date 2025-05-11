#!/usr/bin/env python3
"""
Script to upload vectors from CSV directly to Pinecone.
"""

import os
import sys
import json
import pandas as pd
import logging
from pinecone import Pinecone
from dotenv import load_dotenv
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def upload_to_pinecone(csv_path: str, batch_size: int = 100):
    """Upload vectors from CSV to Pinecone in batches."""
    # Load environment variables
    load_dotenv()
    
    # Initialize Pinecone
    pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
    index = pc.Index('holocron-knowledge')
    
    # Read CSV file
    logger.info(f"Reading vectors from {csv_path}")
    df = pd.read_csv(csv_path)
    total_vectors = len(df)
    logger.info(f"Found {total_vectors} vectors to upload")
    
    # Process in batches
    for i in tqdm(range(0, total_vectors, batch_size)):
        batch_df = df.iloc[i:i + batch_size]
        
        # Prepare vectors for upload
        vectors = []
        for _, row in batch_df.iterrows():
            # Convert string representation of embedding to list of floats
            embedding = eval(row['embedding'])
            
            # Parse metadata JSON
            try:
                metadata = json.loads(row['metadata'])
            except:
                metadata = {'source': 'unknown'}
            
            vector = {
                'id': str(row['id']),
                'values': embedding,
                'metadata': {
                    'content': row['content'],
                    'tokens': row['content_tokens'],
                    **metadata
                }
            }
            vectors.append(vector)
        
        # Upload batch
        try:
            index.upsert(vectors=vectors)
            logger.info(f"Uploaded batch {i//batch_size + 1}/{(total_vectors + batch_size - 1)//batch_size}")
        except Exception as e:
            logger.error(f"Error uploading batch: {e}")
            continue

def main():
    """Main execution function."""
    csv_path = 'holocron_export_20250509_103822.csv'
    
    try:
        upload_to_pinecone(csv_path)
        logger.info("✅ Upload completed successfully")
    except Exception as e:
        logger.error(f"❌ Upload failed: {e}")

if __name__ == "__main__":
    main() 