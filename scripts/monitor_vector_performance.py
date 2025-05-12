#!/usr/bin/env python3
"""
Vector Creation Performance Monitor
Provides real-time, accurate monitoring of vector creation process
"""

import os
import sys
import time
import json
import glob
import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
from rich.live import Live
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
import pandas as pd
import psutil

# Create global console
console = Console()

# Constants
WINDOW_SIZE = 5  # minutes to track for rate calculation
UPDATE_INTERVAL = 10  # seconds between updates

def find_vector_process():
    """Find a running vector creation process"""
    console.print("[yellow]Searching for vector creation processes...[/yellow]")
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Skip if not a Python process
            if not proc.info['name'] or 'python' not in proc.info['name'].lower():
                continue
                
            console.print(f"Found Python process: {proc.info['name']}")
            
            # Check command line
            if not proc.info['cmdline']:
                continue
                
            cmdline = ' '.join(proc.info['cmdline'])
            console.print(f"Found matching process: {cmdline[:50]}...")
            
            if 'create_vectors' in cmdline:
                console.print(f"[green]Found vector process: PID {proc.pid}[/green]")
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    return None

def count_processed_articles():
    """Count processed articles from status log"""
    try:
        # First check by parsing the status CSV directly
        status_file = Path("data/processing_status.csv")
        if status_file.exists():
            with open(status_file, 'r') as f:
                # Count lines, subtract header
                line_count = sum(1 for _ in f) - 1
                return line_count
        
        # Alternatively, try parsing logs
        log_files = list(Path(".").glob("vector_creation_*.log"))
        log_files.extend(list(Path("logs").glob("vector_creation_*.log")))
        
        if not log_files:
            return 0
            
        # Sort by modification time, most recent first
        log_file = sorted(log_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        
        with open(log_file, 'r') as f:
            lines = f.readlines()
        
        # Look for progress lines
        for line in reversed(lines):
            if "Progress:" in line:
                # Extract numbers from format like "Progress: 50/208074 articles (0.0%)"
                match = re.search(r"Progress: (\d+)/(\d+)", line)
                if match:
                    return int(match.group(1))
    except Exception as e:
        console.print(f"[red]Error counting processed articles: {e}[/red]")
    
    return 0

def count_vectors():
    """Count total vector embeddings created"""
    total = 0
    try:
        for path in Path("data/vectors").glob("*.parquet"):
            try:
                df = pd.read_parquet(path)
                total += len(df)
            except Exception as e:
                pass
    except Exception as e:
        console.print(f"[red]Error counting vectors: {e}[/red]")
    
    return total

def count_parquet_files():
    """Count parquet files created"""
    try:
        return len(list(Path("data/vectors").glob("*.parquet")))
    except Exception as e:
        console.print(f"[red]Error counting parquet files: {e}[/red]")
        return 0

def count_json_files():
    """Count total JSON files to process"""
    try:
        total = 0
        for batch_dir in Path("data/processed_articles").glob("batch_*"):
            if batch_dir.is_dir():
                total += len(list(batch_dir.glob("*.json")))
        return total
    except Exception as e:
        console.print(f"[red]Error counting JSON files: {e}[/red]")
        return 0

def parse_log_for_rates(recent_lines=200):
    """Parse log file to extract actual processing rates and skipped articles"""
    log_files = list(Path(".").glob("vector_creation_*.log"))
    log_files.extend(list(Path("logs").glob("vector_creation_*.log")))
    
    if not log_files:
        return None
    
    # Sort by modification time, most recent first
    log_file = sorted(log_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
    
    try:
        with open(log_file, 'r') as f:
            # Get all lines and take last N
            all_lines = f.readlines()
            lines = all_lines[-recent_lines:] if len(all_lines) > recent_lines else all_lines
        
        metrics = {
            'skipped_articles': 0,
            'processed_batch_size': 0,
            'articles_per_second': 0,
            'chunks_per_second': 0,
            'token_rate': 0,
            'rate_limits_hit': 0,
            'eta': None
        }
        
        # Find the most recent progress line
        for line in reversed(lines):
            if "Skipping already processed URL" in line:
                metrics['skipped_articles'] += 1
            
            if "Worker processing batch" in line:
                match = re.search(r"batch \d+ with (\d+) articles", line)
                if match:
                    metrics['processed_batch_size'] = int(match.group(1))
            
            if "Progress:" in line:
                # Extract speed and rate metrics
                # Format: "Progress: 50/208074 articles (0.0%) - Skipped: 10 articles - Speed: 0.45 articles/sec, 8.13 chunks/sec - Token rate: 144357 tokens/min - Rate limits hit: 0 - ETA: 2025-05-17 21:20:18"
                articles_match = re.search(r"Speed: ([\d\.]+) articles/sec", line)
                chunks_match = re.search(r"([\d\.]+) chunks/sec", line)
                token_match = re.search(r"Token rate: ([\d\.]+) tokens/min", line)
                rate_limits_match = re.search(r"Rate limits hit: (\d+)", line)
                eta_match = re.search(r"ETA: ([\d\-]+\s[\d:]+)", line)
                
                if articles_match:
                    metrics['articles_per_second'] = float(articles_match.group(1))
                
                if chunks_match:
                    metrics['chunks_per_second'] = float(chunks_match.group(1))
                
                if token_match:
                    metrics['token_rate'] = float(token_match.group(1))
                
                if rate_limits_match:
                    metrics['rate_limits_hit'] = int(rate_limits_match.group(1))
                
                if eta_match:
                    metrics['eta'] = eta_match.group(1)
                
                # Once we find a progress line, we have what we need
                break
        
        return metrics
    
    except Exception as e:
        console.print(f"[red]Error parsing log for rates: {e}[/red]")
        return None

def create_status_table(metrics):
    """Create a table showing current status"""
    table = Table(title="Current Status", box=box.ROUNDED)
    
    table.add_column("Item", style="cyan")
    table.add_column("Count", style="green")
    
    table.add_row("Articles Processed", f"{metrics['processed_articles']:,}")
    table.add_row("Articles Remaining", f"{metrics['remaining_articles']:,}")
    table.add_row("Total Vectors Created", f"{metrics['total_vectors']:,}")
    table.add_row("Parquet Files Created", f"{metrics['parquet_files']:,}")
    
    return table

def create_performance_table(metrics, log_metrics):
    """Create a table showing performance metrics"""
    table = Table(title="Vector Creation Performance", box=box.ROUNDED)
    
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    # Add rows
    table.add_row("Progress", f"{metrics['progress_percentage']:.2f}%")
    
    # Use the actual processing rate from logs if available
    if log_metrics and log_metrics['articles_per_second'] > 0:
        articles_per_hour = log_metrics['articles_per_second'] * 3600
        table.add_row("Processing Rate", f"{articles_per_hour:.1f} articles/hour")
        
        # Only include this if we have active metrics from the log
        if log_metrics['chunks_per_second'] > 0:
            table.add_row("Chunk Processing Rate", f"{log_metrics['chunks_per_second']:.2f} chunks/sec")
        
        if log_metrics['token_rate'] > 0:
            table.add_row("Token Rate", f"{log_metrics['token_rate']:,.0f} tokens/minute")
        
        # Use the ETA from the log if available
        if log_metrics['eta']:
            table.add_row("Estimated Completion", log_metrics['eta'])
            
            # Calculate hours remaining
            try:
                eta_time = datetime.strptime(log_metrics['eta'], "%Y-%m-%d %H:%M:%S")
                hours_remaining = (eta_time - datetime.now()).total_seconds() / 3600
                table.add_row("Hours Remaining", f"{hours_remaining:.1f} hours")
            except:
                pass
    else:
        # Fall back to calculated rates if log parsing failed
        table.add_row("Processing Rate", f"{metrics.get('files_per_hour', 0):.1f} files/hour")
        
        if 'estimated_completion' in metrics:
            table.add_row("Estimated Completion", metrics['estimated_completion'])
        
        if 'estimated_hours_remaining' in metrics:
            table.add_row("Hours Remaining", f"{metrics['estimated_hours_remaining']:.1f} hours")
    
    return table

def monitor(interval=UPDATE_INTERVAL):
    """Monitor the vector creation process"""
    console.print("\n[bold green]Starting Vector Creation Performance Monitor...[/bold green]")
    
    # Initial counts
    total_json_files = count_json_files()
    console.print(f"Total JSON files to process: {total_json_files:,}")
    
    last_files_count = -1
    last_vectors_count = -1
    
    while True:
        # Don't clear the console, just print a separator
        console.print(f"\n[bold cyan]Vector Creation Monitor[/bold cyan] - {datetime.now().strftime('%H:%M:%S')}")
        
        # Debug output
        console.print("[yellow]Looking for vector creation process...[/yellow]")
        
        proc = find_vector_process()
        if not proc:
            console.print("\n[yellow]No vector creation process found running.[/yellow]")
            
            # Check for log files at least
            log_files = list(Path(".").glob("vector_creation_*.log"))
            if log_files:
                console.print(f"[green]Found {len(log_files)} log files. Most recent: {sorted(log_files, key=lambda x: x.stat().st_mtime, reverse=True)[0]}[/green]")
                
                # Try to parse latest log
                log_metrics = parse_log_for_rates()
                if log_metrics:
                    console.print("[green]Found recent log information:[/green]")
                    for key, value in log_metrics.items():
                        console.print(f"  [cyan]{key}:[/cyan] {value}")
            
            console.print("[yellow]Waiting for vector creation process to start...[/yellow]")
            time.sleep(interval)
        else:
            console.print(f"[green]Found vector process: PID {proc.pid}[/green]")
            
            try:
                # Get current counts
                console.print("[yellow]Counting files and vectors...[/yellow]")
                parquet_files = count_parquet_files()
                total_vectors = count_vectors()
                processed_articles = count_processed_articles()
                
                # Calculate rates if we have previous values
                metrics = {
                    "parquet_files": parquet_files,
                    "total_vectors": total_vectors,
                    "processed_articles": processed_articles,
                    "remaining_articles": total_json_files - processed_articles,
                    "progress_percentage": (processed_articles / total_json_files * 100) if total_json_files > 0 else 0,
                }
                
                if last_files_count != -1 and last_files_count != parquet_files:
                    new_files = parquet_files - last_files_count
                    new_vectors = total_vectors - last_vectors_count
                    metrics["files_per_hour"] = new_files * (3600/interval)
                    metrics["vectors_per_hour"] = new_vectors * (3600/interval)
                
                # Update last counts
                last_files_count = parquet_files
                last_vectors_count = total_vectors
                
                # Parse log for more accurate rates
                console.print("[yellow]Analyzing log files for accurate rates...[/yellow]")
                log_metrics = parse_log_for_rates()
                
                # Display statistics
                console.print("[yellow]Displaying statistics...[/yellow]")
                
                # Create tables
                status_table = create_status_table(metrics)
                performance_table = create_performance_table(metrics, log_metrics)
                
                # Display tables
                console.print(status_table)
                console.print("")  # Spacing
                console.print(performance_table)
                
                # Show process info
                runtime_minutes = (time.time() - proc.create_time()) / 60
                console.print(f"\nProcess: PID {proc.pid}, running for {runtime_minutes:.1f} minutes")
                
                # Memory and CPU usage
                process_memory = proc.memory_info().rss / 1024 / 1024  # MB
                process_cpu = proc.cpu_percent(interval=0.1)
                console.print(f"Memory: {process_memory:.1f} MB, CPU: {process_cpu:.1f}%")
                
                console.print(f"\nNext update in {interval} seconds. Press Ctrl+C to stop.")
                time.sleep(interval)
            
            except KeyboardInterrupt:
                console.print("[yellow]Monitoring stopped.[/yellow]")
                break
            except Exception as e:
                console.print(f"[red]Error in monitoring: {e}[/red]")
                time.sleep(interval)

if __name__ == "__main__":
    interval = UPDATE_INTERVAL
    if len(sys.argv) > 1:
        try:
            interval = int(sys.argv[1])
        except:
            pass
    monitor(interval) 