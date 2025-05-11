"""
Tests for the ProcessingDashboard class.
"""

import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from rich.table import Table
from rich.panel import Panel

from processing_dashboard import ProcessingDashboard, ProcessingMetrics
from process_status_manager import ProcessStatusManager

@pytest.fixture
def temp_status_file():
    """Create a temporary status file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)

@pytest.fixture
def temp_upload_status_file():
    """Create a temporary upload status file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)

@pytest.fixture
def status_manager(temp_status_file, temp_upload_status_file):
    """Create a ProcessStatusManager instance for testing."""
    return ProcessStatusManager(
        status_file=temp_status_file,
        upload_status_file=temp_upload_status_file
    )

@pytest.fixture
def dashboard(status_manager):
    """Create a ProcessingDashboard instance for testing."""
    return ProcessingDashboard(status_manager)

def test_init_metrics(dashboard):
    """Test metrics initialization."""
    assert dashboard.metrics.total_articles == 0
    assert dashboard.metrics.processed_articles == 0
    assert dashboard.metrics.start_time is not None

def test_handle_status_update(dashboard):
    """Test handling status updates."""
    # Test successful processing
    dashboard._handle_status_update("test_url", {
        'processed': True,
        'vectorized': True,
        'uploaded': True
    })
    
    assert dashboard.metrics.processed_articles == 1
    assert dashboard.metrics.vectorized_articles == 1
    assert dashboard.metrics.uploaded_articles == 1
    assert dashboard.metrics.failed_articles == 0
    
    # Test error handling
    dashboard._handle_status_update("test_url2", {
        'error': "Test error"
    })
    
    assert dashboard.metrics.failed_articles == 1

def test_handle_batch_complete(dashboard):
    """Test handling batch completion."""
    dashboard._handle_batch_complete(10, 5.0)  # 10 articles in 5 seconds
    
    assert dashboard.metrics.current_batch_size == 10
    assert len(dashboard.metrics.recent_processing_times) == 1
    assert dashboard.metrics.recent_processing_times[0] == 5.0

def test_create_status_table(dashboard):
    """Test status table creation."""
    # Add some test data
    dashboard.metrics.total_articles = 100
    dashboard.metrics.processed_articles = 50
    dashboard.metrics.vectorized_articles = 30
    dashboard.metrics.uploaded_articles = 20
    dashboard.metrics.failed_articles = 5
    
    table = dashboard._create_status_table()
    
    assert isinstance(table, Table)
    assert table.title == "Processing Status"
    assert len(table.columns) == 3  # Metric, Value, Percentage

def test_create_metrics_panel(dashboard):
    """Test metrics panel creation."""
    # Set start time to 5 minutes ago
    dashboard.metrics.start_time = datetime.now() - timedelta(minutes=5)
    dashboard.metrics.processing_rate = 10.0
    dashboard.metrics.current_batch_size = 20
    dashboard.metrics.recent_processing_times.append(2.5)
    
    panel = dashboard._create_metrics_panel()
    
    assert isinstance(panel, Panel)
    assert "Processing Rate: 10.0" in panel.renderable
    assert "Current Batch Size: 20" in panel.renderable

def test_save_metrics(dashboard, tmp_path):
    """Test saving metrics to file."""
    # Set some test metrics
    dashboard.metrics.total_articles = 100
    dashboard.metrics.processed_articles = 50
    dashboard.metrics.processing_rate = 10.0
    
    # Save metrics
    output_file = tmp_path / "metrics.json"
    dashboard.save_metrics(str(output_file))
    
    # Load and verify
    with open(output_file) as f:
        saved_metrics = json.load(f)
    
    assert saved_metrics['total_articles'] == 100
    assert saved_metrics['processed_articles'] == 50
    assert saved_metrics['processing_rate'] == 10.0
    assert 'timestamp' in saved_metrics

@pytest.mark.asyncio
async def test_dashboard_updates(dashboard, status_manager):
    """Test dashboard updates from status manager events."""
    # Create task for dashboard
    dashboard_task = asyncio.create_task(
        dashboard.start_cli()
    )
    
    try:
        # Simulate processing updates
        status_manager.start_batch(batch_size=10)
        
        for i in range(5):
            status_manager.update_status(
                url=f"url{i}",
                title=f"Article {i}",
                processed=True,
                vectorized=True,
                uploaded=True
            )
            await asyncio.sleep(0.1)
        
        status_manager.clear_batch()
        
        # Verify metrics were updated
        assert dashboard.metrics.processed_articles == 5
        assert dashboard.metrics.vectorized_articles == 5
        assert dashboard.metrics.uploaded_articles == 5
        assert len(dashboard.metrics.recent_processing_times) == 1
        
    finally:
        # Clean up
        dashboard_task.cancel()
        try:
            await dashboard_task
        except asyncio.CancelledError:
            pass 