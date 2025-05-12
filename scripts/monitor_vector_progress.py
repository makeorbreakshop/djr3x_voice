#!/usr/bin/env python3
"""
Enhanced Vector Creation Progress Monitor
Uses rich for beautiful console output and detailed statistics
"""

import os
import sys
import time
import psutil
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import json
import pandas as pd
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.panel import Panel
from rich import box

# Constants
VECTORS_DIR = "data/vectors"
PROCESSED_ARTICLES_DIR = "data/processed_articles"
WINDOW_SIZE = 5  # minutes for rolling average

class VectorProgressMonitor:
    def __init__(self, pid: int = None, refresh_rate: int = 5, list_only: bool = False):
        self.console = Console()
        self.refresh_rate = refresh_rate
        self.list_only = list_only
        
        if list_only:
            self._list_vector_processes()
            sys.exit(0)
            
        self.pid = pid or self._find_vector_creation_pid()
        if not self.pid:
            self.console.print("[red]No vector creation process found!")
            self.console.print("\n[yellow]Available Python processes:")
            self._list_vector_processes()
            sys.exit(1)
        
        # Initialize tracking
        self.start_time = datetime.now()
        self.last_check_time = self.start_time
        self.last_vector_count = self._get_total_vectors()
        self.last_file_count = self._get_total_files()
        
        # Rolling windows for rates
        self.vector_rates = deque(maxlen=int(WINDOW_SIZE * 60 / refresh_rate))
        self.file_rates = deque(maxlen=int(WINDOW_SIZE * 60 / refresh_rate))
        self.token_rates = deque(maxlen=int(WINDOW_SIZE * 60 / refresh_rate))
        
        # Get initial counts
        self.total_articles = self._count_input_articles()
        
    def _list_vector_processes(self) -> None:
        """List all Python processes that might be vector creation processes"""
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
                    if 'create_vectors' in cmdline or 'vector' in cmdline.lower():
                        found = True
                        memory_mb = proc.memory_info().rss / 1024 / 1024
                        
                        # Get just the script name from the full command
                        script_name = "Unknown"
                        for arg in proc.cmdline():
                            if 'vector' in arg.lower():
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
            self.console.print("\n[green]Found these vector-related processes:")
            self.console.print(table)
            self.console.print("\n[yellow]Full command details:")
            self.console.print(details_table)
            self.console.print("\n[cyan]To monitor a process, run:")
            self.console.print("[yellow]python scripts/monitor_vector_progress.py --pid <PID>")
        else:
            self.console.print("[red]No vector creation processes found running!")
            self.console.print("[yellow]Make sure create_vectors_robust.py is running.")

    def _find_vector_creation_pid(self) -> int:
        """Find the PID of the create_vectors_robust.py process"""
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.name().lower():
                    cmdline = proc.cmdline()
                    if any('create_vectors_robust.py' in arg for arg in cmdline):
                        return proc.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def _count_input_articles(self) -> int:
        """Count total number of JSON files to process"""
        count = 0
        for path in Path(PROCESSED_ARTICLES_DIR).rglob("*.json"):
            count += 1
        return count

    def _get_total_vectors(self) -> int:
        """Get total number of vectors created"""
        total = 0
        for path in Path(VECTORS_DIR).glob("*.parquet"):
            try:
                df = pd.read_parquet(path)
                total += len(df)
            except:
                continue
        return total

    def _get_total_files(self) -> int:
        """Get total number of parquet files created"""
        return len(list(Path(VECTORS_DIR).glob("*.parquet")))

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
            self._list_vector_processes()
            sys.exit(1)

    def _calculate_rates(self) -> Tuple[float, float]:
        """Calculate current processing rates"""
        current_time = datetime.now()
        time_diff = (current_time - self.last_check_time).total_seconds()
        
        current_vectors = self._get_total_vectors()
        current_files = self._get_total_files()
        
        vector_rate = (current_vectors - self.last_vector_count) / time_diff * 3600
        file_rate = (current_files - self.last_file_count) / time_diff * 3600
        
        self.vector_rates.append(vector_rate)
        self.file_rates.append(file_rate)
        
        self.last_check_time = current_time
        self.last_vector_count = current_vectors
        self.last_file_count = current_files
        
        return vector_rate, file_rate

    def _create_progress_table(self) -> Table:
        """Create the progress display table"""
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        # Get current stats
        current_vectors = self._get_total_vectors()
        current_files = self._get_total_files()
        vector_rate, file_rate = self._calculate_rates()
        process_stats = self._get_process_stats()
        
        # Calculate averages
        avg_vector_rate = sum(self.vector_rates) / len(self.vector_rates) if self.vector_rates else 0
        avg_file_rate = sum(self.file_rates) / len(self.file_rates) if self.file_rates else 0
        
        # Calculate ETA
        if avg_file_rate > 0:
            remaining_articles = self.total_articles - current_files
            hours_remaining = remaining_articles / avg_file_rate
            eta = datetime.now() + timedelta(hours=hours_remaining)
        else:
            eta = "Unknown"
        
        # Add rows
        table.add_row("Process Status", process_stats["status"])
        table.add_row("Memory Usage", f"{process_stats['memory']:.1f} MB")
        table.add_row("CPU Usage", f"{process_stats['cpu']:.1f}%")
        table.add_row("Active Threads", f"{process_stats['threads']}")
        table.add_row("", "")
        table.add_row("Total Articles to Process", f"{self.total_articles:,}")
        table.add_row("Articles Processed", f"{current_files:,} ({current_files/self.total_articles*100:.2f}%)")
        table.add_row("Total Vectors Created", f"{current_vectors:,}")
        table.add_row("", "")
        table.add_row("Current Processing Rate", f"{vector_rate:.1f} vectors/hour")
        table.add_row(f"{WINDOW_SIZE}min Avg Rate", f"{avg_vector_rate:.1f} vectors/hour")
        table.add_row("Current File Rate", f"{file_rate:.1f} files/hour")
        table.add_row(f"{WINDOW_SIZE}min Avg File Rate", f"{avg_file_rate:.1f} files/hour")
        table.add_row("", "")
        table.add_row("Running Time", str(datetime.now() - self.start_time).split('.')[0])
        table.add_row("Estimated Completion", str(eta))
        
        return table

    def _create_progress_bars(self, progress: Progress) -> List:
        """Create progress bar tasks"""
        articles_task = progress.add_task(
            "[cyan]Articles Progress",
            total=self.total_articles,
            completed=self._get_total_files()
        )
        return [articles_task]

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
                task = progress.add_task("[cyan]Articles Progress", total=self.total_articles)
                
                while True:
                    # Clear screen
                    self.console.clear()
                    
                    # Update progress
                    completed = self._get_total_files()
                    progress.update(task, completed=completed)
                    
                    # Create and display table
                    table = self._create_progress_table()
                    self.console.print(table)
                    
                    # Show progress bar
                    self.console.print(Panel.fit(
                        f"{progress}",
                        title="Vector Creation Progress",
                        subtitle=f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        padding=(1, 2)
                    ))
                    
                    # Wait before next update
                    time.sleep(self.refresh_rate)
                    
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Monitoring stopped by user")
            return

def main():
    parser = argparse.ArgumentParser(description='Monitor vector creation progress with enhanced visualization')
    parser.add_argument('--pid', type=int, help='PID of create_vectors_robust.py process')
    parser.add_argument('--refresh', type=int, default=5, help='Refresh rate in seconds')
    parser.add_argument('--list', action='store_true', help='List running vector creation processes')
    args = parser.parse_args()
    
    monitor = VectorProgressMonitor(pid=args.pid, refresh_rate=args.refresh, list_only=args.list)
    monitor.run()

if __name__ == "__main__":
    main() 