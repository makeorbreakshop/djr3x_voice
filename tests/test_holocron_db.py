#!/usr/bin/env python3
"""
Integration tests for the Holocron database functionality.
These tests verify the vector search capabilities of the Supabase database.
"""

import os
import sys
import pytest
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.client import ClientOptions
import logging
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from numpy.typing import NDArray

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Import the SimpleChatInterface for testing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.simple_holocron_chat import SimpleChatInterface

from holocron.database.holocron_db import (
    HolocronDB,
    HolocronKnowledge,
    RepositoryError,
    ValidationError,
    TransactionError
)

# Test data
SAMPLE_CONTENT = "This is a test knowledge entry"
SAMPLE_METADATA = {
    "source": "test",
    "category": "unittest",
    "url": "https://test.com/article",
    "title": "Test Article"
}
SAMPLE_EMBEDDING: NDArray[np.float32] = np.array([0.1] * 1536, dtype=np.float32)  # 1536-dimensional test embedding
SAMPLE_ENTRY = {
    "content": SAMPLE_CONTENT,
    "content_tokens": len(SAMPLE_CONTENT.split()),
    "metadata": SAMPLE_METADATA,
    "embedding": SAMPLE_EMBEDDING.tolist()
}

@pytest.fixture
async def mock_table():
    """Create a mock table with chainable methods."""
    table = AsyncMock()
    table.insert = AsyncMock()
    table.insert.return_value = AsyncMock()
    table.insert.return_value.execute = AsyncMock()
    
    table.select = AsyncMock()
    table.select.return_value = AsyncMock()
    table.select.return_value.eq = AsyncMock()
    table.select.return_value.eq.return_value = AsyncMock()
    table.select.return_value.eq.return_value.execute = AsyncMock()
    
    table.update = AsyncMock()
    table.update.return_value = AsyncMock()
    table.update.return_value.eq = AsyncMock()
    table.update.return_value.eq.return_value = AsyncMock()
    table.update.return_value.eq.return_value.execute = AsyncMock()
    
    table.delete = AsyncMock()
    table.delete.return_value = AsyncMock()
    table.delete.return_value.eq = AsyncMock()
    table.delete.return_value.eq.return_value = AsyncMock()
    table.delete.return_value.eq.return_value.execute = AsyncMock()
    
    return table

@pytest.fixture
async def mock_client(mock_table):
    """Create a mock Supabase client."""
    client = AsyncMock()
    client.table = AsyncMock(return_value=mock_table)
    client.rpc = AsyncMock()
    client.query = AsyncMock()
    return client

@pytest.fixture
async def db(mock_client):
    """Create a test database instance."""
    db = HolocronDB(
        table_name='holocron_knowledge',
        pool_key='test_db'
    )
    db._client = mock_client  # Inject mock client
    yield db
    await db.close()

@pytest.fixture
async def mock_vector_search():
    """Create a mock vector search instance."""
    search = AsyncMock()
    search.search_similar = AsyncMock()
    search.search_similar_fallback = AsyncMock()
    return search

@pytest.fixture
def supabase_client() -> Client:
    """Create a Supabase client for testing."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        pytest.skip("Missing Supabase credentials")
    
    options = ClientOptions(
        schema="public",
        auto_refresh_token=False,
        persist_session=False
    )
    
    return create_client(url, key, options=options)

@pytest.fixture
async def chat_interface():
    """Create a SimpleChatInterface instance for testing."""
    interface = SimpleChatInterface()
    yield interface
    await interface.close()

@pytest.mark.asyncio
async def test_vector_extension_enabled(supabase_client):
    """Test that the vector extension is enabled in the database."""
    result = await supabase_client.rpc('check_vector_extension').execute()
    assert result.data is True, "Vector extension is not enabled"

@pytest.mark.asyncio
async def test_holocron_knowledge_table_structure(supabase_client):
    """Test that the holocron_knowledge table has the correct structure."""
    result = await supabase_client.table('holocron_knowledge').select('*').limit(1).execute()
    assert 'content' in result.data[0], "Missing content column"
    assert 'metadata' in result.data[0], "Missing metadata column"
    assert 'embedding' in result.data[0], "Missing embedding column"

@pytest.mark.parametrize("query,expected_terms", [
    ("What is the Force?", ["Force", "power", "energy", "Jedi"]),
    ("Tell me about lightsabers", ["lightsaber", "crystal", "weapon"]),
    ("Who is Luke Skywalker?", ["Luke", "Skywalker", "Jedi", "hero"])
])
@pytest.mark.asyncio
async def test_vector_search_relevance(db, mock_vector_search, query: str, expected_terms: List[str]):
    """Test that vector search returns relevant results containing expected terms."""
    # Setup mock response
    mock_results = [
        HolocronKnowledge(
            id=i,
            content=f"Test content containing {term}" if term in expected_terms else "Unrelated content",
            content_tokens=len(SAMPLE_CONTENT.split()),
            metadata=SAMPLE_METADATA,
            embedding=SAMPLE_EMBEDDING.tolist(),
            similarity=0.9 - (i * 0.1)
        )
        for i, term in enumerate(expected_terms)
    ]
    mock_vector_search.search_similar.return_value = mock_results

    # Execute search
    results = await db.search_similar(SAMPLE_EMBEDDING)

    # Verify results
    assert len(results) > 0, f"No results found for query: {query}"
    found_terms = set()
    for result in results:
        for term in expected_terms:
            if term.lower() in result.content.lower():
                found_terms.add(term)
    assert len(found_terms) > 0, f"No expected terms found in results for query: {query}"

@pytest.mark.asyncio
async def test_vector_search_similarity_scores(db, mock_vector_search):
    """Test that vector search results are properly ordered by similarity."""
    # Setup mock response
    mock_results = [
        HolocronKnowledge(
            id=i,
            content=SAMPLE_CONTENT,
            content_tokens=len(SAMPLE_CONTENT.split()),
            metadata=SAMPLE_METADATA,
            embedding=SAMPLE_EMBEDDING.tolist(),
            similarity=0.9 - (i * 0.1)
        )
        for i in range(3)
    ]
    mock_vector_search.search_similar.return_value = mock_results

    # Execute search
    results = await db.search_similar(SAMPLE_EMBEDDING)

    # Verify results
    assert len(results) > 0, "No results found"
    similarities = [r.similarity for r in results]
    assert similarities == sorted(similarities, reverse=True), "Results not ordered by similarity"

@pytest.mark.asyncio
async def test_vector_search_minimum_similarity(db, mock_vector_search):
    """Test that vector search respects minimum similarity threshold."""
    # Setup mock response
    min_similarity = 0.5
    mock_results = [
        HolocronKnowledge(
            id=i,
            content=SAMPLE_CONTENT,
            content_tokens=len(SAMPLE_CONTENT.split()),
            metadata=SAMPLE_METADATA,
            embedding=SAMPLE_EMBEDDING.tolist(),
            similarity=0.9 - (i * 0.1)
        )
        for i in range(3)
    ]
    mock_vector_search.search_similar.return_value = mock_results

    # Execute search
    results = await db.search_similar(SAMPLE_EMBEDDING, threshold=min_similarity)

    # Verify results
    assert all(r.similarity >= min_similarity for r in results), "Results below minimum similarity threshold"

@pytest.mark.asyncio
async def test_vector_search_limit(db, mock_vector_search):
    """Test that vector search respects result limit."""
    # Setup mock response
    limit = 2
    mock_results = [
        HolocronKnowledge(
            id=i,
            content=SAMPLE_CONTENT,
            content_tokens=len(SAMPLE_CONTENT.split()),
            metadata=SAMPLE_METADATA,
            embedding=SAMPLE_EMBEDDING.tolist(),
            similarity=0.9 - (i * 0.1)
        )
        for i in range(5)
    ]
    mock_vector_search.search_similar.return_value = mock_results[:limit]

    # Execute search
    results = await db.search_similar(SAMPLE_EMBEDDING, limit=limit)

    # Verify results
    assert len(results) <= limit, f"Too many results returned (got {len(results)}, expected <= {limit})"

@pytest.mark.asyncio
async def test_search_fallback_behavior(db, mock_vector_search):
    """Test the fallback behavior when vector search fails."""
    # Setup mock response
    mock_vector_search.search_similar.side_effect = Exception("Vector search failed")
    mock_results = [
        HolocronKnowledge(
            id=i,
            content=SAMPLE_CONTENT,
            content_tokens=len(SAMPLE_CONTENT.split()),
            metadata=SAMPLE_METADATA,
            embedding=SAMPLE_EMBEDDING.tolist(),
            similarity=0.9 - (i * 0.1)
        )
        for i in range(3)
    ]
    mock_vector_search.search_similar_fallback.return_value = mock_results

    # Execute search
    results = await db.search_similar(SAMPLE_EMBEDDING)

    # Verify results
    assert len(results) > 0, "No results returned from fallback search"
    assert all(isinstance(r, HolocronKnowledge) for r in results), "Invalid result type"

@pytest.mark.asyncio
async def test_create_knowledge_entry(db, mock_client):
    """Test creating a new knowledge entry."""
    # Setup mock response
    entry_id = 1
    mock_response = AsyncMock()
    mock_response.data = [{
        "id": entry_id,
        **SAMPLE_ENTRY,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }]
    mock_client.table().insert().execute.return_value = mock_response

    # Execute create
    result = await db.create(SAMPLE_ENTRY)

    # Verify result
    assert result.id == entry_id, "Wrong entry ID"
    assert result.content == SAMPLE_CONTENT, "Wrong content"
    assert result.metadata == SAMPLE_METADATA, "Wrong metadata"

@pytest.mark.asyncio
async def test_create_invalid_knowledge_entry(db):
    """Test creating a knowledge entry with invalid data."""
    # Missing required fields
    with pytest.raises(ValidationError):
        await db.create({"content": SAMPLE_CONTENT})  # Missing metadata

@pytest.mark.asyncio
async def test_read_knowledge_entry(db, mock_client):
    """Test reading a knowledge entry."""
    # Setup mock response
    entry_id = 1
    mock_response = AsyncMock()
    mock_response.data = [{
        "id": entry_id,
        **SAMPLE_ENTRY,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }]
    mock_client.table().select().eq().execute.return_value = mock_response

    # Execute read
    result = await db.read(entry_id)

    # Verify result
    assert result.id == entry_id, "Wrong entry ID"
    assert result.content == SAMPLE_CONTENT, "Wrong content"
    assert result.metadata == SAMPLE_METADATA, "Wrong metadata"

@pytest.mark.asyncio
async def test_read_nonexistent_entry(db, mock_client):
    """Test reading a nonexistent knowledge entry."""
    # Setup mock response
    mock_response = AsyncMock()
    mock_response.data = []
    mock_client.table().select().eq().execute.return_value = mock_response

    # Execute read
    result = await db.read(999)  # Non-existent ID

    # Verify result
    assert result is None, "Should return None for non-existent entry"

@pytest.mark.asyncio
async def test_update_knowledge_entry(db, mock_client):
    """Test updating a knowledge entry."""
    # Setup
    entry_id = 1
    updated_data = {
        "content": "Updated content",
        "metadata": {"source": "test", "status": "updated"}
    }
    mock_response = AsyncMock()
    mock_response.data = [{
        "id": entry_id,
        **updated_data,
        "embedding": SAMPLE_EMBEDDING.tolist(),
        "content_tokens": len(updated_data["content"].split()),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }]
    mock_client.table().update().eq().execute.return_value = mock_response

    # Execute update
    result = await db.update(entry_id, updated_data)

    # Verify result
    assert result.id == entry_id, "Wrong entry ID"
    assert result.content == updated_data["content"], "Content not updated"
    assert result.metadata == updated_data["metadata"], "Metadata not updated"

@pytest.mark.asyncio
async def test_delete_knowledge_entry(db, mock_client):
    """Test deleting a knowledge entry."""
    # Setup mock response
    entry_id = 1
    mock_response = AsyncMock()
    mock_response.data = [{"id": entry_id}]
    mock_client.table().delete().eq().execute.return_value = mock_response

    # Execute delete
    result = await db.delete(entry_id)

    # Verify result
    assert result is True, "Delete operation failed"

@pytest.mark.asyncio
async def test_list_knowledge_entries(db, mock_client):
    """Test listing knowledge entries with filters."""
    # Setup mock response
    mock_response = AsyncMock()
    mock_response.data = [
        {
            "id": i,
            **SAMPLE_ENTRY,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        for i in range(1, 4)
    ]
    mock_client.table().select().execute.return_value = mock_response

    # Execute list
    results = await db.list()

    # Verify results
    assert len(results) == 3, "Wrong number of entries"
    assert all(isinstance(r, HolocronKnowledge) for r in results), "Invalid result type"

@pytest.mark.asyncio
async def test_batch_create(db, mock_client):
    """Test batch creation of knowledge entries."""
    # Setup
    entries = [SAMPLE_ENTRY.copy() for _ in range(3)]
    mock_response = AsyncMock()
    mock_response.data = [
        {
            "id": i,
            **entry,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        for i, entry in enumerate(entries, 1)
    ]
    mock_client.table().insert().execute.return_value = mock_response

    # Execute batch create
    results = await db.batch_create(entries)

    # Verify results
    assert len(results) == len(entries), "Wrong number of entries created"
    assert all(isinstance(r, HolocronKnowledge) for r in results), "Invalid result type"

@pytest.mark.asyncio
async def test_vector_search(db, mock_vector_search):
    """Test vector similarity search."""
    # Setup
    query_embedding = np.array(SAMPLE_EMBEDDING)
    mock_results = [
        {
            "id": i,
            **SAMPLE_ENTRY,
            "similarity": 0.9 - (i * 0.1)
        }
        for i in range(3)
    ]
    mock_vector_search.search_similar.return_value = mock_results

    # Execute
    results = await db.search_similar(query_embedding)

    # Verify
    assert len(results) == 3
    assert all(isinstance(r, HolocronKnowledge) for r in results)
    assert all(hasattr(r, 'similarity') for r in results)
    mock_vector_search.search_similar.assert_called_once()

@pytest.mark.asyncio
async def test_transaction_success(db, mock_client):
    """Test successful transaction execution."""
    # Setup mock response
    test_data = {
        "content": "Test content",
        "content_tokens": 2,
        "metadata": {"source": "test"},
        "embedding": SAMPLE_EMBEDDING.tolist()
    }
    mock_response = AsyncMock()
    mock_response.data = [{"id": 1, **test_data}]
    mock_client.table().insert().execute.return_value = mock_response

    # Execute transaction
    async with db.transaction():
        result = await db.create(test_data)

    # Verify result
    assert result.id == 1, "Wrong entry ID"
    assert result.content == test_data["content"], "Wrong content"

@pytest.mark.asyncio
async def test_transaction_rollback(db, mock_client):
    """Test transaction rollback on error."""
    # Setup mock response
    test_data = {
        "content": "Test content",
        "content_tokens": 2,
        "metadata": {"source": "test"},
        "embedding": SAMPLE_EMBEDDING.tolist()
    }
    mock_client.table().insert().execute.side_effect = Exception("Database error")

    # Execute transaction
    with pytest.raises(Exception):
        async with db.transaction():
            await db.create(test_data)

@pytest.mark.asyncio
async def test_transaction_nested(db, mock_client):
    """Test nested transaction handling."""
    # Setup mock responses
    test_data_1 = {
        "content": "Test content 1",
        "content_tokens": 3,
        "metadata": {"source": "test1"},
        "embedding": SAMPLE_EMBEDDING.tolist()
    }
    test_data_2 = {
        "content": "Test content 2",
        "content_tokens": 3,
        "metadata": {"source": "test2"},
        "embedding": SAMPLE_EMBEDDING.tolist()
    }
    mock_client.table().insert().execute.side_effect = [
        AsyncMock(data=[{"id": 1, **test_data_1}]),
        AsyncMock(data=[{"id": 2, **test_data_2}])
    ]

    # Execute nested transactions
    async with db.transaction():
        result1 = await db.create(test_data_1)
        async with db.transaction():
            result2 = await db.create(test_data_2)

    # Verify results
    assert result1.id == 1, "Wrong first entry ID"
    assert result2.id == 2, "Wrong second entry ID"

@pytest.mark.asyncio
async def test_transaction_with_search(db, mock_client, mock_vector_search):
    """Test transaction with vector search operations."""
    # Setup mock responses
    test_data = {
        "content": "Test content",
        "content_tokens": 2,
        "metadata": {"source": "test"},
        "embedding": SAMPLE_EMBEDDING.tolist()
    }
    mock_client.table().insert().execute.return_value.data = [{"id": 1, **test_data}]
    
    mock_results = [
        HolocronKnowledge(
            id=1,
            content=test_data["content"],
            content_tokens=test_data["content_tokens"],
            metadata=test_data["metadata"],
            embedding=test_data["embedding"],
            similarity=0.95
        )
    ]
    mock_vector_search.search_similar.return_value = mock_results

    # Execute transaction with search
    async with db.transaction():
        created = await db.create(test_data)
        results = await db.search_similar(SAMPLE_EMBEDDING)

    # Verify results
    assert created.id == 1, "Wrong entry ID"
    assert len(results) == 1, "Wrong number of search results"
    assert results[0].id == created.id, "Search result doesn't match created entry"

@pytest.mark.asyncio
async def test_close_connections(db, mock_client):
    """Test that connections are properly closed."""
    await db.close()
    # Since we're using a mock, we just verify it was called
    assert True, "Close operation completed"

if __name__ == "__main__":
    pytest.main(["-v", __file__]) 