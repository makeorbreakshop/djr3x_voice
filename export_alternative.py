#!/usr/bin/env python3
"""
Alternative Holocron Knowledge Export Script Using Direct PostgreSQL Connection

This script exports data directly using psycopg2 to connect to the Supabase Postgres database.
According to Supabase documentation, direct PostgreSQL connections are much faster
than using the Supabase client, especially for large datasets.

Required environment variables:
- SUPABASE_DB_HOST: The database host (usually postgres.YOURPROJECT.supabase.co)
- SUPABASE_DB_PASSWORD: The database password
- SUPABASE_DB_USER: The database user (usually postgres)
- SUPABASE_DB_NAME: The database name (usually postgres)
- SUPABASE_DB_PORT: The database port (usually 5432 or 6543)
"""
import csv
import os
import sys
import time
import json
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Please run: pip install psycopg2-binary")
    sys.exit(1)

# Constants
BATCH_SIZE = 500  # Batch size for efficient exports
MAX_RETRIES = 3   # Maximum number of retries on failure
RETRY_DELAY = 5   # Seconds to wait between retries

# Load env vars if using dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Get PostgreSQL connection parameters from environment variables
DB_HOST = os.environ.get("SUPABASE_DB_HOST")
DB_PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD")
DB_USER = os.environ.get("SUPABASE_DB_USER", "postgres")
DB_NAME = os.environ.get("SUPABASE_DB_NAME", "postgres")
DB_PORT = os.environ.get("SUPABASE_DB_PORT", "5432")

# Check if credentials are available
if not DB_HOST or not DB_PASSWORD:
    print("Error: Database credentials not found. Please set the following environment variables:")
    print("- SUPABASE_DB_HOST: The database host")
    print("- SUPABASE_DB_PASSWORD: The database password")
    print("- SUPABASE_DB_USER: The database user (optional, defaults to 'postgres')")
    print("- SUPABASE_DB_NAME: The database name (optional, defaults to 'postgres')")
    print("- SUPABASE_DB_PORT: The database port (optional, defaults to 5432)")
    sys.exit(1)

def get_db_connection():
    """Create a connection to the PostgreSQL database"""
    connection = psycopg2.connect(
        host=DB_HOST,
        password=DB_PASSWORD,
        user=DB_USER,
        dbname=DB_NAME,
        port=DB_PORT
    )
    # Set autocommit mode
    connection.autocommit = True
    return connection

def execute_query(query, params=None):
    """Execute a query and return results as a list of dictionaries"""
    retry_count = 0
    
    while retry_count < MAX_RETRIES:
        try:
            with get_db_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query, params)
                    
                    # For SELECT queries, return the results
                    if query.strip().upper().startswith("SELECT") or query.strip().upper().startswith("WITH"):
                        return cursor.fetchall()
                    
                    # For non-SELECT queries, return an empty list
                    return []
                    
        except Exception as e:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                print(f"Error executing query, retrying ({retry_count}/{MAX_RETRIES}): {e}", file=sys.stderr)
                time.sleep(RETRY_DELAY * retry_count)  # Exponential backoff
            else:
                print(f"Failed after {MAX_RETRIES} attempts: {e}", file=sys.stderr)
                raise

def export_holocron_data():
    """Export holocron knowledge data using direct PostgreSQL connection with keyset pagination"""
    # Create output filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"holocron_export_{timestamp}.csv"
    
    # Try to increase statement timeout
    try:
        print("Setting extended statement timeout...")
        execute_query("SET statement_timeout = '300000';")  # 5 minutes
        print("Statement timeout set to 5 minutes.")
    except Exception as e:
        print(f"Warning: Could not set statement_timeout: {e}")
    
    # Check total records (just for reporting)
    try:
        print("Counting total records...")
        result = execute_query("SELECT COUNT(*) as count FROM holocron_knowledge;")
        total_records = result[0]['count']
        print(f"Total records to export: {total_records}")
    except Exception as e:
        print(f"Warning: Could not get total count: {e}")
        total_records = "unknown"
    
    # Track our progress for restart capability
    progress_file = f"export_progress_{timestamp}.json"
    
    # Open CSV file and write header
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'content', 'content_tokens', 'metadata'])
        
        # Use keyset pagination instead of OFFSET
        last_id = 0
        records_exported = 0
        
        print(f"Starting export with batch size {BATCH_SIZE}...")
        
        while True:
            try:
                # Using keyset pagination with WHERE id > last_id
                print(f"Fetching batch starting after ID {last_id}...")
                query = """
                SELECT 
                    id,
                    content,
                    content_tokens,
                    metadata
                FROM holocron_knowledge
                WHERE id > %s
                ORDER BY id
                LIMIT %s;
                """
                
                batch = execute_query(query, (last_id, BATCH_SIZE))
                
                if not batch or len(batch) == 0:
                    print("No more records to export.")
                    break
                
                batch_size = len(batch)
                for record in batch:
                    writer.writerow([
                        record['id'],
                        record['content'],
                        record['content_tokens'],
                        json.dumps(record['metadata']) if record['metadata'] else ''
                    ])
                
                records_exported += batch_size
                last_id = batch[-1]['id']
                
                # Save progress to allow for restart capability
                with open(progress_file, 'w') as progress:
                    json.dump({
                        'last_id': last_id,
                        'records_exported': records_exported,
                        'timestamp': datetime.now().isoformat()
                    }, progress)
                
                # Report progress and flush to disk to ensure data is saved
                print(f"Exported batch of {batch_size} records. Progress: {records_exported}/{total_records} records. Last ID: {last_id}")
                f.flush()
                
            except Exception as e:
                print(f"Error exporting batch after ID {last_id}: {e}", file=sys.stderr)
                # If we already have some data written, let's commit it to disk
                f.flush()
                # Wait before retrying
                time.sleep(RETRY_DELAY)
                continue
    
    print(f"\nExport completed! Exported {records_exported} records to {output_file}")
    
    # Clean up progress file when done
    try:
        os.remove(progress_file)
    except:
        pass
        
    return output_file

if __name__ == "__main__":
    try:
        print("Starting holocron export using direct PostgreSQL connection...")
        output_file = export_holocron_data()
        print(f"Successfully exported data to: {output_file}")
    except Exception as e:
        print(f"Export failed: {e}", file=sys.stderr)
        sys.exit(1) 