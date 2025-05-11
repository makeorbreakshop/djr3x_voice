#!/usr/bin/env python3

import os
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
import logging
from typing import List, Dict, Any
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def convert_csv_to_parquet(
    input_csv: str,
    output_dir: str,
    batch_size: int = 10000,
    namespace: str = "default"
) -> List[str]:
    """
    Convert CSV file to Parquet format, optimized for Pinecone import.
    
    Args:
        input_csv: Path to input CSV file
        output_dir: Directory to store Parquet files
        batch_size: Number of rows per Parquet file
        namespace: Pinecone namespace to organize data
        
    Returns:
        List of generated Parquet file paths
    """
    try:
        # Create output directory if it doesn't exist
        namespace_dir = os.path.join(output_dir, namespace)
        os.makedirs(namespace_dir, exist_ok=True)
        
        # Read CSV in chunks
        chunk_iterator = pd.read_csv(
            input_csv,
            chunksize=batch_size,
            dtype={
                'id': str,
                'content': str,
                'url': str,
                'title': str,
                'priority': str,
                'values': str  # We'll parse this from JSON
            }
        )
        
        parquet_files = []
        for i, chunk in enumerate(chunk_iterator):
            # Parse vector values from JSON string
            chunk['values'] = chunk['values'].apply(json.loads)
            
            # Create metadata dictionary
            chunk['metadata'] = chunk.apply(
                lambda row: {
                    'content': row['content'],
                    'url': row['url'],
                    'title': row['title'],
                    'priority': row['priority']
                },
                axis=1
            )
            
            # Select only columns needed for Pinecone
            pinecone_df = chunk[['id', 'values', 'metadata']]
            
            # Convert to PyArrow Table
            table = pa.Table.from_pandas(pinecone_df)
            
            # Write Parquet file
            output_file = os.path.join(namespace_dir, f"batch_{i:04d}.parquet")
            pq.write_table(
                table,
                output_file,
                compression='snappy'
            )
            
            parquet_files.append(output_file)
            logger.info(f"Converted batch {i} to {output_file}")
        
        return parquet_files
        
    except Exception as e:
        logger.error(f"Error converting CSV to Parquet: {str(e)}")
        raise

def main():
    """Main entry point."""
    try:
        load_dotenv()
        
        # Get paths from environment or use defaults
        input_csv = os.getenv("HOLOCRON_EXPORT_CSV", "data/holocron_knowledge_export.csv")
        output_dir = os.getenv("HOLOCRON_PARQUET_DIR", "data/parquet")
        
        # Convert CSV to Parquet
        parquet_files = convert_csv_to_parquet(
            input_csv=input_csv,
            output_dir=output_dir
        )
        
        logger.info(f"Successfully converted CSV to {len(parquet_files)} Parquet files")
        
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise

if __name__ == "__main__":
    main() 