#!/usr/bin/env python3
"""
Comprehensive Test Suite for Pinecone Migration

This script tests all aspects of the Pinecone migration:
1. Vector format compatibility
2. Embedding generation and metadata handling
3. Batch processing and Parquet conversion
4. S3 integration
5. Pinecone import functionality
6. Search and filtering capabilities

Usage:
    python -m pytest tests/test_pinecone_migration.py -v
"""

import os
import sys
import pytest
import asyncio
import logging
import json
import pandas as pd
import numpy as np
from pinecone import Pinecone, ServerlessSpec
import boto3
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv

# Add project root to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.pinecone_url_processor import PineconeURLProcessor
from scripts.setup_s3_bucket import create_bucket, setup_bucket_policy
from scripts.monitor_pinecone_import import ImportMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test data
TEST_URLS = [
    "https://starwars.fandom.com/wiki/Star_Tours",
    "https://starwars.fandom.com/wiki/DJ_R-3X",
    "https://starwars.fandom.com/wiki/Oga%27s_Cantina"
]

@pytest.fixture(scope="session")
def test_namespace():
    """Provide a test namespace for Pinecone operations."""
    return "test_migration"

@pytest.fixture(scope="session")
def s3_test_bucket():
    """Set up and tear down a test S3 bucket."""
    bucket_name = "holocron-pinecone-test"
    region = "us-east-1"
    
    # Create bucket
    assert create_bucket(bucket_name, region)
    assert setup_bucket_policy(bucket_name)
    
    # Set environment variable
    os.environ['S3_BUCKET'] = bucket_name
    
    yield bucket_name
    
    # Cleanup bucket
    try:
        s3 = boto3.client('s3')
        
        # Delete all objects
        response = s3.list_objects_v2(Bucket=bucket_name)
        if 'Contents' in response:
            for obj in response['Contents']:
                s3.delete_object(Bucket=bucket_name, Key=obj['Key'])
                
        # Delete bucket
        s3.delete_bucket(Bucket=bucket_name)
        logger.info(f"Cleaned up test bucket: {bucket_name}")
        
    except Exception as e:
        logger.error(f"Error cleaning up test bucket: {e}")

@pytest.fixture(scope="session")
def pinecone_index():
    """Initialize Pinecone index for testing."""
    load_dotenv()
    
    # Initialize Pinecone
    pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
    
    index_name = os.getenv('PINECONE_INDEX_NAME', 'holocron-knowledge')
    
    # Create index if it doesn't exist
    if index_name not in pc.list_indexes().names():
        pc.create_index(
            name=index_name,
            dimension=1536,  # OpenAI embedding dimension
            metric='cosine',
            spec=ServerlessSpec(
                cloud='aws',
                region='us-west-2'
            )
        )
    
    index = pc.Index(index_name)
    
    yield index
    
    # Cleanup test namespace
    try:
        index.delete(
            filter={},  # Delete all vectors
            namespace='test_migration'
        )
        logger.info("Cleaned up test namespace")
    except Exception as e:
        logger.error(f"Error cleaning up test namespace: {e}")

@pytest.mark.asyncio
async def test_url_processing(test_namespace):
    """Test URL processing and vector generation."""
    processor = PineconeURLProcessor(
        batch_size=2,
        workers=2,
        namespace=test_namespace
    )
    
    # Process test URLs
    results = await processor.process_batch(TEST_URLS, 1, 1)
    
    # Verify results
    assert len(results) > 0, "Should process at least one URL"
    
    for result in results:
        # Check structure
        assert 'url' in result
        assert 'vectors' in result
        assert 'metadata' in result
        
        # Check vectors
        vectors = result['vectors']
        assert len(vectors) > 0, "Should generate at least one vector"
        
        for vector in vectors:
            # Check vector format
            assert 'id' in vector
            assert 'values' in vector
            assert 'metadata' in vector
            
            # Check vector dimensions
            assert len(vector['values']) == 1536, "Should be OpenAI embedding dimension"
            
            # Check metadata
            metadata = vector['metadata']
            assert 'content' in metadata
            assert 'url' in metadata
            assert 'title' in metadata
            assert 'priority' in metadata

@pytest.mark.asyncio
async def test_parquet_conversion(test_namespace):
    """Test Parquet file generation."""
    processor = PineconeURLProcessor(
        batch_size=2,
        workers=2,
        namespace=test_namespace
    )
    
    # Process and save vectors
    results = await processor.process_batch(TEST_URLS, 1, 1)
    parquet_file = processor.save_vectors_to_parquet(results, 1)
    
    # Verify file
    assert os.path.exists(parquet_file)
    
    # Read and verify contents
    df = pd.read_parquet(parquet_file)
    assert len(df) > 0, "Parquet file should contain vectors"
    
    # Check schema
    required_columns = ['id', 'values', 'metadata']
    for col in required_columns:
        assert col in df.columns, f"Missing required column: {col}"

@pytest.mark.asyncio
async def test_s3_integration(s3_test_bucket, test_namespace):
    """Test S3 bucket operations."""
    processor = PineconeURLProcessor(
        batch_size=2,
        workers=2,
        namespace=test_namespace
    )
    
    # Process and save vectors
    results = await processor.process_batch(TEST_URLS, 1, 1)
    parquet_file = processor.save_vectors_to_parquet(results, 1)
    
    # Upload to S3
    s3 = boto3.client('s3')
    key = f"{test_namespace}/batch_0001.parquet"
    
    s3.upload_file(parquet_file, s3_test_bucket, key)
    
    # Verify upload
    response = s3.list_objects_v2(
        Bucket=s3_test_bucket,
        Prefix=test_namespace
    )
    assert 'Contents' in response
    assert any(obj['Key'] == key for obj in response['Contents'])

@pytest.mark.asyncio
async def test_pinecone_import(pinecone_index, s3_test_bucket, test_namespace):
    """Test Pinecone import functionality."""
    processor = PineconeURLProcessor(
        batch_size=2,
        workers=2,
        namespace=test_namespace
    )
    
    # Process and save vectors
    results = await processor.process_batch(TEST_URLS, 1, 1)
    parquet_file = processor.save_vectors_to_parquet(results, 1)
    
    # Upload to S3
    s3 = boto3.client('s3')
    key = f"{test_namespace}/batch_0001.parquet"
    s3.upload_file(parquet_file, s3_test_bucket, key)
    
    # Import to Pinecone
    pinecone_index.import_from_s3(
        bucket_name=s3_test_bucket,
        key_prefix=test_namespace,
        namespace=test_namespace
    )
    
    # Wait for import to complete
    monitor = ImportMonitor(pinecone_index.name)
    monitor.total_expected_vectors = sum(len(r['vectors']) for r in results)
    await asyncio.sleep(5)  # Give time for import to start
    
    # Verify import
    stats = pinecone_index.describe_index_stats()
    assert test_namespace in stats.namespaces
    assert stats.namespaces[test_namespace].vector_count > 0

@pytest.mark.asyncio
async def test_search_functionality(pinecone_index, test_namespace):
    """Test search and filtering capabilities."""
    # Perform a search
    query = "What is Star Tours?"
    
    # Generate query embedding (reuse processor's method)
    processor = PineconeURLProcessor(namespace=test_namespace)
    query_embedding = await processor.data_processor.generate_embedding(query)
    
    # Search with metadata filter
    results = pinecone_index.query(
        vector=query_embedding,
        top_k=5,
        namespace=test_namespace,
        filter={
            "priority": {"$in": ["high", "medium"]}
        }
    )
    
    # Verify results
    assert len(results.matches) > 0, "Should return search results"
    
    for match in results.matches:
        # Check result structure
        assert hasattr(match, 'id')
        assert hasattr(match, 'score')
        assert hasattr(match, 'metadata')
        
        # Verify metadata
        metadata = match.metadata
        assert 'content' in metadata
        assert 'url' in metadata
        assert 'title' in metadata
        assert 'priority' in metadata
        
        # Check relevance
        assert match.score > 0.5, "Results should be reasonably relevant"

@pytest.mark.asyncio
async def test_incremental_processing():
    """Test incremental processing of new URLs."""
    processor = PineconeURLProcessor(
        batch_size=2,
        workers=2,
        namespace="test_incremental"
    )
    
    # Get initial unprocessed URLs
    urls = await processor.get_unprocessed_urls(limit=2)
    assert isinstance(urls, list), "Should return a list of URLs"
    
    if urls:
        # Process URLs
        results = await processor.process_batch(urls, 1, 1)
        assert len(results) > 0, "Should process URLs successfully"
        
        # Mark as processed
        await processor.mark_urls_processed([r['url'] for r in results])
        
        # Verify URLs are marked as processed
        new_urls = await processor.get_unprocessed_urls(limit=2)
        assert not any(url in urls for url in new_urls), "Processed URLs should not be returned"

def test_error_handling():
    """Test error handling in various scenarios."""
    # Test invalid S3 bucket
    with pytest.raises(ValueError):
        os.environ.pop('S3_BUCKET', None)  # Remove S3_BUCKET from env
        PineconeURLProcessor()
        
    # Test invalid namespace
    with pytest.raises(Exception):
        pinecone.init(
            api_key=os.getenv('PINECONE_API_KEY'),
            environment=os.getenv('PINECONE_ENVIRONMENT', 'us-west1-gcp')
        )
        index = pinecone.Index(os.getenv('PINECONE_INDEX_NAME'))
        index.delete(namespace="nonexistent_namespace") 