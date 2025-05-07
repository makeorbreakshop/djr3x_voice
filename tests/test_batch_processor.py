"""
Tests for the Holocron Batch Processing System.

This module contains comprehensive tests for the BatchProcessor class and its components,
including queue management, rate limiting, progress tracking, and error handling.
"""

import os
import json
import asyncio
import pytest
import shutil
from datetime import datetime, timedelta
from typing import List, Dict, Any

from src.holocron.batch_processor import BatchProcessor, BatchProgress, RateLimiter

# Test data
TEST_URLS = [
    "https://starwars.fandom.com/wiki/Test1",
    "https://starwars.fandom.com/wiki/Test2",
    "https://starwars.fandom.com/wiki/Test3",
    "https://starwars.fandom.com/wiki/Test4",
    "https://starwars.fandom.com/wiki/Test5"
]

@pytest.fixture
def checkpoint_dir(tmp_path):
    """Create a temporary directory for checkpoints."""
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    yield checkpoint_dir
    # Cleanup
    if checkpoint_dir.exists():
        shutil.rmtree(checkpoint_dir)

@pytest.fixture
def batch_processor(checkpoint_dir):
    """Create a BatchProcessor instance for testing."""
    return BatchProcessor(
        urls=TEST_URLS,
        num_workers=2,
        batch_size=2,
        checkpoint_dir=str(checkpoint_dir),
        requests_per_minute=60
    )

class TestRateLimiter:
    """Tests for the RateLimiter class."""
    
    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test that rate limiting properly spaces requests."""
        limiter = RateLimiter(requests_per_minute=60)  # 1 request per second
        
        # Record timestamps of 5 requests
        timestamps = []
        for _ in range(5):
            await limiter.acquire()
            timestamps.append(datetime.now())
        
        # Check that requests are properly spaced
        for i in range(1, len(timestamps)):
            time_diff = (timestamps[i] - timestamps[i-1]).total_seconds()
            assert time_diff >= 0.9  # Allow small margin for processing time
    
    @pytest.mark.asyncio
    async def test_burst_prevention(self):
        """Test that the rate limiter prevents request bursts."""
        limiter = RateLimiter(requests_per_minute=30)  # 2 seconds between requests
        
        # Try to make requests in rapid succession
        start_time = datetime.now()
        for _ in range(3):
            await limiter.acquire()
        end_time = datetime.now()
        
        # Check that it took at least 4 seconds (2 delays of 2 seconds)
        assert (end_time - start_time).total_seconds() >= 4.0

class TestBatchProgress:
    """Tests for the BatchProgress class."""
    
    def test_progress_serialization(self):
        """Test that progress can be properly serialized and deserialized."""
        progress = BatchProgress(
            total_urls=10,
            processed_urls=5,
            failed_urls=["url1", "url2"],
            current_batch=2,
            start_time=datetime.now(),
            last_checkpoint=datetime.now(),
            checkpoint_file="test.json"
        )
        
        # Convert to dict and back
        progress_dict = progress.to_dict()
        restored_progress = BatchProgress.from_dict(progress_dict, "test.json")
        
        # Check that all fields match
        assert progress.total_urls == restored_progress.total_urls
        assert progress.processed_urls == restored_progress.processed_urls
        assert progress.failed_urls == restored_progress.failed_urls
        assert progress.current_batch == restored_progress.current_batch
        assert abs((progress.start_time - restored_progress.start_time).total_seconds()) < 1
        assert abs((progress.last_checkpoint - restored_progress.last_checkpoint).total_seconds()) < 1

class TestBatchProcessor:
    """Tests for the BatchProcessor class."""
    
    @pytest.mark.asyncio
    async def test_basic_processing(self, batch_processor):
        """Test that basic URL processing works."""
        success = await batch_processor.run()
        assert success
        assert batch_processor.progress.processed_urls == len(TEST_URLS)
        assert len(batch_processor.progress.failed_urls) == 0
    
    @pytest.mark.asyncio
    async def test_checkpointing(self, batch_processor):
        """Test that checkpoints are created and can be loaded."""
        # Start processing
        await batch_processor.run()
        
        # Verify checkpoint file exists and contains correct data
        assert os.path.exists(batch_processor.progress.checkpoint_file)
        with open(batch_processor.progress.checkpoint_file, 'r') as f:
            checkpoint_data = json.load(f)
            assert checkpoint_data["processed_urls"] == len(TEST_URLS)
    
    @pytest.mark.asyncio
    async def test_resumability(self, checkpoint_dir):
        """Test that processing can be resumed from a checkpoint."""
        # Create a checkpoint file
        checkpoint_file = os.path.join(str(checkpoint_dir), "test_checkpoint.json")
        checkpoint_data = {
            "total_urls": len(TEST_URLS),
            "processed_urls": 2,
            "failed_urls": [],
            "current_batch": 1,
            "start_time": datetime.now().isoformat(),
            "last_checkpoint": datetime.now().isoformat()
        }
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f)
        
        # Create new processor with same checkpoint directory
        processor = BatchProcessor(
            urls=TEST_URLS,
            checkpoint_dir=str(checkpoint_dir),
            num_workers=2,
            batch_size=2
        )
        
        # Run processing
        await processor.run()
        
        # Verify all URLs were processed
        assert processor.progress.processed_urls == len(TEST_URLS)
    
    @pytest.mark.asyncio
    async def test_error_handling(self, batch_processor):
        """Test that errors in URL processing are properly handled."""
        # Modify process_url to simulate errors
        async def mock_process_url(url: str) -> bool:
            if "Test3" in url:  # Simulate failure for Test3
                raise Exception("Simulated error")
            await asyncio.sleep(0.1)  # Simulate processing
            return True
        
        batch_processor.process_url = mock_process_url
        
        # Run processing
        await batch_processor.run()
        
        # Verify error handling
        assert len(batch_processor.progress.failed_urls) == 1
        assert "Test3" in batch_processor.progress.failed_urls[0]
        assert batch_processor.progress.processed_urls == len(TEST_URLS) - 1
    
    @pytest.mark.asyncio
    async def test_worker_coordination(self, batch_processor):
        """Test that multiple workers coordinate properly."""
        processed_urls = []
        
        # Modify process_url to track processed URLs
        async def mock_process_url(url: str) -> bool:
            await asyncio.sleep(0.1)  # Simulate processing
            processed_urls.append(url)
            return True
        
        batch_processor.process_url = mock_process_url
        
        # Run processing
        await batch_processor.run()
        
        # Verify all URLs were processed exactly once
        assert len(processed_urls) == len(TEST_URLS)
        assert set(processed_urls) == set(TEST_URLS)
    
    @pytest.mark.asyncio
    async def test_batch_size_checkpointing(self, batch_processor):
        """Test that checkpoints are created at correct batch intervals."""
        checkpoint_times = []
        
        # Modify save_checkpoint to track when it's called
        async def mock_save_checkpoint():
            checkpoint_times.append(datetime.now())
            await batch_processor.save_checkpoint()
        
        batch_processor.save_checkpoint = mock_save_checkpoint
        
        # Run processing
        await batch_processor.run()
        
        # Verify number of checkpoints
        expected_checkpoints = (len(TEST_URLS) // batch_processor.batch_size) + 1  # +1 for final checkpoint
        assert len(checkpoint_times) == expected_checkpoints

if __name__ == "__main__":
    pytest.main([__file__]) 