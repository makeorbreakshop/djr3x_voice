#!/usr/bin/env python3
import os
import json
import time
import glob
import datetime
from pathlib import Path

def count_jsonl_entries(filepath):
    """Count entries in a JSONL file"""
    try:
        with open(filepath, 'r') as f:
            return sum(1 for _ in f)
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return 0

def format_time(seconds):
    """Format seconds to days, hours, minutes, seconds"""
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds:.1f}s"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds:.1f}s"
    else:
        return f"{minutes}m {seconds:.1f}s"

def monitor_progress():
    """Monitor the progress of the E5 embedding migration"""
    checkpoint_file = "e5_migration_checkpoint.json"
    output_dir = "e5_vectors_full"
    total_vectors = 679826  # Total vectors in the source index
    
    print("\033c", end="")  # Clear screen
    print("E5 Embedding Migration Monitor")
    print("=" * 50)
    
    last_processed = 0
    last_check_time = time.time()
    start_time = time.time()
    
    while True:
        try:
            # Get processed count from checkpoint file
            processed_ids = []
            if os.path.exists(checkpoint_file):
                try:
                    with open(checkpoint_file, 'r') as f:
                        data = json.load(f)
                        processed_ids = data.get("processed_ids", [])
                        checkpoint_timestamp = data.get("timestamp", "Unknown")
                except json.JSONDecodeError:
                    print("Warning: Checkpoint file is corrupted or malformed JSON")
                    checkpoint_timestamp = "Unknown (corrupted file)"
            else:
                checkpoint_timestamp = "No checkpoint file found"
            
            processed_from_checkpoint = len(processed_ids)
            
            # Count vectors in output files
            output_files = glob.glob(f"{output_dir}/*.jsonl")
            vectors_in_files = sum(count_jsonl_entries(f) for f in output_files)
            
            # Calculate progress
            current_time = time.time()
            elapsed = current_time - start_time
            
            # Calculate processing rate
            rate_interval = current_time - last_check_time
            if rate_interval >= 10:  # Only update rate every 10 seconds
                processing_rate = (vectors_in_files - last_processed) / rate_interval
                last_processed = vectors_in_files
                last_check_time = current_time
            else:
                # Use overall average rate if we haven't hit the rate update interval
                processing_rate = vectors_in_files / elapsed if elapsed > 0 else 0
            
            # Estimate time remaining
            remaining_vectors = total_vectors - vectors_in_files
            if processing_rate > 0:
                time_remaining = remaining_vectors / processing_rate
                eta = datetime.datetime.now() + datetime.timedelta(seconds=time_remaining)
                eta_str = eta.strftime("%Y-%m-%d %H:%M:%S")
            else:
                time_remaining = 0
                eta_str = "Unknown (no progress detected)"
            
            # Clear screen and print stats
            print("\033c", end="")  # Clear screen
            print(f"E5 Embedding Migration Monitor - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 80)
            print(f"Last checkpoint update: {checkpoint_timestamp}")
            print(f"Total vectors to process: {total_vectors:,}")
            print(f"Vectors processed so far: {vectors_in_files:,} ({vectors_in_files/total_vectors*100:.2f}%)")
            print(f"Vectors remaining: {remaining_vectors:,}")
            print(f"Processed IDs in checkpoint: {processed_from_checkpoint:,}")
            print("-" * 80)
            print(f"Processing rate: {processing_rate:.2f} vectors/second")
            print(f"Elapsed time: {format_time(elapsed)}")
            print(f"Estimated time remaining: {format_time(time_remaining)}")
            print(f"Estimated completion: {eta_str}")
            print("=" * 80)
            print("Output files:")
            
            # List output files with counts
            for file_path in sorted(output_files):
                file_name = os.path.basename(file_path)
                count = count_jsonl_entries(file_path)
                file_size = os.path.getsize(file_path) / (1024 * 1024)  # Size in MB
                print(f"  {file_name}: {count:,} vectors, {file_size:.2f} MB")
            
            print("\nPress Ctrl+C to exit")
            
            # Sleep for a bit
            time.sleep(10)
            
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    monitor_progress() 