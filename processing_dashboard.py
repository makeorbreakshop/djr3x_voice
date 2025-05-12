"""
Processing Dashboard for XML Vector Processing Pipeline

Provides real-time visualization of processing status and metrics through:
1. CLI interface using Rich
2. Web dashboard using FastAPI (coming soon)
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass
from collections import deque
import json
from pathlib import Path
import logging

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.panel import Panel
from rich.layout import Layout

from process_status_manager import ProcessStatusManager

logger = logging.getLogger(__name__)

@dataclass
class ProcessingMetrics:
    """Stores processing metrics for visualization."""
    total_articles: int = 0
    processed_articles: int = 0
    failed_articles: int = 0
    vectorized_articles: int = 0
    uploaded_articles: int = 0
    current_batch_size: int = 0
    processing_rate: float = 0.0  # articles/minute
    start_time: Optional[datetime] = None
    recent_processing_times: deque = deque(maxlen=100)

class ProcessingDashboard:
    """Real-time dashboard for monitoring processing status."""
    
    def __init__(self, status_manager: ProcessStatusManager):
        """
        Initialize the dashboard.
        
        Args:
            status_manager: ProcessStatusManager instance to monitor
        """
        self.status_manager = status_manager
        self.metrics = ProcessingMetrics()
        self.console = Console()
        
        # Subscribe to status manager events
        self.status_manager.on_status_update = self._handle_status_update
        self.status_manager.on_batch_complete = self._handle_batch_complete
        
        # Initialize metrics
        self._init_metrics()
    
    def _init_metrics(self):
        """Initialize processing metrics from current status."""
        status = self.status_manager.get_status()
        self.metrics.total_articles = len(status.get('total_urls', set()))
        self.metrics.processed_articles = len(status.get('processed_urls', set()))
        self.metrics.failed_articles = len(self.status_manager.get_failed_articles())
        self.metrics.vectorized_articles = len(status.get('vectorized_urls', set()))
        self.metrics.uploaded_articles = len(status.get('uploaded_urls', set()))
        self.metrics.start_time = datetime.now()
    
    def _handle_status_update(self, url: str, status: Dict):
        """Handle status update events from ProcessStatusManager."""
        if status.get('processed'):
            self.metrics.processed_articles += 1
        if status.get('error'):
            self.metrics.failed_articles += 1
        if status.get('vectorized'):
            self.metrics.vectorized_articles += 1
        if status.get('uploaded'):
            self.metrics.uploaded_articles += 1
            
        # Update processing rate
        if self.metrics.start_time:
            elapsed_minutes = (datetime.now() - self.metrics.start_time).total_seconds() / 60
            if elapsed_minutes > 0:
                self.metrics.processing_rate = self.metrics.processed_articles / elapsed_minutes
    
    def _handle_batch_complete(self, batch_size: int, duration: float):
        """Handle batch completion events."""
        self.metrics.recent_processing_times.append(duration)
        self.metrics.current_batch_size = batch_size
    
    def _create_status_table(self) -> Table:
        """Create Rich table with current status."""
        table = Table(title="Processing Status")
        
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Percentage", style="yellow")
        
        def add_metric(name: str, value: int):
            percentage = (value / self.metrics.total_articles * 100) if self.metrics.total_articles else 0
            table.add_row(name, str(value), f"{percentage:.1f}%")
        
        add_metric("Total Articles", self.metrics.total_articles)
        add_metric("Processed", self.metrics.processed_articles)
        add_metric("Vectorized", self.metrics.vectorized_articles)
        add_metric("Uploaded", self.metrics.uploaded_articles)
        add_metric("Failed", self.metrics.failed_articles)
        
        return table
    
    def _create_metrics_panel(self) -> Panel:
        """Create Rich panel with processing metrics."""
        if not self.metrics.start_time:
            return Panel("Processing not started", title="Metrics")
            
        elapsed_time = datetime.now() - self.metrics.start_time
        elapsed_minutes = elapsed_time.total_seconds() / 60
        
        avg_batch_time = (
            sum(self.metrics.recent_processing_times) / len(self.metrics.recent_processing_times)
            if self.metrics.recent_processing_times
            else 0
        )
        
        metrics_text = (
            f"Processing Rate: {self.metrics.processing_rate:.1f} articles/minute\n"
            f"Elapsed Time: {elapsed_time}\n"
            f"Current Batch Size: {self.metrics.current_batch_size}\n"
            f"Average Batch Time: {avg_batch_time:.1f}s"
        )
        
        return Panel(metrics_text, title="Processing Metrics")
    
    async def start_cli(self):
        """Start the CLI dashboard."""
        layout = Layout()
        layout.split_column(
            Layout(name="status"),
            Layout(name="metrics")
        )
        
        with Live(layout, refresh_per_second=1) as live:
            while True:
                # Update layout
                layout["status"].update(self._create_status_table())
                layout["metrics"].update(self._create_metrics_panel())
                
                await asyncio.sleep(1)
    
    def save_metrics(self, output_file: str):
        """Save current metrics to a JSON file."""
        metrics_dict = {
            'timestamp': datetime.now().isoformat(),
            'total_articles': self.metrics.total_articles,
            'processed_articles': self.metrics.processed_articles,
            'failed_articles': self.metrics.failed_articles,
            'vectorized_articles': self.metrics.vectorized_articles,
            'uploaded_articles': self.metrics.uploaded_articles,
            'processing_rate': self.metrics.processing_rate
        }
        
        with open(output_file, 'w') as f:
            json.dump(metrics_dict, f, indent=2)

    def update_progress(self, current: int, total: int, stage: str) -> None:
        """
        Update progress for a processing stage.
        
        Args:
            current: Current item number
            total: Total items
            stage: Processing stage name
        """
        percentage = (current / total) * 100 if total > 0 else 0
        logger.info(f"Progress for {stage}: {current}/{total} ({percentage:.1f}%)")
        
    def log_stats(self) -> Dict[str, Any]:
        """
        Log current processing statistics.
        
        Returns:
            Dictionary of statistics
        """
        stats = self.status_manager.get_stats()
        
        logger.info("=== Processing Statistics ===")
        logger.info(f"Total URLs: {stats['total']}")
        
        if stats['total'] > 0:
            processed_pct = stats['processed']/stats['total']*100
            vectorized_pct = stats['vectorized']/stats['total']*100
            uploaded_pct = stats['uploaded']/stats['total']*100
            error_pct = stats['error']/stats['total']*100
        else:
            processed_pct = vectorized_pct = uploaded_pct = error_pct = 0
            
        logger.info(f"Processed: {stats['processed']} ({processed_pct:.1f}%)")
        logger.info(f"Vectorized: {stats['vectorized']} ({vectorized_pct:.1f}%)")
        logger.info(f"Uploaded: {stats['uploaded']} ({uploaded_pct:.1f}%)")
        logger.info(f"Errors: {stats['error']} ({error_pct:.1f}%)")
        
        return stats 