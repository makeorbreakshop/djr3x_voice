#!/usr/bin/env python
import os
import sys
import time
import glob
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import psutil

def find_vector_process():
    """Find the vector creation process if running"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        if proc.info['cmdline'] and len(proc.info['cmdline']) > 1:
            cmdline = ' '.join(proc.info['cmdline'])
            if 'create_vectors_robust.py' in cmdline:
                return proc
    return None

def count_processed_articles():
    """Count the number of processed articles from status CSV"""
    csv_path = "data/processing_status.csv"
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            return len(df)
        except Exception as e:
            print(f"Error reading processing status: {e}")
    return 0

def count_vectors():
    """Count the total number of vectors in parquet files"""
    total_vectors = 0
    try:
        for parquet_file in Path("data/vectors").glob("*.parquet"):
            df = pd.read_parquet(parquet_file)
            total_vectors += len(df)
    except Exception as e:
        print(f"Error counting vectors: {e}")
    return total_vectors

def count_parquet_files():
    """Count the number of parquet files"""
    return len(list(Path("data/vectors").glob("*.parquet")))

def count_json_files():
    """Count the number of JSON files in processed_articles"""
    return len(list(Path("data/processed_articles").rglob("*.json")))

def get_file_stats():
    """Get creation times of vector files to analyze processing rate"""
    files = []
    for parquet_file in Path("data/vectors").glob("*.parquet"):
        ctime = os.path.getctime(parquet_file)
        mtime = os.path.getmtime(parquet_file)
        size = os.path.getsize(parquet_file)
        files.append({
            'path': str(parquet_file),
            'ctime': ctime,
            'mtime': mtime,
            'size': size,
            'datetime': datetime.fromtimestamp(mtime)
        })
    return sorted(files, key=lambda x: x['ctime'])

def estimate_completion(files, total_articles):
    """Estimate completion time based on recent processing rate"""
    if len(files) < 2:
        return "Not enough data for estimation"
    
    # Look at the last 10 files or all if fewer
    recent_files = files[-min(10, len(files)):]
    
    # Get time deltas between file creations
    time_deltas = []
    for i in range(1, len(recent_files)):
        delta = recent_files[i]['datetime'] - recent_files[i-1]['datetime']
        time_deltas.append(delta.total_seconds())
    
    if not time_deltas:
        return "Not enough data for estimation"
    
    # Average time between files
    avg_seconds_per_file = sum(time_deltas) / len(time_deltas)
    
    # Estimate vectors per file
    if len(recent_files) > 0:
        try:
            sample_file = recent_files[-1]['path']
            df = pd.read_parquet(sample_file)
            vectors_per_file = len(df)
        except:
            vectors_per_file = 25  # Assume a reasonable default
    else:
        vectors_per_file = 25
    
    processed_articles = count_processed_articles()
    remaining_articles = total_articles - processed_articles
    
    # Estimate vectors per article
    vectors_per_article = 1.5  # Rough estimate, articles typically create 1-2 vectors
    
    # Estimate remaining files
    remaining_vectors = remaining_articles * vectors_per_article
    remaining_files = remaining_vectors / vectors_per_file
    
    # Calculate time remaining
    seconds_remaining = remaining_files * avg_seconds_per_file
    completion_time = datetime.now() + timedelta(seconds=seconds_remaining)
    
    return {
        "time_per_file_seconds": avg_seconds_per_file,
        "vectors_per_file": vectors_per_file,
        "files_per_hour": 3600 / avg_seconds_per_file,
        "processed_articles": processed_articles,
        "remaining_articles": remaining_articles,
        "estimated_completion": completion_time.strftime("%Y-%m-%d %H:%M:%S"),
        "estimated_hours_remaining": seconds_remaining / 3600
    }

def monitor(interval=60):
    """Monitor the vector creation process"""
    print(f"Starting vector creation monitor (checking every {interval} seconds)")
    
    # Initial counts
    total_json_files = count_json_files()
    print(f"Total JSON files to process: {total_json_files}")
    
    last_files_count = -1
    last_vectors_count = -1
    
    while True:
        proc = find_vector_process()
        if not proc:
            print("No vector creation process found running.")
            if last_files_count != -1:
                print("Process appears to have stopped. Final stats:")
                # Show final stats
                parquet_files = count_parquet_files()
                total_vectors = count_vectors()
                processed_articles = count_processed_articles()
                
                print(f"Parquet files created: {parquet_files}")
                print(f"Total vectors created: {total_vectors}")
                print(f"Articles processed: {processed_articles}/{total_json_files} ({processed_articles/total_json_files*100:.2f}%)")
                break
        else:
            print(f"\n--- Vector Creation Monitor: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
            
            # Get current counts
            parquet_files = count_parquet_files()
            total_vectors = count_vectors()
            processed_articles = count_processed_articles()
            
            # Calculate rates if we have previous values
            if last_files_count != -1:
                new_files = parquet_files - last_files_count
                new_vectors = total_vectors - last_vectors_count
                print(f"New files in last {interval}s: {new_files} ({new_files * (3600/interval):.1f}/hour)")
                print(f"New vectors in last {interval}s: {new_vectors} ({new_vectors * (3600/interval):.1f}/hour)")
            
            # Update last counts
            last_files_count = parquet_files
            last_vectors_count = total_vectors
            
            print(f"Parquet files created: {parquet_files}")
            print(f"Total vectors created: {total_vectors}")
            print(f"Articles processed: {processed_articles}/{total_json_files} ({processed_articles/total_json_files*100:.2f}%)")
            
            # Get estimates
            files = get_file_stats()
            if files:
                est = estimate_completion(files, total_json_files)
                if isinstance(est, dict):
                    print("\nCompletion Estimates:")
                    print(f"Processing rate: {est['files_per_hour']:.1f} files/hour (~{est['vectors_per_file'] * est['files_per_hour']:.1f} vectors/hour)")
                    print(f"Estimated completion: {est['estimated_completion']} (in {est['estimated_hours_remaining']:.1f} hours)")
                else:
                    print(f"\nCompletion estimate: {est}")
            
            print(f"Process: PID {proc.pid}, running for {(time.time() - proc.create_time())/60:.1f} minutes")
            try:
                mem = proc.memory_info()
                cpu = proc.cpu_percent(interval=1)
                print(f"Memory: {mem.rss / (1024*1024):.1f} MB, CPU: {cpu}%")
            except:
                pass
        
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
            break

if __name__ == "__main__":
    interval = 60
    if len(sys.argv) > 1:
        try:
            interval = int(sys.argv[1])
        except:
            pass
    monitor(interval) 