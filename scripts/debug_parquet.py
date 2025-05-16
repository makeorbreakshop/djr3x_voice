#!/usr/bin/env python3
"""
Debug script to check parquet files in the vectors_optimized directory
"""

import os
import sys
from pathlib import Path
import pandas as pd

def main():
    """Check files in the vectors_optimized directory"""
    vectors_dir = os.path.join(Path(__file__).parent.parent, "data", "vectors_optimized")
    
    # Get the first file
    files = sorted(list(Path(vectors_dir).glob("*.parquet")))
    if not files:
        print("No parquet files found!")
        return
    
    print(f"Found {len(files)} parquet files")
    
    # Try different methods to read the file
    first_file = files[0]
    print(f"\nTrying to read {first_file} using different methods:")
    
    # Method 1: Try with pyarrow
    try:
        print("\nMethod 1: Using pyarrow engine")
        df = pd.read_parquet(first_file, engine='pyarrow')
        print(f"Success! Read {len(df)} rows with columns: {df.columns.tolist()}")
        print(f"First few rows:\n{df.head(2)}")
    except Exception as e:
        print(f"Error with pyarrow: {e}")
    
    # Method 2: Try with fastparquet
    try:
        print("\nMethod 2: Using fastparquet engine")
        df = pd.read_parquet(first_file, engine='fastparquet')
        print(f"Success! Read {len(df)} rows with columns: {df.columns.tolist()}")
        print(f"First few rows:\n{df.head(2)}")
    except Exception as e:
        print(f"Error with fastparquet: {e}")
    
    # Method 3: Try using direct pandas with inferred engine
    try:
        print("\nMethod 3: Using pandas with inferred engine")
        df = pd.read_parquet(first_file)
        print(f"Success! Read {len(df)} rows with columns: {df.columns.tolist()}")
        print(f"First few rows:\n{df.head(2)}")
    except Exception as e:
        print(f"Error with pandas: {e}")
    
    # Try a variety of files to see if the issue is with specific files
    print("\n\nChecking multiple files:")
    for i, file_path in enumerate(files[:5]):  # Check first 5 files
        print(f"\nChecking file {i+1}: {file_path}")
        try:
            df = pd.read_parquet(file_path, engine='pyarrow')
            print(f"Success with pyarrow! Read {len(df)} rows")
        except Exception as e:
            print(f"Error with pyarrow: {e}")
            
            try:
                df = pd.read_parquet(file_path, engine='fastparquet')
                print(f"Success with fastparquet! Read {len(df)} rows")
            except Exception as e2:
                print(f"Error with fastparquet: {e2}")

if __name__ == "__main__":
    main() 