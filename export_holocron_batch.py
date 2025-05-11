#!/usr/bin/env python3
"""
Export Holocron Knowledge Table using Direct PostgreSQL Connection

This script uses a direct PostgreSQL connection to export data from
the holocron_knowledge table to a CSV file, avoiding timeout issues
with API access.
"""
import sys
import os
import csv
import time
from datetime import datetime

# Supabase Project Settings
PROJECT_ID = "xkotscjkvejcgrweolsd"
DB_HOST = f"db.{PROJECT_ID}.supabase.co"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PORT = "5432"

# Set your database password here or in environment
DB_PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD")
if not DB_PASSWORD:
    DB_PASSWORD = input("Enter your Supabase database password: ")

# Create output file with timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"holocron_export_{timestamp}.csv"

# Construct connection string (for direct connection to PostgreSQL)
connection_string = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def export_with_python():
    """Export using Python's psycopg2 library"""
    try:
        import psycopg2
    except ImportError:
        print("Error: psycopg2 module not found. Install with 'pip install psycopg2-binary'")
        return False
    
    print(f"Exporting data to {output_file} using psycopg2...")
    
    try:
        # Connect to the database
        conn = psycopg2.connect(connection_string)
        
        # Set a longer statement timeout (5 minutes)
        with conn.cursor() as cursor:
            cursor.execute("SET statement_timeout = '300000';")  # 5 minutes
            
            # Get total count for progress reporting
            cursor.execute("SELECT COUNT(*) FROM holocron_knowledge;")
            total_records = cursor.fetchone()[0]
            print(f"Found {total_records} records to export")
        
        # Open CSV file and write header
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'content', 'content_tokens', 'metadata', 'embedding'])
            
            # Use batched fetching instead of server-side cursor
            # This avoids transaction block requirements
            batch_size = 500
            records_exported = 0
            last_id = 0
            
            while True:
                with conn.cursor() as cursor:
                    # Use keyset pagination (WHERE id > last_id) instead of OFFSET
                    # Include embedding vector data
                    cursor.execute(f"""
                        SELECT id, content, content_tokens, metadata::text as metadata, embedding::text
                        FROM holocron_knowledge
                        WHERE id > %s
                        ORDER BY id
                        LIMIT %s;
                    """, (last_id, batch_size))
                    
                    batch = cursor.fetchall()
                    
                    if not batch:
                        break
                    
                    # Write batch to CSV
                    for record in batch:
                        writer.writerow(record)
                    
                    # Update last_id for next iteration
                    last_id = batch[-1][0]
                    
                    records_exported += len(batch)
                    
                    # Report progress
                    progress = (records_exported / total_records) * 100
                    print(f"Exported {records_exported}/{total_records} records ({progress:.1f}%)")
                    
                    # Flush to disk to ensure data is saved
                    f.flush()
                    
                    # Small pause to avoid overwhelming the connection
                    time.sleep(0.5)
        
        # Close connection
        conn.close()
        
        print(f"\nExport completed successfully!")
        print(f"Exported {records_exported} records to {output_file}")
        return True
        
    except Exception as e:
        print(f"Error exporting data: {e}", file=sys.stderr)
        return False

def main():
    """Main function to run the export"""
    print("Starting export of holocron_knowledge table...")
    
    # Try the Python method directly since psql/pg_dump aren't available
    try:
        if not export_with_python():
            print("Export failed.")
            return 1
    except KeyboardInterrupt:
        print("\nExport interrupted by user.")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 