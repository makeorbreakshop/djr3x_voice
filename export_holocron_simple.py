#!/usr/bin/env python3
import csv
import sys
from datetime import datetime
import json

# Constants
PROJECT_ID = "xkotscjkvejcgrweolsd"
BATCH_SIZE = 50

def execute_sql(query):
    from mcp_api_client.main import call_tool
    result = call_tool('mcp_supabase_execute_sql', {
        'project_id': PROJECT_ID,
        'query': query
    })
    return json.loads(result)

# Create output filename with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"holocron_export_{timestamp}.csv"

# Get total count
result = execute_sql("SELECT COUNT(*) as count FROM holocron_knowledge;")
total_records = result[0]['count']
print(f"Total records to export: {total_records}")

# Open CSV file and write header
with open(output_file, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['id', 'content', 'content_tokens', 'metadata'])

    # Process in batches
    for offset in range(0, total_records, BATCH_SIZE):
        print(f"Exporting records {offset + 1} to {min(offset + BATCH_SIZE, total_records)}")
        
        query = f"""
        SELECT 
            id,
            content,
            content_tokens,
            metadata::text as metadata
        FROM holocron_knowledge
        ORDER BY id
        LIMIT {BATCH_SIZE}
        OFFSET {offset};
        """
        
        try:
            batch = execute_sql(query)
            for record in batch:
                writer.writerow([
                    record['id'],
                    record['content'],
                    record['content_tokens'],
                    record['metadata']
                ])
        except Exception as e:
            print(f"Error exporting batch at offset {offset}: {e}", file=sys.stderr)
            continue

print(f"\nExport completed! Data saved to {output_file}") 