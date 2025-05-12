#!/usr/bin/env python3
"""
Monitor Wookieepedia Pipeline Progress

This script provides a real-time monitor for the Wookieepedia processing pipeline,
showing colored progress indicators for XML processing, vector generation, and 
Pinecone uploads.

Usage:
    python scripts/monitor_pipeline.py
"""

import os
import time
import glob
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
PROCESSED_ARTICLES_DIR = "data/processed_articles"
VECTORS_DIR = "data/vectors"
LOGS_DIR = "logs"
REFRESH_RATE = 2  # seconds

# Initialize rich console
console = Console()

class PipelineMonitor:
    """Monitors the progress of the Wookieepedia processing pipeline."""
    
    def __init__(self):
        """Initialize the monitor."""
        self.start_time = datetime.now()
        self.last_article_count = 0
        self.last_vector_count = 0
        self.last_upload_count = 0
        self.article_batch_count = 0
        self.article_file_count = 0
        self.vector_file_count = 0
        self.upload_count = 0
        self.article_rates = []
        self.vector_rates = []
        self.upload_rates = []
        self.max_rates = 10  # Keep only the last N rate measurements
        
    def get_article_stats(self) -> Tuple[int, int]:
        """
        Get statistics about processed articles.
        
        Returns:
            Tuple of (batch count, file count)
        """
        try:
            if not os.path.exists(PROCESSED_ARTICLES_DIR):
                return 0, 0
                
            batch_dirs = [d for d in os.listdir(PROCESSED_ARTICLES_DIR) 
                          if os.path.isdir(os.path.join(PROCESSED_ARTICLES_DIR, d))]
            
            batch_count = len(batch_dirs)
            file_count = 0
            
            # Count files (limit to checking 5 newest batches for performance)
            newest_batches = sorted(batch_dirs, reverse=True)[:5]
            for batch in newest_batches:
                batch_path = os.path.join(PROCESSED_ARTICLES_DIR, batch)
                json_files = [f for f in os.listdir(batch_path) 
                              if f.endswith('.json')]
                file_count += len(json_files)
                
            # Estimate total count based on average files per batch
            if batch_count > 0 and newest_batches:
                avg_files_per_batch = file_count / len(newest_batches)
                estimated_total = int(avg_files_per_batch * batch_count)
                return batch_count, estimated_total
            
            return batch_count, file_count
        except Exception as e:
            logger.error(f"Error getting article stats: {e}")
            return 0, 0
    
    def get_vector_stats(self) -> int:
        """
        Get statistics about vector files.
        
        Returns:
            Count of vector files
        """
        try:
            if not os.path.exists(VECTORS_DIR):
                return 0
                
            vector_files = [f for f in os.listdir(VECTORS_DIR) 
                            if f.endswith('.parquet')]
            return len(vector_files)
        except Exception as e:
            logger.error(f"Error getting vector stats: {e}")
            return 0
    
    def get_upload_stats(self) -> int:
        """
        Get statistics about uploads to Pinecone.
        
        Returns:
            Count of uploaded vectors
        """
        try:
            # Check recent logs for upload information
            if not os.path.exists(LOGS_DIR):
                return 0
                
            log_files = sorted(glob.glob(f"{LOGS_DIR}/pipeline_*.log"), 
                               key=os.path.getmtime, reverse=True)
            
            if not log_files:
                return 0
                
            # Check most recent log file
            latest_log = log_files[0]
            upload_count = 0
            
            with open(latest_log, 'r') as f:
                for line in f:
                    if "vectors uploaded successfully" in line:
                        # Extract number from line like "100 vectors uploaded successfully"
                        try:
                            parts = line.split()
                            for i, part in enumerate(parts):
                                if part.isdigit() and i+1 < len(parts) and "vector" in parts[i+1]:
                                    upload_count = max(upload_count, int(part))
                        except:
                            pass
            
            return upload_count
        except Exception as e:
            logger.error(f"Error getting upload stats: {e}")
            return 0
    
    def calculate_rates(self):
        """Calculate processing rates for each stage."""
        current_time = datetime.now()
        time_diff = (current_time - self.start_time).total_seconds()
        
        if time_diff < 1:  # Avoid division by zero
            return
            
        # Calculate current rates
        article_rate = self.article_file_count / time_diff
        vector_rate = self.vector_file_count / time_diff
        upload_rate = self.upload_count / time_diff
        
        # Add to rate history
        self.article_rates.append(article_rate)
        self.vector_rates.append(vector_rate)
        self.upload_rates.append(upload_rate)
        
        # Keep only the most recent rates
        self.article_rates = self.article_rates[-self.max_rates:]
        self.vector_rates = self.vector_rates[-self.max_rates:]
        self.upload_rates = self.upload_rates[-self.max_rates:]
    
    def get_average_rates(self) -> Tuple[float, float, float]:
        """
        Get average processing rates.
        
        Returns:
            Tuple of (article rate, vector rate, upload rate)
        """
        article_avg = sum(self.article_rates) / max(len(self.article_rates), 1)
        vector_avg = sum(self.vector_rates) / max(len(self.vector_rates), 1)
        upload_avg = sum(self.upload_rates) / max(len(self.upload_rates), 1)
        
        return article_avg, vector_avg, upload_avg
    
    def update_stats(self):
        """Update all statistics."""
        # Get current stats
        self.article_batch_count, self.article_file_count = self.get_article_stats()
        self.vector_file_count = self.get_vector_stats()
        self.upload_count = self.get_upload_stats()
        
        # Calculate rates
        self.calculate_rates()
    
    def create_display(self) -> Layout:
        """
        Create a rich layout for display.
        
        Returns:
            Layout object with dashboard
        """
        layout = Layout()
        
        # Create header
        header = Panel(
            Text("Wookieepedia Pipeline Monitor", style="bold white on blue", justify="center"),
            border_style="blue"
        )
        
        # Create stats table
        stats_table = Table(show_header=True, header_style="bold magenta")
        stats_table.add_column("Stage", style="dim")
        stats_table.add_column("Count", justify="right")
        stats_table.add_column("Rate", justify="right")
        
        # Get average rates
        article_rate, vector_rate, upload_rate = self.get_average_rates()
        
        # Add rows with color-coding
        stats_table.add_row(
            "XML Processing", 
            f"{self.article_batch_count} batches\n{self.article_file_count} articles", 
            f"{article_rate:.2f} articles/sec",
            style="green"
        )
        stats_table.add_row(
            "Vector Generation", 
            f"{self.vector_file_count} vectors", 
            f"{vector_rate:.2f} vectors/sec",
            style="blue"
        )
        stats_table.add_row(
            "Pinecone Upload", 
            f"{self.upload_count} uploaded", 
            f"{upload_rate:.2f} uploads/sec",
            style="yellow"
        )
        
        # Create progress indicators
        progress = Table.grid()
        
        # Determine which stages are active
        xml_active = self.article_file_count > self.last_article_count
        vector_active = self.vector_file_count > self.last_vector_count
        upload_active = self.upload_count > self.last_upload_count
        
        # Create status indicators
        xml_status = "[green]ACTIVE[/green]" if xml_active else "[dim]idle[/dim]"
        vector_status = "[blue]ACTIVE[/blue]" if vector_active else "[dim]idle[/dim]"
        upload_status = "[yellow]ACTIVE[/yellow]" if upload_active else "[dim]idle[/dim]"
        
        progress.add_row(Text.from_markup(f"XML Processing: {xml_status}"))
        progress.add_row(Text.from_markup(f"Vector Generation: {vector_status}"))
        progress.add_row(Text.from_markup(f"Pinecone Upload: {upload_status}"))
        
        # Create timer
        elapsed = datetime.now() - self.start_time
        timer = Text.from_markup(
            f"[bold]Elapsed Time:[/bold] {elapsed.seconds // 3600:02}:{(elapsed.seconds % 3600) // 60:02}:{elapsed.seconds % 60:02}"
        )
        
        # Update last counts
        self.last_article_count = self.article_file_count
        self.last_vector_count = self.vector_file_count
        self.last_upload_count = self.upload_count
        
        # Put it all together
        layout.split(
            Layout(header, size=3),
            Layout(stats_table, size=12),
            Layout(progress, size=5),
            Layout(timer, size=3)
        )
        
        return layout
    
    def run(self):
        """Run the monitor continuously."""
        try:
            with Live(refresh_per_second=1/REFRESH_RATE) as live:
                while True:
                    self.update_stats()
                    live.update(self.create_display())
                    time.sleep(REFRESH_RATE)
        except KeyboardInterrupt:
            console.print("\n[bold green]Monitor stopped.[/bold green]")
        except Exception as e:
            console.print(f"\n[bold red]Error:[/bold red] {e}")

def main():
    """Main function to run the monitor."""
    console.print("[bold]Starting Wookieepedia Pipeline Monitor...[/bold]")
    monitor = PipelineMonitor()
    monitor.run()

if __name__ == "__main__":
    main() 