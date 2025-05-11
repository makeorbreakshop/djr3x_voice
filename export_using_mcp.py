#!/usr/bin/env python3
"""
Export Holocron Knowledge Table - Direct MCP Solution

This script helps you export the holocron_knowledge table using
direct MCP calls through the terminal, avoiding the need for 
the mcp_api_client package.
"""
import os
import sys
import time
from datetime import datetime

# Constants
PROJECT_ID = "xkotscjkvejcgrweolsd"
BATCH_SIZE = 500
OUTPUT_DIR = "holocron_export"

# Create output directory if it doesn't exist
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# Create a timestamp for filenames
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"{OUTPUT_DIR}/holocron_export_{timestamp}.csv"
batch_dir = f"{OUTPUT_DIR}/batches_{timestamp}"

# Create batch directory
if not os.path.exists(batch_dir):
    os.makedirs(batch_dir)

print("=" * 80)
print(f"HOLOCRON KNOWLEDGE EXPORT UTILITY")
print("=" * 80)
print(f"This script will help you export the data from the holocron_knowledge table.")
print(f"The export will be done in batches to avoid timeout issues.")
print()
print(f"Output file: {output_file}")
print(f"Batch files will be stored in: {batch_dir}")
print("=" * 80)
print("\nInstructions:")
print("1. You will need to run several MCP commands to export the data.")
print("2. Follow the prompts to execute each step.")
print("3. If any step fails, you can retry from that point.")
print("4. The final CSV will be assembled from the batch files.")
print("\nPress Enter to begin...")
input()

# Step 1: Set longer timeout
print("\nStep 1: Setting longer statement timeout")
print("Run this MCP command in your Claude chat:")
print("-" * 80)
print(f"mcp_supabase_execute_sql project_id={PROJECT_ID} query=\"SET statement_timeout = '300000';\"")
print("-" * 80)
print("After running the command, press Enter to continue...")
input()

# Step 2: Get total count
print("\nStep 2: Getting total record count")
print("Run this MCP command in your Claude chat:")
print("-" * 80)
print(f"mcp_supabase_execute_sql project_id={PROJECT_ID} query=\"SELECT COUNT(*) as count FROM holocron_knowledge;\"")
print("-" * 80)
print("Enter the total count shown in the result: ", end="")
try:
    total_count = int(input().strip())
    print(f"Total records to export: {total_count}")
except ValueError:
    print("Invalid number. Using estimate of 50,000 records.")
    total_count = 50000

# Step 3: Export batches
print("\nStep 3: Exporting data in batches")
last_id = 0
batch_num = 1
records_exported = 0

while records_exported < total_count:
    batch_file = f"{batch_dir}/batch_{batch_num:04d}.csv"
    
    print(f"\nBatch {batch_num}: Records after ID {last_id}")
    print("Run this MCP command in your Claude chat:")
    print("-" * 80)
    print(f"mcp_supabase_execute_sql project_id={PROJECT_ID} query=\"")
    print(f"COPY (")
    print(f"  SELECT id, content, content_tokens, metadata::text")
    print(f"  FROM holocron_knowledge")
    print(f"  WHERE id > {last_id}")
    print(f"  ORDER BY id")
    print(f"  LIMIT {BATCH_SIZE}")
    print(f") TO STDOUT WITH CSV HEADER;")
    print(f"\"")
    print("-" * 80)
    print(f"After running the command, save the results to: {batch_file}")
    print("Enter the highest ID in this batch (or 'skip' to move to the next step): ", end="")
    
    response = input().strip()
    if response.lower() == 'skip':
        print("Skipping to the next step...")
        break
        
    try:
        last_id = int(response)
        records_exported += BATCH_SIZE
        batch_num += 1
        
        progress = min(100, (records_exported / total_count) * 100)
        print(f"Progress: {records_exported}/{total_count} records ({progress:.1f}%)")
    except ValueError:
        print("Invalid ID. Please try again.")

# Step 4: Combine files
print("\nStep 4: Combining batch files into a single CSV")
print("You need to manually combine the batch files into a single CSV.")
print("On macOS/Linux, you can run:")
print("-" * 80)
print(f"head -1 {batch_dir}/batch_0001.csv > {output_file}")
print(f"for f in {batch_dir}/batch_*.csv; do")
print(f"  if [ \"$f\" != \"{batch_dir}/batch_0001.csv\" ]; then")
print(f"    tail -n +2 \"$f\" >> {output_file}")
print(f"  fi")
print(f"done")
print("-" * 80)
print("\nExport process complete! Follow the final step above to combine the files.")
print(f"The final output file will be: {output_file}") 