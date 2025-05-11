#!/usr/bin/env python3
"""
MCP Holocron Knowledge Export Script

This script uses MCP Supabase tools to export data from the
holocron_knowledge table with optimized performance.
"""
import csv
import sys
import json
from datetime import datetime

# Constants
PROJECT_ID = "xkotscjkvejcgrweolsd"
BATCH_SIZE = 500  # Increased batch size for better performance

def execute_sql(query):
    """Execute SQL query using MCP Supabase tools"""
    from mcp_api_client.main import call_tool
    result = call_tool('mcp_supabase_execute_sql', {
        'project_id': PROJECT_ID,
        'query': query
    })
    return json.loads(result)

def export_holocron_data():
    """Export holocron knowledge data using keyset pagination and optimized queries"""
    # Create output filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"holocron_export_{timestamp}.csv"
    
    # Try to increase statement timeout
    print("Setting extended statement timeout...")
    try:
        execute_sql("SET statement_timeout = '300000';")  # 5 minutes
        print("Statement timeout set to 5 minutes.")
    except Exception as e:
        print(f"Warning: Could not set statement_timeout: {e}")
    
    # Get total count (just for reporting)
    try:
        print("Counting total records...")
        result = execute_sql("SELECT COUNT(*) as count FROM holocron_knowledge;")
        total_records = result[0]['count'] if result and len(result) > 0 else "unknown"
        print(f"Total records to export: {total_records}")
    except Exception as e:
        print(f"Warning: Could not get total count: {e}")
        total_records = "unknown"
    
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
                query = f"""
                SELECT 
                    id,
                    content,
                    content_tokens,
                    metadata::text as metadata
                FROM holocron_knowledge
                WHERE id > {last_id}
                ORDER BY id
                LIMIT {BATCH_SIZE};
                """
                
                batch = execute_sql(query)
                
                if not batch or len(batch) == 0:
                    print("No more records to export.")
                    break
                
                batch_size = len(batch)
                for record in batch:
                    writer.writerow([
                        record['id'],
                        record['content'],
                        record['content_tokens'],
                        record['metadata']
                    ])
                
                records_exported += batch_size
                last_id = batch[-1]['id']
                
                # Report progress and flush to disk to ensure data is saved
                print(f"Exported batch of {batch_size} records. Progress: {records_exported}/{total_records} records. Last ID: {last_id}")
                f.flush()
                
            except Exception as e:
                print(f"Error exporting batch after ID {last_id}: {e}", file=sys.stderr)
                # If we already have some data written, let's commit it to disk
                f.flush()
                # Wait before retrying
                import time
                time.sleep(5)
                continue
    
    print(f"\nExport completed! Exported {records_exported} records to {output_file}")
    return output_file

if __name__ == "__main__":
    try:
        print("Starting holocron export using MCP tools...")
        output_file = export_holocron_data()
        print(f"Successfully exported data to: {output_file}")
    except Exception as e:
        print(f"Export failed: {e}", file=sys.stderr)
        sys.exit(1) 