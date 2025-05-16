#!/usr/bin/env python3
"""
Wrapper script for migrate_to_e5_embeddings.py that filters out progress bars
and presents a clean, summarized output.
"""

import subprocess
import sys
import re
import time
import argparse
from datetime import datetime

def parse_args():
    parser = argparse.ArgumentParser(description="Run E5 migration with clean output")
    parser.add_argument("--api-key", type=str, help="Pinecone API key")
    parser.add_argument("--source-index", type=str, default="holocron-knowledge", help="Source index name")
    parser.add_argument("--target-index", type=str, default="holocron-sbert-e5", help="Target index name")
    parser.add_argument("--source-namespace", type=str, default="", help="Source namespace")
    parser.add_argument("--target-namespace", type=str, default="", help="Target namespace")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for processing")
    parser.add_argument("--model-name", type=str, default="intfloat/e5-small-v2", help="E5 model name")
    parser.add_argument("--limit", type=int, help="Limit number of vectors to process")
    parser.add_argument("--num-workers", type=int, default=4, help="Number of worker processes")
    parser.add_argument("--reset", action="store_true", help="Reset checkpoint file and process all vectors again")
    parser.add_argument("--debug", action="store_true", help="Show full debug output from the migration script")
    return parser.parse_args()

def main():
    args = parse_args()
    
    # Build command
    cmd = ["python", "scripts/migrate_to_e5_embeddings.py"]
    
    if not args.debug:
        cmd.append("--summary-only")
    
    # Add arguments
    if args.api_key:
        cmd.extend(["--api-key", args.api_key])
    if args.source_index:
        cmd.extend(["--source-index", args.source_index])
    if args.target_index:
        cmd.extend(["--target-index", args.target_index])
    if args.source_namespace:
        cmd.extend(["--source-namespace", args.source_namespace])
    if args.target_namespace:
        cmd.extend(["--target-namespace", args.target_namespace])
    if args.batch_size:
        cmd.extend(["--batch-size", str(args.batch_size)])
    if args.model_name:
        cmd.extend(["--model-name", args.model_name])
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.num_workers:
        cmd.extend(["--num-workers", str(args.num_workers)])
    if args.reset:
        cmd.append("--reset")
        
    # Print run info
    print(f"\n{'='*80}")
    print(f"STARTING E5 MIGRATION: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    print(f"Source index: {args.source_index}")
    print(f"Target index: {args.target_index}")
    if args.limit:
        print(f"Limit: {args.limit} vectors")
    print(f"Workers: {args.num_workers}")
    if args.reset:
        print("RESET MODE: Clearing checkpoint and processing all vectors")
    print(f"{'='*80}\n")
    
    # Run the process and capture output
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )
    
    # Counters for statistics
    start_time = time.time()
    batch_count = 0
    total_processed = 0
    last_status_time = time.time()
    
    # Patterns to match
    batch_pattern = re.compile(r"Batch (\d+): Processed (\d+) vectors")
    processed_pattern = re.compile(r"Cumulative processed: (\d+)")
    completion_pattern = re.compile(r"Migration finished. Total unique IDs processed: (\d+)")
    fetch_pattern = re.compile(r"Batch \d+: Fetched (\d+) vectors")
    processing_batch_pattern = re.compile(r"Batch (\d+): Found (\d+) IDs from Pinecone")
    
    # Define color codes for better visual indicators
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    BOLD = '\033[1m'
    RESET = '\033[0m'
    
    def timestamp():
        return f"{GREEN}[{datetime.now().strftime('%H:%M:%S')}]{RESET}"
    
    print("Migration running... live statistics:")
    print(f"{'='*50}")
    
    # Session counters
    session_vectors_processed = 0
    last_printed_total = 0
    session_start = time.time()
    active_migration = False
    
    try:
        # Process output line by line
        for line in iter(process.stdout.readline, ''):
            # In debug mode, print all lines
            if args.debug:
                print(line.strip())
                continue
                
            # Skip lines with progress bars
            if "it/s]" in line or ("|" in line and "%" in line):
                continue
            
            # Always show certain important log lines immediately 
            if any(important in line for important in [
                "Error", "WARNING", "Failed", "Exception", "Could not", 
                "Checkpoint reset", "Loaded", "Sample", "empty", "skipping"
            ]):
                print(f"{timestamp()} {YELLOW}{line.strip()}{RESET}")
                continue
                
            # Process informative lines
            if "Found" in line and "IDs from Pinecone" in line:
                match = processing_batch_pattern.search(line)
                if match:
                    batch_num = match.group(1)
                    id_count = match.group(2)
                    active_migration = True
                    print(f"{timestamp()} {BLUE}▶ Processing batch {batch_num} with {id_count} IDs{RESET}")
                
            elif "Fetched" in line:
                match = fetch_pattern.search(line)
                if match:
                    fetched = int(match.group(1))
                    active_migration = True
                    print(f"{timestamp()} {GREEN}✓ Fetched {fetched} vectors for processing{RESET}")
                    
            elif "Batch" in line and "Processed" in line:
                match = batch_pattern.search(line)
                if match:
                    batch_count = int(match.group(1))
                    batch_processed = int(match.group(2))
                    session_vectors_processed += batch_processed
                    active_migration = True
                    
                    # Always print batch completions
                    print(f"{timestamp()} {GREEN}{BOLD}✓ COMPLETED batch {batch_count}: {batch_processed} vectors processed!{RESET}")
                    
                    # Show rate every 5 seconds
                    if time.time() - last_status_time > 5:
                        elapsed = time.time() - session_start
                        rate = session_vectors_processed / elapsed if elapsed > 0 else 0
                        print(f"{timestamp()}   → Session total: {session_vectors_processed} vectors")
                        print(f"{timestamp()}   → Processing rate: {rate:.2f} vectors/sec")
                        last_status_time = time.time()
            
            elif "Cumulative processed:" in line:
                match = processed_pattern.search(line)
                if match:
                    total_processed = int(match.group(1))
                    # Only print if the total has changed
                    if total_processed != last_printed_total:
                        print(f"{timestamp()} {BLUE}● Total vectors processed (all time): {BOLD}{total_processed}{RESET}")
                        last_printed_total = total_processed
                    
            elif "Saved checkpoint" in line:
                print(f"{timestamp()} {GREEN}✓ Checkpoint saved{RESET}")
                    
            elif "Migration finished" in line:
                match = completion_pattern.search(line)
                if match:
                    final_count = int(match.group(1))
                    print(f"\n{GREEN}{'='*50}{RESET}")
                    print(f"{GREEN}{BOLD}MIGRATION COMPLETE: {final_count} vectors processed{RESET}")
                    print(f"{GREEN}{'='*50}{RESET}")
                    
            # Show a heartbeat message if nothing's been printed for a while
            elif time.time() - last_status_time > 15:
                if active_migration:
                    print(f"{timestamp()} {YELLOW}● Still running... ({session_vectors_processed} vectors processed so far){RESET}")
                else:
                    print(f"{timestamp()} {YELLOW}● Waiting for Pinecone to return data...{RESET}")
                last_status_time = time.time()
                
    except KeyboardInterrupt:
        print("\nMigration interrupted by user")
        process.terminate()
        
    # Calculate final stats
    total_time = time.time() - start_time
    hours, remainder = divmod(total_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    print(f"\nTotal runtime: {int(hours)}h {int(minutes)}m {seconds:.2f}s")
    
if __name__ == "__main__":
    main() 