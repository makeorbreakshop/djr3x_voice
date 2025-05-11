"""
Tests for the ProcessStatusManager class.
"""

import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from process_status_manager import ProcessStatusManager, ProcessingStatus

@pytest.fixture
def temp_status_file():
    """Create a temporary status file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
        yield f.name
    os.unlink(f.name)

@pytest.fixture
def temp_upload_status_file():
    """Create a temporary upload status file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        yield f.name
    os.unlink(f.name)

@pytest.fixture
def manager(temp_status_file, temp_upload_status_file):
    """Create a ProcessStatusManager instance for testing."""
    return ProcessStatusManager(
        status_file=temp_status_file,
        upload_status_file=temp_upload_status_file
    )

def test_init_empty(manager):
    """Test initialization with empty status files."""
    assert len(manager.status_map) == 0
    assert len(manager.processed_urls) == 0

def test_update_status(manager):
    """Test updating article processing status."""
    url = "https://starwars.fandom.com/wiki/Test_Article"
    title = "Test Article"
    
    # Initial update
    manager.update_status(url, title, processed=True)
    assert url in manager.status_map
    assert manager.status_map[url].processed
    assert not manager.status_map[url].vectorized
    assert not manager.status_map[url].uploaded
    
    # Update with vectorization
    manager.update_status(url, title, processed=True, vectorized=True)
    assert manager.status_map[url].vectorized
    
    # Update with error
    manager.update_status(url, title, error="Test error")
    assert manager.status_map[url].error == "Test error"

def test_needs_processing(manager):
    """Test determining if an article needs processing."""
    url = "https://starwars.fandom.com/wiki/Test_Article"
    title = "Test Article"
    
    # New article needs processing
    assert manager.needs_processing(url)
    
    # Fully processed article doesn't need processing
    manager.update_status(url, title, processed=True, vectorized=True, uploaded=True)
    assert not manager.needs_processing(url)
    
    # Article with error needs processing
    manager.update_status(url, title, error="Test error")
    assert manager.needs_processing(url)
    
    # Article with different last_modified needs processing
    manager.update_status(url, title, processed=True, vectorized=True, uploaded=True)
    assert manager.needs_processing(url, last_modified="2025-05-11")

def test_save_and_load_status(manager):
    """Test saving and loading processing status."""
    url1 = "https://starwars.fandom.com/wiki/Article1"
    url2 = "https://starwars.fandom.com/wiki/Article2"
    
    # Add some test data
    manager.update_status(url1, "Article 1", processed=True, vectorized=True)
    manager.update_status(url2, "Article 2", processed=True, error="Test error")
    
    # Save status
    manager.save_status()
    
    # Create new manager instance to test loading
    new_manager = ProcessStatusManager(
        status_file=manager.status_file,
        upload_status_file=manager.upload_status_file
    )
    
    # Verify loaded data
    assert len(new_manager.status_map) == 2
    assert new_manager.status_map[url1].vectorized
    assert new_manager.status_map[url2].error == "Test error"

def test_get_processing_stats(manager):
    """Test getting processing statistics."""
    # Add test data
    manager.update_status("url1", "Article 1", processed=True, vectorized=True, uploaded=True)
    manager.update_status("url2", "Article 2", processed=True, error="Error")
    manager.update_status("url3", "Article 3")
    
    stats = manager.get_processing_stats()
    assert stats['total'] == 3
    assert stats['processed'] == 1  # Only url1 is fully processed
    assert stats['vectorized'] == 1
    assert stats['uploaded'] == 1
    assert stats['errors'] == 1
    assert stats['pending'] == 1

def test_get_failed_articles(manager):
    """Test getting list of failed articles."""
    # Add test data with errors
    manager.update_status("url1", "Article 1", error="Error 1")
    manager.update_status("url2", "Article 2", processed=True)
    manager.update_status("url3", "Article 3", error="Error 2")
    
    failed = manager.get_failed_articles()
    assert len(failed) == 2
    assert ("Article 1", "url1", "Error 1") in failed
    assert ("Article 3", "url3", "Error 2") in failed

def test_reset_failed_articles(manager):
    """Test resetting failed articles."""
    # Add test data with errors
    manager.update_status("url1", "Article 1", error="Error 1")
    manager.update_status("url2", "Article 2", processed=True)
    manager.update_status("url3", "Article 3", error="Error 2")
    
    count = manager.reset_failed_articles()
    assert count == 2
    
    # Verify reset
    assert not manager.status_map["url1"].error
    assert not manager.status_map["url1"].processed
    assert not manager.status_map["url3"].error
    assert not manager.status_map["url3"].processed
    
    # Verify non-failed article unchanged
    assert manager.status_map["url2"].processed

def test_compare_urls(manager):
    """Test URL comparison functionality."""
    # Setup existing articles
    manager.update_status("url1", "Article 1", processed=True, vectorized=True, uploaded=True)
    manager.update_status("url2", "Article 2", processed=True, error="Error")  # Needs update
    manager.update_status("url3", "Article 3", processed=False)  # Needs update
    manager.update_status("url4", "Article 4", processed=True, vectorized=True, uploaded=True)
    
    # XML dump URLs
    xml_urls = {"url1", "url2", "url3", "url5", "url6"}  # url4 is deleted, url5/6 are new
    
    # Compare URLs
    new_urls, update_urls, deleted_urls = manager.compare_urls(xml_urls)
    
    # Verify results
    assert new_urls == {"url5", "url6"}
    assert update_urls == {"url2", "url3"}  # Needs update due to error and incomplete
    assert deleted_urls == {"url4"}
    
    # Verify deleted URL is marked
    assert manager.status_map["url4"].deleted
    assert "url4" not in manager.processed_urls

def test_batch_processing(manager):
    """Test batch processing functionality."""
    # Start batch with custom size
    manager.start_batch(batch_size=3)
    
    # Add URLs to batch
    assert not manager.add_to_batch("url1")  # Batch not full
    assert not manager.add_to_batch("url2")  # Batch not full
    assert manager.add_to_batch("url3")      # Batch full
    
    # Check batch contents
    batch = manager.get_current_batch()
    assert len(batch) == 3
    assert batch == ["url1", "url2", "url3"]
    
    # Check batch stats
    stats = manager.get_batch_stats()
    assert stats['batch_size'] == 3
    assert stats['current_size'] == 3
    assert stats['remaining_capacity'] == 0
    
    # Clear batch
    manager.clear_batch()
    assert len(manager.get_current_batch()) == 0

def test_deleted_articles(manager):
    """Test handling of deleted articles."""
    # Setup some articles
    manager.update_status("url1", "Article 1", processed=True)
    manager.update_status("url2", "Article 2", processed=True)
    manager.update_status("url3", "Article 3", processed=True)
    
    # Mark some as deleted
    deleted_urls = {"url1", "url3"}
    manager.mark_deleted(deleted_urls)
    
    # Check deleted status
    deleted = manager.get_deleted_articles()
    assert len(deleted) == 2
    assert ("Article 1", "url1") in deleted
    assert ("Article 3", "url3") in deleted
    
    # Verify processed_urls doesn't contain deleted
    assert "url1" not in manager.processed_urls
    assert "url3" not in manager.processed_urls
    assert "url2" in manager.processed_urls

def test_save_load_with_deleted(manager):
    """Test saving and loading status with deleted articles."""
    # Setup articles including deleted
    manager.update_status("url1", "Article 1", processed=True)
    manager.update_status("url2", "Article 2", processed=True)
    manager.mark_deleted({"url1"})
    
    # Save status
    manager.save_status()
    
    # Create new manager and load
    new_manager = ProcessStatusManager(
        status_file=manager.status_file,
        upload_status_file=manager.upload_status_file
    )
    
    # Verify deleted status preserved
    assert new_manager.status_map["url1"].deleted
    assert not new_manager.status_map["url2"].deleted
    assert "url1" not in new_manager.processed_urls
    assert "url2" in new_manager.processed_urls 