#!/usr/bin/env python3
"""
Pinecone Import Monitoring Script

This script monitors the progress of vector imports into Pinecone:
1. Tracks the number of vectors imported
2. Monitors import speed and estimates completion time
3. Checks for any import errors or failures
4. Provides real-time status updates

Usage:
    python scripts/monitor_pinecone_import.py [--index-name NAME] [--interval SECONDS]
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import pinecone
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.table import Table

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
console = Console()

class ImportMonitor:
    """Monitors Pinecone import progress."""
    
    def __init__(self, index_name: str, check_interval: int = 30):
        """
        Initialize the import monitor.
        
        Args:
            index_name: Name of the Pinecone index
            check_interval: How often to check progress (in seconds)
        """
        self.index_name = index_name
        self.check_interval = check_interval
        self.start_time = datetime.now()
        self.last_vector_count = 0
        self.total_expected_vectors = 0
        
        # Initialize Pinecone
        pinecone.init(
            api_key=os.getenv('PINECONE_API_KEY'),
            environment=os.getenv('PINECONE_ENVIRONMENT', 'us-west1-gcp')
        )
        
        # Get the index
        try:
            self.index = pinecone.Index(self.index_name)
        except Exception as e:
            logger.error(f"Failed to connect to Pinecone index: {str(e)}")
            raise
            
    def get_vector_count(self) -> int:
        """Get the current number of vectors in the index."""
        try:
            stats = self.index.describe_index_stats()
            return stats.total_vector_count
        except Exception as e:
            logger.error(f"Error getting vector count: {str(e)}")
            return 0
            
    def calculate_progress(self, current_count: int) -> Dict[str, Any]:
        """
        Calculate import progress metrics.
        
        Returns:
            Dictionary containing progress metrics
        """
        now = datetime.now()
        elapsed = (now - self.start_time).total_seconds()
        
        # Calculate vectors per second
        if elapsed > 0:
            vectors_per_second = current_count / elapsed
        else:
            vectors_per_second = 0
            
        # Calculate vectors since last check
        vectors_added = current_count - self.last_vector_count
        self.last_vector_count = current_count
        
        # Estimate time remaining if we know total expected
        if self.total_expected_vectors > 0:
            remaining_vectors = self.total_expected_vectors - current_count
            if vectors_per_second > 0:
                time_remaining = remaining_vectors / vectors_per_second
            else:
                time_remaining = float('inf')
        else:
            time_remaining = None
            
        return {
            'current_count': current_count,
            'vectors_per_second': vectors_per_second,
            'vectors_added': vectors_added,
            'elapsed_seconds': elapsed,
            'time_remaining': time_remaining
        }
        
    def display_progress(self, metrics: Dict[str, Any]):
        """Display progress information using rich."""
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="dim")
        table.add_column("Value")
        
        # Add metrics to table
        table.add_row(
            "Total Vectors",
            f"{metrics['current_count']:,}"
        )
        table.add_row(
            "Import Rate",
            f"{metrics['vectors_per_second']:.2f} vectors/second"
        )
        table.add_row(
            "Vectors Added",
            f"{metrics['vectors_added']:,} since last check"
        )
        table.add_row(
            "Elapsed Time",
            str(timedelta(seconds=int(metrics['elapsed_seconds'])))
        )
        
        if metrics['time_remaining'] is not None:
            table.add_row(
                "Estimated Remaining",
                str(timedelta(seconds=int(metrics['time_remaining'])))
            )
            
        # Clear screen and display table
        console.clear()
        console.print(table)
        
    def monitor(self):
        """Main monitoring loop."""
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
                transient=True
            ) as progress:
                
                task = progress.add_task(
                    "Monitoring import...",
                    total=self.total_expected_vectors if self.total_expected_vectors > 0 else None
                )
                
                while True:
                    # Get current progress
                    current_count = self.get_vector_count()
                    metrics = self.calculate_progress(current_count)
                    
                    # Update progress bar if we have a total
                    if self.total_expected_vectors > 0:
                        progress.update(task, completed=current_count)
                    
                    # Display detailed metrics
                    self.display_progress(metrics)
                    
                    # Check if import is complete
                    if (self.total_expected_vectors > 0 and 
                        current_count >= self.total_expected_vectors):
                        logger.info("Import completed successfully!")
                        break
                        
                    # Wait for next check
                    time.sleep(self.check_interval)
                    
        except KeyboardInterrupt:
            logger.info("\nMonitoring stopped by user")
        except Exception as e:
            logger.error(f"Error during monitoring: {str(e)}")
            raise

def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description='Monitor Pinecone import progress')
    parser.add_argument('--index-name', default='holocron-knowledge',
                      help='Name of the Pinecone index (default: holocron-knowledge)')
    parser.add_argument('--interval', type=int, default=30,
                      help='Check interval in seconds (default: 30)')
    parser.add_argument('--expected-vectors', type=int,
                      help='Total number of vectors expected (optional)')
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    # Verify environment variables
    if not os.getenv('PINECONE_API_KEY'):
        logger.error("PINECONE_API_KEY not found in environment")
        sys.exit(1)
        
    try:
        # Create and run monitor
        monitor = ImportMonitor(args.index_name, args.interval)
        if args.expected_vectors:
            monitor.total_expected_vectors = args.expected_vectors
        monitor.monitor()
        
    except Exception as e:
        logger.error(f"Error running import monitor: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 