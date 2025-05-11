#!/usr/bin/env python3
"""
Holocron Knowledge Export Script using Cursor-based approach with MCP

This script exports data from the holocron_knowledge table using MCP
and a cursor-based approach to avoid timeouts.
"""
import csv
import os
import sys
import time
import json
from datetime import datetime
from pathlib import Path

# Constants
PROJECT_ID = "xkotscjkvejcgrweolsd"
BATCH_SIZE = 500
DATA_DIR = Path("./holocron_export_data")

# Create output directory if it doesn't exist
DATA_DIR.mkdir(exist_ok=True)

# Create output filename with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"holocron_export_{timestamp}.csv"

print(f"Exporting holocron_knowledge table to {output_file}...")

try:
    # Import the MCP API client
    from mcp_api_client.main import call_tool
    
    # Set longer statement timeout
    print("Setting statement timeout to 5 minutes...")
    call_tool('mcp_supabase_execute_sql', {
        'project_id': PROJECT_ID,
        'query': "SET statement_timeout = '300000';"
    })
    
    # Get total count for progress reporting
    print("Getting total record count...")
    result = call_tool('mcp_supabase_execute_sql', {
        'project_id': PROJECT_ID,
        'query': "SELECT COUNT(*) as count FROM holocron_knowledge;"
    })
    total_records = json.loads(result)[0]['count']
    print(f"Found {total_records} records to export")
    
    # Open output file
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'content', 'content_tokens', 'metadata'])
        
        # Use cursor-based pagination
        last_id = 0
        records_exported = 0
        
        # Continue fetching batches until we've processed all records
        while True:
            print(f"Fetching records with ID > {last_id}...")
            
            # Get a batch of records
            result = call_tool('mcp_supabase_execute_sql', {
                'project_id': PROJECT_ID,
                'query': f"""
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
            })
            
            batch = json.loads(result)
            
            # If no more records, we're done
            if not batch or len(batch) == 0:
                break
                
            # Write records to CSV
            for record in batch:
                writer.writerow([
                    record['id'],
                    record['content'],
                    record['content_tokens'],
                    record['metadata']
                ])
            
            # Update counters
            records_exported += len(batch)
            last_id = batch[-1]['id']
            
            # Report progress
            progress = (records_exported / total_records) * 100
            print(f"Exported {records_exported}/{total_records} records ({progress:.1f}%)")
            
            # Flush to disk to ensure data is saved
            f.flush()
            
            # Small delay to avoid overwhelming the API
            time.sleep(0.5)
    
    print(f"\nExport completed successfully!")
    print(f"Exported {records_exported} records to {output_file}")
    
except Exception as e:
    print(f"Error during export: {str(e)}", file=sys.stderr)
    sys.exit(1) 