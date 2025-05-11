#!/usr/bin/env python3
import json
from datetime import datetime

# Create output filename with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"holocron_export_{timestamp}.json"

# Initialize the output file with an empty array
with open(output_file, 'w') as f:
    f.write('[\n')

# Keep track of whether we've written any records
first_record = True

# Process in batches of 100
batch_size = 100
offset = 0

while True:
    print(f"Fetching records starting at offset {offset}")
    
    # Fetch a batch of records
    query = f"""
    SELECT id, content, content_tokens, metadata
    FROM holocron_knowledge
    ORDER BY id
    LIMIT {batch_size}
    OFFSET {offset};
    """
    
    # Execute query using MCP Supabase connection
    # This will be filled in with the actual execution
    
    # If no more records, break
    if not records:
        break
        
    # Write records to file
    with open(output_file, 'a') as f:
        for record in records:
            if not first_record:
                f.write(',\n')
            json.dump(record, f, indent=2)
            first_record = False
    
    # Move to next batch
    offset += batch_size
    print(f"Processed {offset} records so far")

# Close the JSON array
with open(output_file, 'a') as f:
    f.write('\n]')

print(f"Export completed to {output_file}") 