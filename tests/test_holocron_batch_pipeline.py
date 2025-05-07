"""
Integration test for the complete Holocron batch processing pipeline.

This script tests the entire pipeline from URL collection to content processing,
using a small set of test articles to verify all components work together correctly.
"""

import os
import sys
import json
import asyncio
import logging
import pytest
from datetime import datetime
from typing import List, Dict, Any

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.holocron.wookieepedia_scraper import WookieepediaScraper
from src.holocron.data_processor import HolocronDataProcessor
from src.holocron.batch_processor import BatchProcessor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test data - specific articles we know should work
TEST_ARTICLES = [
    "https://starwars.fandom.com/wiki/RX-24",  # DJ R3X himself
    "https://starwars.fandom.com/wiki/Oga%27s_Cantina",  # His workplace
    "https://starwars.fandom.com/wiki/Star_Tours"  # His original job
]

@pytest.fixture
def test_dir(tmp_path):
    """Create test directories."""
    data_dir = tmp_path / "data"
    checkpoint_dir = data_dir / "checkpoints"
    data_dir.mkdir()
    checkpoint_dir.mkdir()
    return data_dir

@pytest.mark.asyncio
async def test_url_collection():
    """Test that we can collect URLs properly."""
    async with WookieepediaScraper() as scraper:
        # Test with a single known category
        urls = await scraper.get_category_articles("Star_Tours", limit=5)
        assert urls, "Should find some URLs in the Star Tours category"
        assert any("Star_Tours" in url for url in urls), "Should find Star Tours related articles"

@pytest.mark.asyncio
async def test_article_scraping():
    """Test that we can scrape individual articles correctly."""
    async with WookieepediaScraper() as scraper:
        # Test scraping RX-24's article
        article = await scraper.scrape_article(TEST_ARTICLES[0])
        assert article, "Should successfully scrape RX-24/R-3X article"
        assert any(name in article["title"] for name in ["RX-24", "R-3X"]), "Should have correct title (either RX-24 or R-3X)"
        assert article["content"], "Should have content"
        assert article["is_canonical"], "Should be marked as canonical"

@pytest.mark.asyncio
async def test_batch_processing(test_dir):
    """Test the complete batch processing pipeline."""
    # Initialize components
    processor = BatchProcessor(
        urls=TEST_ARTICLES,
        num_workers=2,
        batch_size=1,
        checkpoint_dir=str(test_dir / "checkpoints"),
        requests_per_minute=30
    )
    
    data_processor = HolocronDataProcessor()
    
    # Override process_url to use our actual processing logic
    async def process_url(url: str) -> bool:
        try:
            async with WookieepediaScraper() as scraper:
                content = await scraper.scrape_article(url)
            
            if content:
                success = await data_processor.process_and_upload([content])
                return success
            return False
        except Exception as e:
            logger.error(f"Failed to process URL {url}: {e}")
            return False
    
    processor.process_url = process_url
    
    # Run the batch processor
    success = await processor.run()
    assert success, "Batch processing should complete successfully"
    
    # Verify results
    assert processor.progress.processed_urls > 0, "Should process some URLs"
    assert len(processor.progress.failed_urls) == 0, "Should not have any failed URLs"
    
    # Verify checkpoints
    checkpoint_files = list((test_dir / "checkpoints").glob("*.json"))
    assert checkpoint_files, "Should create checkpoint files"
    
    # Load the last checkpoint
    latest_checkpoint = max(checkpoint_files, key=os.path.getctime)
    with open(latest_checkpoint, 'r') as f:
        checkpoint_data = json.load(f)
        assert checkpoint_data["processed_urls"] == len(TEST_ARTICLES), "Should process all test articles"

@pytest.mark.asyncio
async def test_error_recovery(test_dir):
    """Test that the system can recover from errors and resume processing."""
    # Create a checkpoint file indicating partial progress
    checkpoint_file = test_dir / "checkpoints" / "test_checkpoint.json"
    checkpoint_data = {
        "total_urls": len(TEST_ARTICLES),
        "processed_urls": 1,
        "failed_urls": [],
        "current_batch": 1,
        "start_time": datetime.now().isoformat(),
        "last_checkpoint": datetime.now().isoformat()
    }
    with open(checkpoint_file, 'w') as f:
        json.dump(checkpoint_data, f)
    
    # Initialize processor with same checkpoint directory
    processor = BatchProcessor(
        urls=TEST_ARTICLES,
        checkpoint_dir=str(test_dir / "checkpoints"),
        num_workers=2,
        batch_size=1
    )
    
    data_processor = HolocronDataProcessor()
    
    # Override process_url
    async def process_url(url: str) -> bool:
        try:
            async with WookieepediaScraper() as scraper:
                content = await scraper.scrape_article(url)
            
            if content:
                success = await data_processor.process_and_upload([content])
                return success
            return False
        except Exception as e:
            logger.error(f"Failed to process URL {url}: {e}")
            return False
    
    processor.process_url = process_url
    
    # Run the processor
    success = await processor.run()
    assert success, "Should complete successfully after resuming"
    assert processor.progress.processed_urls == len(TEST_ARTICLES), "Should complete all remaining articles"

@pytest.mark.asyncio
async def test_rate_limiting(test_dir):
    """Test that rate limiting is working correctly."""
    start_time = datetime.now()
    
    # Use a more restrictive rate limit for testing
    requests_per_minute = 10  # One request every 6 seconds
    processor = BatchProcessor(
        urls=TEST_ARTICLES,
        checkpoint_dir=str(test_dir / "checkpoints"),
        num_workers=2,
        batch_size=1,
        requests_per_minute=requests_per_minute
    )
    
    # Override process_url with a simple mock that tracks call times
    call_times = []
    async def mock_process_url(url: str) -> bool:
        call_times.append(datetime.now())
        return True
    
    processor.process_url = mock_process_url
    
    # Run the processor
    await processor.run()
    
    # Verify minimum time between requests
    min_delay = 60.0 / requests_per_minute  # 6 seconds between requests
    for i in range(1, len(call_times)):
        time_diff = (call_times[i] - call_times[i-1]).total_seconds()
        assert time_diff >= min_delay * 0.9, f"Request {i} came too soon after request {i-1}"
    
    # Verify total processing time
    end_time = datetime.now()
    processing_time = (end_time - start_time).total_seconds()
    expected_min_time = (len(TEST_ARTICLES) - 1) * min_delay
    assert processing_time >= expected_min_time, "Processing completed too quickly"

if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 