#!/usr/bin/env python3
"""
Export Supabase holocron_knowledge table data to a format compatible with Pinecone.

This script:
1. Connects directly to your Supabase PostgreSQL database using SQL
2. Fetches all data from the holocron_knowledge table
3. Processes the vector embeddings
4. Saves the data in formats compatible with Pinecone (JSON and Parquet)

Usage:
  python export_supabase_to_pinecone.py [--batch-size=500]
  
Options:
  --batch-size=N   Number of records to fetch in each batch (default: 500)

Examples:
  # Export with default settings (batch size 500)
  python export_supabase_to_pinecone.py
  
  # Export with larger batch size (faster)
  python export_supabase_to_pinecone.py --batch-size=1000

Requirements:
  - Python 3.8+
  - psycopg2-binary
  - pandas
  - pyarrow
  - numpy
  - tqdm (for progress bars)
  - dotenv
"""

import os
import json
import logging
import time
from typing import List, Dict, Any
import pandas as pd
from dotenv import load_dotenv
from mcp.supabase import execute_sql

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
OUTPUT_DIR = "pinecone_data"
HOLOCRON_TABLE = "holocron_knowledge"
BATCH_SIZE = 1000  # Increased batch size since we're using direct SQL
PROJECT_ID = "xkotscjkvejcgrweolsd"

def fetch_batch(offset: int, limit: int) -> List[Dict[str, Any]]:
    """Fetch a batch of records using SQL"""
    query = f"""
    SELECT id, content, content_tokens, metadata, embedding 
    FROM {HOLOCRON_TABLE}
    ORDER BY id
    LIMIT {limit} 
    OFFSET {offset};
    """
    try:
        result = execute_sql(PROJECT_ID, query)
        return result.get("data", [])
    except Exception as e:
        logger.error(f"Error fetching batch: {str(e)}")
        return []

def get_total_count() -> int:
    """Get total count of records"""
    query = f"SELECT COUNT(*) as count FROM {HOLOCRON_TABLE};"
    try:
        result = execute_sql(PROJECT_ID, query)
        return result["data"][0]["count"]
    except Exception as e:
        logger.error(f"Error getting count: {str(e)}")
        return 0

def main():
    """Main export function"""
    try:
        # Create output directory if it doesn't exist
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Get total count of records
        total_records = get_total_count()
        logger.info(f"Total records to process: {total_records}")
        
        if total_records == 0:
            logger.error("No records found")
            return
        
        # Process in batches
        all_records = []
        for offset in range(0, total_records, BATCH_SIZE):
            logger.info(f"Processing batch starting at offset {offset}")
            
            try:
                # Fetch batch of records
                records = fetch_batch(offset, BATCH_SIZE)
                
                if not records:
                    logger.warning(f"No records returned for batch at offset {offset}")
                    continue
                    
                # Add to all records
                all_records.extend(records)
                
                # Save intermediate batch
                batch_num = offset // BATCH_SIZE
                output_file = os.path.join(OUTPUT_DIR, f"batch_{batch_num}.json")
                
                # Convert to DataFrame and save
                df = pd.DataFrame(records)
                df.to_json(output_file, orient="records")
                
                logger.info(f"Saved batch {batch_num} to {output_file}")
                
                # Small delay between batches
                time.sleep(0.5)
                
            except Exception as batch_error:
                logger.error(f"Error processing batch at offset {offset}: {str(batch_error)}")
                continue
        
        # Save complete dataset
        if all_records:
            final_output = os.path.join(OUTPUT_DIR, "complete_export.json")
            df_complete = pd.DataFrame(all_records)
            df_complete.to_json(final_output, orient="records")
            logger.info(f"Saved complete export to {final_output}")
            
        logger.info("Export completed successfully")
        
    except Exception as e:
        logger.error(f"Error during export: {str(e)}")
        raise

if __name__ == "__main__":
    main() 