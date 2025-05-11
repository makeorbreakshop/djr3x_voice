#!/usr/bin/env python3
"""
Optimized Holocron Knowledge Export Script

This script exports data from the holocron_knowledge table using
keyset pagination and other optimizations to avoid timeouts.

Key improvements over the original script:
1. Uses keyset pagination (WHERE id > last_id) instead of OFFSET
2. Increases statement timeout
3. Uses larger batch sizes
4. Implements retry logic
5. Flushes data to disk regularly to avoid data loss
"""
import csv
import sys
import time
import json
import os
from datetime import datetime
from supabase import create_client, Client

# Constants
BATCH_SIZE = 500  # Increased batch size for better performance
MAX_RETRIES = 3   # Maximum number of retries on failure
RETRY_DELAY = 5   # Seconds to wait between retries

# Load env vars if using dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Get Supabase credentials from environment variables
import os
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Check if credentials are available
if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Supabase credentials not found. Please set SUPABASE_URL and SUPABASE_KEY environment variables.")
    sys.exit(1)

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def execute_sql(query):
    """
    Execute SQL query against Supabase with retry logic
    
    The retry logic helps overcome temporary connectivity issues
    or brief Supabase service interruptions.
    """
    retry_count = 0
    
    while retry_count < MAX_RETRIES:
        try:
            # Use the Supabase client to execute the query
            result = supabase.rpc('exec_sql', {'sql_statement': query}).execute()
            
            # Check for errors
            if result.error:
                raise Exception(f"Supabase error: {result.error.message}")
            
            return result.data
        except Exception as e:
            retry_count += 1
            if retry_count < MAX_RETRIES:
                print(f"Error executing query, retrying ({retry_count}/{MAX_RETRIES}): {e}", file=sys.stderr)
                time.sleep(RETRY_DELAY * retry_count)  # Exponential backoff
            else:
                print(f"Failed after {MAX_RETRIES} attempts: {e}", file=sys.stderr)
                raise

def export_holocron_data():
    """
    Export holocron knowledge data using efficient keyset pagination
    
    Keyset pagination maintains consistent performance regardless of table size
    by using 'WHERE id > last_id' instead of OFFSET which gets slower as the
    offset increases.
    """
    # Create output filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"holocron_export_{timestamp}.csv"
    
    # Try to increase statement timeout - this helps prevent query timeouts
    # for large tables or slow connections
    try:
        print("Setting extended statement timeout...")
        # Create RPC function to set statement timeout if it doesn't exist
        supabase.rpc('exec_sql', {
            'sql_statement': """
            CREATE OR REPLACE FUNCTION set_statement_timeout()
            RETURNS void AS $$
            BEGIN
                SET statement_timeout = '300000';
            END;
            $$ LANGUAGE plpgsql;
            """
        }).execute()
        
        # Call the function to set the timeout
        supabase.rpc('set_statement_timeout').execute()
        print("Statement timeout set to 5 minutes.")
    except Exception as e:
        print(f"Warning: Could not set statement_timeout: {e}")
    
    # Check total records (just for reporting)
    try:
        print("Counting total records...")
        result = supabase.table('holocron_knowledge').select('count', count='exact').execute()
        total_records = result.count
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
                # This is much more efficient than OFFSET for large datasets
                print(f"Fetching batch starting after ID {last_id}...")
                result = supabase.table('holocron_knowledge') \
                    .select('id,content,content_tokens,metadata') \
                    .gt('id', last_id) \
                    .order('id') \
                    .limit(BATCH_SIZE) \
                    .execute()
                
                batch = result.data
                
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
        output_file = export_holocron_data()
        print(f"Successfully exported data to: {output_file}")
    except Exception as e:
        print(f"Export failed: {e}", file=sys.stderr)
        sys.exit(1) 