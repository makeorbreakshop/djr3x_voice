#!/usr/bin/env python3
"""
Holocron Knowledge Export Script using MCP directly

This script retrieves data from the holocron_knowledge table using keyset pagination
and exports it to a CSV file. It uses the MCP Supabase tools directly to avoid
connection issues and latency.
"""
import sys
import csv
import json
import time
from datetime import datetime
import os

# Constants
PROJECT_ID = "xkotscjkvejcgrweolsd"
BATCH_SIZE = 500  # Batch size for efficient exports

# Create output filename with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"holocron_export_{timestamp}.csv"

# Open CSV file and write header
with open(output_file, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['id', 'content', 'content_tokens', 'metadata'])
    
    print("Setting extended statement timeout...")
    
    # Direct MCP call to increase statement timeout
    from mcp_api_client.main import call_tool
    result = call_tool('mcp_supabase_execute_sql', {
        'project_id': PROJECT_ID,
        'query': "SET statement_timeout = '300000';"  # 5 minutes
    })
    
    # Count total records
    print("Counting total records...")
    result = call_tool('mcp_supabase_execute_sql', {
        'project_id': PROJECT_ID,
        'query': "SELECT COUNT(*) as count FROM holocron_knowledge;"
    })
    count_data = json.loads(result)
    total_records = count_data[0]['count']
    print(f"Total records to export: {total_records}")
    
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
            
            result = call_tool('mcp_supabase_execute_sql', {
                'project_id': PROJECT_ID,
                'query': query
            })
            
            batch = json.loads(result)
            
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
            
            # Small pause to avoid overloading the API
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error exporting batch after ID {last_id}: {e}", file=sys.stderr)
            # If we already have some data written, let's commit it to disk
            f.flush()
            # Wait before retrying
            time.sleep(5)
            continue

print(f"\nExport completed! Exported {records_exported} records to {output_file}") 