#!/usr/bin/env python3
"""
E5 Migration Progress Monitor
Tracks progress of migration from OpenAI embeddings to E5 embeddings in Pinecone
"""

import os
import sys
import time
import psutil
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.panel import Panel
from rich import box
import pinecone
from pinecone import Pinecone

# Constants
WINDOW_SIZE = 5  # minutes for rolling average
DEFAULT_DB_PATH = "e5_migration.db"

class E5MigrationMonitor:
    def __init__(self, 
                 source_index: str = "holocron-knowledge",
                 target_index: str = "holocron-sbert-e5-new", 
                 db_path: str = DEFAULT_DB_PATH,
                 pid: int = None,
                 refresh_rate: int = 5, 
                 list_only: bool = False):
        self.console = Console()
        self.refresh_rate = refresh_rate
        self.list_only = list_only
        self.source_index = source_index
        self.target_index = target_index
        self.db_path = db_path
        
        # Initialize Pinecone client
        try:
            pc = Pinecone()
            self.source_idx = pc.Index(source_index)
            self.target_idx = pc.Index(target_index)
            self.console.print(f"[green]Connected to Pinecone indexes: {source_index} → {target_index}")
        except Exception as e:
            self.console.print(f"[red]Error connecting to Pinecone: {e}")
            sys.exit(1)
        
        if list_only:
            self._list_migration_processes()
            sys.exit(0)
            
        self.pid = pid or self._find_migration_pid()
        if not self.pid:
            self.console.print("[red]No E5 migration process found!")
            self.console.print("\n[yellow]Available Python processes:")
            self._list_migration_processes()
            sys.exit(1)
        
        # Initialize tracking
        self.start_time = datetime.now()
        self.last_check_time = self.start_time
        self.last_processed_count = self._get_processed_count()
        self.last_target_count = self._get_target_count()
        
        # Rolling windows for rates
        self.process_rates = deque(maxlen=int(WINDOW_SIZE * 60 / refresh_rate))
        self.upload_rates = deque(maxlen=int(WINDOW_SIZE * 60 / refresh_rate))
        
        # Get initial counts
        self.total_vectors = self._get_total_vectors()
        
    def _list_migration_processes(self) -> None:
        """List all Python processes that might be migration processes"""
        table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
        table.add_column("PID", style="cyan")
        table.add_column("Script Name", style="green")
        table.add_column("Memory (MB)", style="yellow", justify="right")
        table.add_column("CPU %", style="red", justify="right")
        
        details_table = Table(show_header=False, box=box.ROUNDED, style="dim")
        details_table.add_column("Details", style="yellow")
        
        found = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info', 'cpu_percent']):
            try:
                if 'python' in proc.name().lower():
                    cmdline = ' '.join(proc.cmdline())
                    if 'migrate_to_e5' in cmdline:
                        found = True
                        memory_mb = proc.memory_info().rss / 1024 / 1024
                        
                        # Get just the script name from the full command
                        script_name = "Unknown"
                        for arg in proc.cmdline():
                            if 'migrate_to_e5' in arg:
                                script_name = os.path.basename(arg)
                                break
                        
                        table.add_row(
                            str(proc.pid),
                            script_name,
                            f"{memory_mb:.1f}",
                            f"{proc.cpu_percent():.1f}"
                        )
                        
                        # Add full command to details
                        details_table.add_row(f"PID {proc.pid} full command:")
                        details_table.add_row(cmdline)
                        details_table.add_row("")  # Empty row for spacing
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if found:
            self.console.print("\n[green]Found these E5 migration processes:")
            self.console.print(table)
            self.console.print("\n[yellow]Full command details:")
            self.console.print(details_table)
            self.console.print("\n[cyan]To monitor a process, run:")
            self.console.print("[yellow]python scripts/monitor_e5_migration.py --pid <PID>")
        else:
            self.console.print("[red]No E5 migration processes found running!")
            self.console.print("[yellow]Make sure migrate_to_e5_embeddings.py is running.")

    def _find_migration_pid(self) -> int:
        """Find the PID of the migrate_to_e5_embeddings.py process"""
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.name().lower():
                    cmdline = proc.cmdline()
                    if any('migrate_to_e5_embeddings.py' in arg for arg in cmdline):
                        return proc.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def _get_processed_count(self) -> int:
        """Get count of processed vectors from SQLite database"""
        if not os.path.exists(self.db_path):
            return 0
            
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM processed_vectors")
                return cursor.fetchone()[0]
        except Exception as e:
            self.console.print(f"[red]Error reading database: {e}")
            return 0

    def _get_target_count(self) -> int:
        """Get count of vectors in the target index"""
        try:
            stats = self.target_idx.describe_index_stats()
            namespace_stats = stats.namespaces.get("", None)
            if namespace_stats:
                return namespace_stats.vector_count
            return 0
        except Exception as e:
            self.console.print(f"[red]Error fetching target index stats: {e}")
            return 0

    def _get_total_vectors(self) -> int:
        """Get total number of vectors in source index"""
        try:
            stats = self.source_idx.describe_index_stats()
            namespace_stats = stats.namespaces.get("", None)
            if namespace_stats:
                return namespace_stats.vector_count
            return 0
        except Exception as e:
            self.console.print(f"[red]Error fetching source index stats: {e}")
            return 0

    def _get_process_stats(self) -> Dict:
        """Get process statistics"""
        if not self.pid:
            return {"status": "Not Found", "memory": 0, "cpu": 0}
        
        try:
            process = psutil.Process(self.pid)
            return {
                "status": "Running",
                "memory": process.memory_info().rss / 1024 / 1024,  # MB
                "cpu": process.cpu_percent(),
                "threads": process.num_threads()
            }
        except psutil.NoSuchProcess:
            self.console.print("[red]Process not found! It may have terminated.")
            self._list_migration_processes()
            sys.exit(1)

    def _calculate_rates(self) -> Tuple[float, float]:
        """Calculate current processing rates"""
        current_time = datetime.now()
        time_diff = (current_time - self.last_check_time).total_seconds()
        
        current_processed = self._get_processed_count()
        current_target = self._get_target_count()
        
        process_rate = (current_processed - self.last_processed_count) / time_diff * 60  # per minute
        upload_rate = (current_target - self.last_target_count) / time_diff * 60  # per minute
        
        self.process_rates.append(process_rate)
        self.upload_rates.append(upload_rate)
        
        self.last_check_time = current_time
        self.last_processed_count = current_processed
        self.last_target_count = current_target
        
        return process_rate, upload_rate

    def _create_progress_table(self) -> Table:
        """Create the progress display table"""
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        # Get current stats
        current_processed = self._get_processed_count()
        current_target = self._get_target_count()
        process_rate, upload_rate = self._calculate_rates()
        process_stats = self._get_process_stats()
        
        # Calculate averages
        avg_process_rate = sum(self.process_rates) / len(self.process_rates) if self.process_rates else 0
        avg_upload_rate = sum(self.upload_rates) / len(self.upload_rates) if self.upload_rates else 0
        
        # Calculate ETA
        if avg_process_rate > 0:
            remaining_vectors = self.total_vectors - current_processed
            minutes_remaining = remaining_vectors / avg_process_rate
            eta = datetime.now() + timedelta(minutes=minutes_remaining)
        else:
            eta = "Unknown"
        
        # Add rows
        table.add_row("Process Status", process_stats["status"])
        table.add_row("Memory Usage", f"{process_stats['memory']:.1f} MB")
        table.add_row("CPU Usage", f"{process_stats['cpu']:.1f}%")
        table.add_row("Active Threads", f"{process_stats['threads']}")
        table.add_row("", "")
        table.add_row("Total Vectors in Source", f"{self.total_vectors:,}")
        table.add_row("Vectors Processed", f"{current_processed:,} ({current_processed/self.total_vectors*100:.2f}%)")
        table.add_row("Vectors in Target Index", f"{current_target:,}")
        table.add_row("", "")
        table.add_row("Current Processing Rate", f"{process_rate:.1f} vectors/minute")
        table.add_row(f"{WINDOW_SIZE}min Avg Process Rate", f"{avg_process_rate:.1f} vectors/minute")
        table.add_row("Current Upload Rate", f"{upload_rate:.1f} vectors/minute")
        table.add_row(f"{WINDOW_SIZE}min Avg Upload Rate", f"{avg_upload_rate:.1f} vectors/minute")
        table.add_row("", "")
        table.add_row("Running Time", str(datetime.now() - self.start_time).split('.')[0])
        table.add_row("Estimated Completion", str(eta))
        
        return table

    def run(self):
        """Run the monitoring loop"""
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                expand=True
            ) as progress:
                # Create progress bar
                task = progress.add_task("[cyan]Migration Progress", total=self.total_vectors)
                
                while True:
                    # Clear screen
                    self.console.clear()
                    
                    # Update progress
                    completed = self._get_processed_count()
                    progress.update(task, completed=completed)
                    
                    # Create and display table
                    table = self._create_progress_table()
                    self.console.print(table)
                    
                    # Show progress bar
                    self.console.print(Panel.fit(
                        f"{progress}",
                        title="E5 Migration Progress",
                        subtitle=f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        padding=(1, 2)
                    ))
                    
                    # Wait before next update
                    time.sleep(self.refresh_rate)
                    
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Monitoring stopped by user")
            return

def main():
    parser = argparse.ArgumentParser(description='Monitor E5 migration progress')
    parser.add_argument('--source-index', type=str, default='holocron-knowledge', help='Source Pinecone index name')
    parser.add_argument('--target-index', type=str, default='holocron-sbert-e5-new', help='Target Pinecone index name')
    parser.add_argument('--db-path', type=str, default=DEFAULT_DB_PATH, help='Path to the SQLite database')
    parser.add_argument('--pid', type=int, help='PID of migrate_to_e5_embeddings.py process')
    parser.add_argument('--refresh', type=int, default=5, help='Refresh rate in seconds')
    parser.add_argument('--list', action='store_true', help='List running migration processes')
    args = parser.parse_args()
    
    monitor = E5MigrationMonitor(
        source_index=args.source_index,
        target_index=args.target_index,
        db_path=args.db_path,
        pid=args.pid,
        refresh_rate=args.refresh, 
        list_only=args.list
    )
    monitor.run()

if __name__ == "__main__":
    main() 