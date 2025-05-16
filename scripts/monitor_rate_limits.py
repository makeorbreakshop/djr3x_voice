#!/usr/bin/env python3
"""
Monitor Rate Limit script

Analyzes log files to monitor OpenAI API rate limit hits and provides statistics.
"""

import os
import re
import sys
import time
import glob
from collections import defaultdict, Counter
from datetime import datetime, timedelta
import argparse

def parse_log_file(log_file, last_n_minutes=None):
    """Parse the log file for rate limit hits and successful requests."""
    rate_limit_pattern = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - .*Rate limit hit')
    success_pattern = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - .*HTTP Request:.* "HTTP/1\.1 200 OK"')
    
    # Initialize counters
    rate_limit_hits = 0
    successful_requests = 0
    rate_limit_by_minute = defaultdict(int)
    success_by_minute = defaultdict(int)
    
    current_time = datetime.now()
    cutoff_time = None
    if last_n_minutes:
        cutoff_time = current_time - timedelta(minutes=last_n_minutes)
    
    with open(log_file, 'r') as f:
        for line in f:
            # Check for rate limit hits
            match = rate_limit_pattern.search(line)
            if match:
                timestamp_str = match.group(1)
                timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                
                if not cutoff_time or timestamp >= cutoff_time:
                    rate_limit_hits += 1
                    minute_key = timestamp.strftime('%Y-%m-%d %H:%M')
                    rate_limit_by_minute[minute_key] += 1
            
            # Check for successful requests
            match = success_pattern.search(line)
            if match:
                timestamp_str = match.group(1)
                timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                
                if not cutoff_time or timestamp >= cutoff_time:
                    successful_requests += 1
                    minute_key = timestamp.strftime('%Y-%m-%d %H:%M')
                    success_by_minute[minute_key] += 1
    
    return {
        'rate_limit_hits': rate_limit_hits,
        'successful_requests': successful_requests,
        'rate_limit_by_minute': rate_limit_by_minute,
        'success_by_minute': success_by_minute
    }

def find_latest_log():
    """Find the latest log file in the logs directory."""
    log_files = glob.glob('logs/vector_creation_*.log')
    if not log_files:
        return None
    
    # Sort by modification time (most recent first)
    log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return log_files[0]

def monitor_live(log_file, interval=5):
    """Monitor a log file in real-time, updating statistics."""
    print(f"Monitoring {log_file} for rate limits. Press Ctrl+C to stop.")
    
    try:
        while True:
            # Parse last 5 minutes of logs
            stats = parse_log_file(log_file, last_n_minutes=5)
            
            # Clear screen
            os.system('cls' if os.name == 'nt' else 'clear')
            
            # Print statistics
            success_rate = 0
            if stats['successful_requests'] + stats['rate_limit_hits'] > 0:
                success_rate = stats['successful_requests'] / (stats['successful_requests'] + stats['rate_limit_hits']) * 100
            
            print(f"=== OpenAI API Rate Limit Monitor (Last 5 minutes) ===")
            print(f"Log file: {log_file}")
            print(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"\nSuccess rate: {success_rate:.2f}%")
            print(f"Successful requests: {stats['successful_requests']}")
            print(f"Rate limit hits: {stats['rate_limit_hits']}")
            
            print("\nRate limit hits by minute:")
            minutes = sorted(list(set(stats['rate_limit_by_minute'].keys()) | set(stats['success_by_minute'].keys())))
            for minute in minutes[-10:]:  # Show last 10 minutes
                rate_hits = stats['rate_limit_by_minute'].get(minute, 0)
                success_hits = stats['success_by_minute'].get(minute, 0)
                total = rate_hits + success_hits
                success_pct = 0
                if total > 0:
                    success_pct = success_hits / total * 100
                
                print(f"{minute}: {'■' * min(rate_hits, 50)} {rate_hits} rate limits, {success_hits} successes ({success_pct:.1f}% success)")
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")

def main():
    parser = argparse.ArgumentParser(description='Monitor OpenAI API rate limits in logs')
    parser.add_argument('--log-file', type=str, help='Path to the log file to monitor')
    parser.add_argument('--interval', type=int, default=5, help='Update interval in seconds')
    args = parser.parse_args()
    
    log_file = args.log_file
    if not log_file:
        log_file = find_latest_log()
        
    if not log_file or not os.path.exists(log_file):
        print("No log file found. Please specify a valid log file.")
        return 1
    
    monitor_live(log_file, args.interval)
    return 0

if __name__ == "__main__":
    sys.exit(main()) 