#!/usr/bin/env python3
"""
Check Vector Duplicates

This script checks for duplicate vector IDs across parquet files
in the data/vectors directory.
"""

import os
import sys
import pandas as pd
from pathlib import Path
from collections import Counter

def check_duplicates(directory='data/vectors', sample_size=20):
    """Check for duplicate vector IDs across parquet files."""
    print(f"Checking for duplicates in {directory}")
    
    # Get all parquet files
    parquet_files = list(Path(directory).glob('*.parquet'))
    total_files = len(parquet_files)
    print(f"Found {total_files} parquet files")
    
    # If there are too many files, take a sample
    if sample_size and total_files > sample_size:
        print(f"Sampling {sample_size} files")
        import random
        parquet_files = random.sample(parquet_files, sample_size)
    
    # Track all IDs and their counts
    all_ids = Counter()
    files_processed = 0
    total_rows = 0
    
    # Process each file
    for parquet_file in parquet_files:
        try:
            df = pd.read_parquet(parquet_file)
            ids = list(df['id'])
            all_ids.update(ids)
            
            files_processed += 1
            total_rows += len(df)
            
            # Print progress
            if files_processed % 5 == 0:
                print(f"Processed {files_processed}/{len(parquet_files)} files, {total_rows} vectors")
        except Exception as e:
            print(f"Error processing {parquet_file}: {e}")
    
    # Find duplicates
    duplicates = {id: count for id, count in all_ids.items() if count > 1}
    
    # Print results
    print("\n=== Results ===")
    print(f"Total files processed: {files_processed}")
    print(f"Total vectors processed: {total_rows}")
    print(f"Unique vector IDs: {len(all_ids)}")
    print(f"Duplicate vector IDs: {len(duplicates)}")
    
    # Print some example duplicates
    if duplicates:
        print("\nExample duplicates:")
        for id, count in list(duplicates.items())[:10]:
            print(f"  {id}: {count} occurrences")
    else:
        print("\nNo duplicates found!")
    
    # Calculate duplication percentage
    if total_rows > 0:
        # Calculate how many duplicate entries exist (total occurrences minus unique IDs)
        total_duplicate_occurrences = sum(count - 1 for count in duplicates.values())
        duplicate_percentage = total_duplicate_occurrences / total_rows * 100
        print(f"\nDuplication percentage: {duplicate_percentage:.2f}%")
    
    return duplicates

if __name__ == "__main__":
    directory = 'data/vectors'
    sample_size = 20  # Set to None to process all files
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        directory = sys.argv[1]
    if len(sys.argv) > 2:
        sample_size = int(sys.argv[2]) if sys.argv[2] != 'all' else None
    
    check_duplicates(directory, sample_size) 