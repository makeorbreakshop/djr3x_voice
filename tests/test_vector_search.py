"""
Test suite for the vector search module.
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from typing import List, Dict, Any

from holocron.database.vector_search import VectorSearch, VectorSearchResult

# Test data
TEST_EMBEDDING_DIM = 1536
TEST_TABLE = "test_holocron_knowledge"

@pytest.fixture
def mock_client():
    """Fixture to provide a mock Supabase client."""
    client = MagicMock()
    client.rpc = MagicMock()
    client.rpc.return_value = MagicMock()
    client.rpc.return_value.execute = MagicMock()
    
    client.query = MagicMock()
    client.query.return_value = MagicMock()
    client.query.return_value.execute = MagicMock()
    
    client.table = MagicMock()
    client.table.return_value = MagicMock()
    client.table.return_value.select = MagicMock()
    client.table.return_value.select.return_value = MagicMock()
    client.table.return_value.select.return_value.limit = MagicMock()
    client.table.return_value.select.return_value.limit.return_value = MagicMock()
    client.table.return_value.select.return_value.limit.return_value.execute = MagicMock()
    
    # Create factory mock that returns our client
    with patch('holocron.database.client_factory.default_factory') as mock_factory:
        mock_factory.get_client.return_value = client
        yield client

@pytest.fixture
def vector_search(mock_client):
    """Fixture to provide a VectorSearch instance with mocked client."""
    vector_search = VectorSearch(
        table_name=TEST_TABLE,
        embedding_dimension=TEST_EMBEDDING_DIM
    )
    # Inject the mock client directly to bypass client creation
    vector_search._client = mock_client
    return vector_search

@pytest.fixture
def sample_embedding():
    """Fixture to provide a sample embedding vector."""
    return np.random.rand(TEST_EMBEDDING_DIM)

@pytest.fixture
def sample_results():
    """Fixture to provide sample search results."""
    return [
        {
            'id': 1,
            'content': 'Test content 1',
            'metadata': {'type': 'test'},
            'similarity': 0.95
        },
        {
            'id': 2,
            'content': 'Test content 2',
            'metadata': {'type': 'test'},
            'similarity': 0.85
        }
    ]

def test_vector_search_result_creation():
    """Test VectorSearchResult object creation and methods."""
    result = VectorSearchResult(
        id=1,
        content='test',
        metadata={'type': 'test'},
        similarity=0.95
    )
    
    assert result.id == 1
    assert result.content == 'test'
    assert result.metadata == {'type': 'test'}
    assert result.similarity == 0.95
    
    # Test dictionary conversion
    result_dict = result.to_dict()
    assert result_dict == {
        'id': 1,
        'content': 'test',
        'metadata': {'type': 'test'},
        'similarity': 0.95
    }

def test_vector_search_result_from_rpc():
    """Test VectorSearchResult creation from RPC result."""
    rpc_result = {
        'id': 1,
        'content': 'test',
        'metadata': {'type': 'test'},
        'similarity': 0.95
    }
    
    result = VectorSearchResult.from_rpc_result(rpc_result)
    assert result.id == rpc_result['id']
    assert result.content == rpc_result['content']
    assert result.metadata == rpc_result['metadata']
    assert result.similarity == rpc_result['similarity']

def test_vector_search_result_from_sql():
    """Test VectorSearchResult creation from SQL result."""
    sql_result = {
        'id': 1,
        'content': 'test',
        'metadata': {'type': 'test'},
        'similarity': 0.95
    }
    
    result = VectorSearchResult.from_sql_result(sql_result)
    assert result.id == sql_result['id']
    assert result.content == sql_result['content']
    assert result.metadata == sql_result['metadata']
    assert result.similarity == sql_result['similarity']

def test_vector_search_initialization(vector_search):
    """Test VectorSearch initialization."""
    assert vector_search.table_name == TEST_TABLE
    assert vector_search.embedding_dimension == TEST_EMBEDDING_DIM
    assert vector_search._client is not None

def test_vector_search_client_property(vector_search, mock_client):
    """Test client property and connection pooling."""
    # First access should get client from factory
    client = vector_search.client
    assert client == mock_client
    assert vector_search._client == mock_client
    
    # Second access should use cached client
    client2 = vector_search.client
    assert client2 == mock_client
    mock_client.get_client.assert_called_once()

def test_embedding_validation(vector_search):
    """Test embedding validation and normalization."""
    # Test with list input
    embedding_list = [1.0] * TEST_EMBEDDING_DIM
    validated = vector_search._validate_embedding(embedding_list)
    assert isinstance(validated, np.ndarray)
    assert np.allclose(np.linalg.norm(validated), 1.0)
    
    # Test with numpy array input
    embedding_array = np.random.rand(TEST_EMBEDDING_DIM)
    validated = vector_search._validate_embedding(embedding_array)
    assert isinstance(validated, np.ndarray)
    assert np.allclose(np.linalg.norm(validated), 1.0)
    
    # Test incorrect dimension
    with pytest.raises(ValueError):
        vector_search._validate_embedding([1.0] * (TEST_EMBEDDING_DIM + 1))

def test_embedding_formatting(vector_search, sample_embedding):
    """Test embedding formatting for SQL queries."""
    formatted = vector_search._format_embedding(sample_embedding)
    assert isinstance(formatted, str)
    assert formatted.startswith('[')
    assert formatted.endswith(']')
    assert len(formatted.split(',')) == TEST_EMBEDDING_DIM

@pytest.mark.asyncio
async def test_search_rpc_success(vector_search, mock_client, sample_embedding, sample_results):
    """Test successful RPC search."""
    mock_response = MagicMock()
    mock_response.data = sample_results
    mock_client.rpc().execute.return_value = mock_response
    
    results = await vector_search._search_rpc(
        embedding=sample_embedding,
        limit=10,
        threshold=0.5
    )
    
    assert len(results) == len(sample_results)
    assert all(isinstance(r, VectorSearchResult) for r in results)
    assert results[0].similarity == sample_results[0]['similarity']

@pytest.mark.asyncio
async def test_search_sql_success(vector_search, mock_client, sample_embedding, sample_results):
    """Test successful SQL search."""
    mock_response = MagicMock()
    mock_response.data = sample_results
    mock_client.query().execute.return_value = mock_response
    
    results = await vector_search._search_sql(
        embedding=sample_embedding,
        limit=10,
        threshold=0.5
    )
    
    assert len(results) == len(sample_results)
    assert all(isinstance(r, VectorSearchResult) for r in results)
    assert results[0].similarity == sample_results[0]['similarity']

@pytest.mark.asyncio
async def test_search_basic_success(vector_search, mock_client, sample_embedding):
    """Test successful basic search."""
    mock_response = MagicMock()
    mock_response.data = [
        {
            'id': 1,
            'content': 'test',
            'metadata': {'type': 'test'},
            'embedding': sample_embedding.tolist()
        }
    ]
    mock_client.table().select().limit().execute.return_value = mock_response
    
    results = await vector_search._search_basic(
        embedding=sample_embedding,
        limit=10,
        threshold=0.0  # Low threshold to ensure match
    )
    
    assert len(results) == 1
    assert all(isinstance(r, VectorSearchResult) for r in results)

@pytest.mark.asyncio
async def test_search_with_metadata_filters(vector_search, mock_client, sample_embedding, sample_results):
    """Test search with metadata filters."""
    mock_response = MagicMock()
    mock_response.data = sample_results
    mock_client.rpc().execute.return_value = mock_response
    
    metadata_filters = {'type': 'test'}
    results = await vector_search.search(
        embedding=sample_embedding,
        metadata_filters=metadata_filters
    )
    
    assert len(results) == len(sample_results)
    mock_client.rpc.assert_called_with(
        'match_documents',
        {
            'query_embedding': vector_search._format_embedding(
                vector_search._validate_embedding(sample_embedding)
            ),
            'match_threshold': 0.5,
            'match_count': 10,
            'metadata_filters': '{"type": "test"}'
        }
    )

@pytest.mark.asyncio
async def test_search_fallback_behavior(vector_search, mock_client, sample_embedding):
    """Test search method fallback behavior."""
    # Make RPC fail
    mock_client.rpc().execute.side_effect = Exception("RPC failed")
    
    # Make SQL succeed
    mock_response = MagicMock()
    mock_response.data = [
        {
            'id': 1,
            'content': 'test',
            'metadata': {'type': 'test'},
            'similarity': 0.95
        }
    ]
    mock_client.query().execute.return_value = mock_response
    
    results = await vector_search.search(sample_embedding)
    
    assert len(results) == 1
    assert isinstance(results[0], VectorSearchResult)
    assert results[0].similarity == 0.95

@pytest.mark.asyncio
async def test_search_all_methods_fail(vector_search, mock_client, sample_embedding):
    """Test behavior when all search methods fail."""
    # Make all methods fail
    mock_client.rpc().execute.side_effect = Exception("RPC failed")
    mock_client.query().execute.side_effect = Exception("SQL failed")
    mock_client.table().select().limit().execute.side_effect = Exception("Basic failed")
    
    with pytest.raises(RuntimeError) as exc_info:
        await vector_search.search(sample_embedding)
    
    assert "All search methods failed" in str(exc_info.value)

def test_close_connection(vector_search, mock_client):
    """Test connection closing."""
    # Access client to initialize it
    _ = vector_search.client
    
    vector_search.close()
    assert vector_search._client is None
    mock_client.close_client.assert_called_once_with("vector_search") 