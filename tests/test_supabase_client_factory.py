"""
Test suite for the Supabase client factory.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, ANY
import pkg_resources
from holocron.database.client_factory import SupabaseClientFactory
from supabase.client import ClientOptions

# Test data
TEST_SUPABASE_URL = "https://test.supabase.co"
TEST_SUPABASE_KEY = "test_key"

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton state before each test."""
    SupabaseClientFactory._instance = None
    SupabaseClientFactory._client = None
    SupabaseClientFactory._connection_pool = {}
    yield

@pytest.fixture
def mock_env_vars():
    """Fixture to set up test environment variables."""
    with patch.dict(os.environ, {
        "SUPABASE_URL": TEST_SUPABASE_URL,
        "SUPABASE_KEY": TEST_SUPABASE_KEY
    }):
        yield

@pytest.fixture
def mock_pkg_resources():
    """Fixture to mock pkg_resources for version checking."""
    with patch("holocron.database.client_factory.pkg_resources.get_distribution") as mock:
        mock.return_value = MagicMock(version="2.3.5")
        yield mock

@pytest.fixture
def mock_create_client():
    """Fixture to mock Supabase client creation."""
    with patch("holocron.database.client_factory.create_client") as mock:
        mock.return_value = MagicMock()
        yield mock

def test_singleton_pattern():
    """Test that the factory follows the singleton pattern."""
    factory1 = SupabaseClientFactory()
    factory2 = SupabaseClientFactory()
    assert factory1 is factory2

def test_version_verification(mock_pkg_resources):
    """Test Supabase version verification."""
    factory = SupabaseClientFactory()
    mock_pkg_resources.assert_called_once()

def test_version_mismatch(mock_pkg_resources):
    """Test handling of version mismatch."""
    mock_pkg_resources.return_value = MagicMock(version="2.3.4")
    with pytest.raises(ValueError) as exc_info:
        SupabaseClientFactory()
    assert "Incorrect Supabase version" in str(exc_info.value)

def test_missing_package(mock_pkg_resources):
    """Test handling of missing Supabase package."""
    mock_pkg_resources.side_effect = pkg_resources.DistributionNotFound()
    with pytest.raises(ImportError) as exc_info:
        SupabaseClientFactory()
    assert "Supabase package not found" in str(exc_info.value)

def test_get_client_with_env_vars(mock_env_vars, mock_create_client):
    """Test client creation with environment variables."""
    factory = SupabaseClientFactory()
    client = factory.get_client()
    
    # Check that create_client was called with the correct parameters
    mock_create_client.assert_called_once()
    args, kwargs = mock_create_client.call_args
    assert args[0] == TEST_SUPABASE_URL
    assert args[1] == TEST_SUPABASE_KEY
    assert "options" in kwargs
    assert isinstance(kwargs["options"], ClientOptions)
    assert kwargs["options"].schema == "public"
    assert kwargs["options"].auto_refresh_token is False
    assert kwargs["options"].persist_session is False
    
    assert client is mock_create_client.return_value

def test_get_client_missing_env_vars():
    """Test handling of missing environment variables."""
    with patch.dict(os.environ, clear=True):
        factory = SupabaseClientFactory()
        with pytest.raises(ValueError) as exc_info:
            factory.get_client()
        assert "Missing Supabase credentials" in str(exc_info.value)

def test_connection_pooling(mock_env_vars, mock_create_client):
    """Test connection pooling functionality."""
    factory = SupabaseClientFactory()
    
    # Configure mock to return different objects
    mock_client1 = MagicMock(name="client1")
    mock_client2 = MagicMock(name="client2")
    mock_create_client.side_effect = [mock_client1, mock_client2]
    
    # Get same client twice
    client1 = factory.get_client("test_pool")
    client2 = factory.get_client("test_pool")
    assert client1 is client2
    assert mock_create_client.call_count == 1
    
    # Get different client
    client3 = factory.get_client("other_pool")
    assert client3 is not client1
    assert mock_create_client.call_count == 2
    
    # Verify correct options were passed
    calls = mock_create_client.call_args_list
    for call in calls:
        _, kwargs = call
        assert "options" in kwargs
        assert isinstance(kwargs["options"], ClientOptions)
        assert kwargs["options"].schema == "public"

def test_pool_size_limit(mock_env_vars, mock_create_client):
    """Test connection pool size limit."""
    factory = SupabaseClientFactory()
    
    # Create max_pool_size + 1 clients
    for i in range(factory._max_pool_size):
        factory.get_client(f"pool_{i}")
    
    with pytest.raises(RuntimeError) as exc_info:
        factory.get_client("overflow_pool")
    assert "Connection pool limit reached" in str(exc_info.value)

def test_close_client(mock_env_vars, mock_create_client):
    """Test closing specific client connections."""
    factory = SupabaseClientFactory()
    
    # Create and close a client
    factory.get_client("test_pool")
    assert "test_pool" in factory._connection_pool
    
    factory.close_client("test_pool")
    assert "test_pool" not in factory._connection_pool

def test_close_all(mock_env_vars, mock_create_client):
    """Test closing all client connections."""
    factory = SupabaseClientFactory()
    
    # Create multiple clients
    factory.get_client("pool_1")
    factory.get_client("pool_2")
    assert len(factory._connection_pool) == 2
    
    factory.close_all()
    assert len(factory._connection_pool) == 0

@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_retry_logic(mock_env_vars, mock_create_client, attempts):
    """Test retry logic with different numbers of failures."""
    factory = SupabaseClientFactory()
    
    # Make create_client fail n-1 times then succeed
    side_effects = [RuntimeError("Connection failed")] * (attempts - 1) + [MagicMock()]
    mock_create_client.side_effect = side_effects
    
    client = factory.get_client()
    assert client is not None
    assert mock_create_client.call_count == attempts

def test_retry_exhaustion(mock_env_vars, mock_create_client):
    """Test handling of retry exhaustion."""
    factory = SupabaseClientFactory()
    
    # Make create_client always fail
    mock_create_client.side_effect = RuntimeError("Connection failed")
    
    with pytest.raises(RuntimeError) as exc_info:
        factory.get_client()
    assert mock_create_client.call_count == factory._retry_attempts
    assert "Connection failed" in str(exc_info.value) 