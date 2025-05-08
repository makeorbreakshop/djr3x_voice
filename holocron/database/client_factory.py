"""
Centralized Supabase client factory with singleton pattern and robust error handling.
This module provides a standardized way to create and manage Supabase client instances
across the application.
"""

import os
import logging
import time
from typing import Optional, Dict, Any, Callable
from functools import wraps
import pkg_resources
import httpx
from supabase import create_client, Client
from supabase.client import ClientOptions
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def _with_retry(func: Callable) -> Callable:
    """Decorator to add retry logic to client operations."""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        last_error = None
        for attempt in range(self._retry_attempts):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self._retry_attempts - 1:
                    delay = self._retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(
                        f"Attempt {attempt + 1} failed. Retrying in {delay} seconds. "
                        f"Error: {str(e)}"
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"All retry attempts failed. Last error: {str(last_error)}")
                raise last_error
        return None  # Should never reach here
    return wrapper

class SupabaseClientFactory:
    """
    Singleton factory for creating and managing Supabase client instances.
    Includes version detection, connection pooling, and retry logic.
    """
    _instance = None
    _client: Optional[Client] = None
    _retry_attempts = 3
    _retry_delay = 1  # seconds
    _connection_pool: Dict[str, Client] = {}
    _max_pool_size = 5

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SupabaseClientFactory, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        # Only initialize once
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._connection_pool = {}

    def _get_credentials(self) -> tuple[str, str]:
        """Get Supabase credentials from environment variables."""
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

        if not url or not key:
            raise ValueError(
                "Missing Supabase credentials. Please set SUPABASE_URL and "
                "SUPABASE_KEY or SUPABASE_SERVICE_ROLE_KEY environment variables."
            )
        return url, key

    @_with_retry
    def get_client(self, pool_key: str = "default") -> Client:
        """
        Get a Supabase client instance from the connection pool.
        Creates a new client if none exists for the given pool key.
        
        Args:
            pool_key: Identifier for the client in the connection pool
            
        Returns:
            Supabase Client instance
        """
        # Check if client exists in pool
        if pool_key in self._connection_pool:
            logger.debug(f"Returning existing client from pool: {pool_key}")
            return self._connection_pool[pool_key]

        # Check pool size
        if len(self._connection_pool) >= self._max_pool_size:
            raise RuntimeError(
                f"Connection pool limit reached (max: {self._max_pool_size})"
            )

        # Create new client with updated options
        url, key = self._get_credentials()
        options = ClientOptions(
            schema="public",
            auto_refresh_token=False,
            persist_session=False,
            postgrest_client_timeout=60  # Increase timeout to 60 seconds
        )
        client = create_client(url, key, options=options)
        self._connection_pool[pool_key] = client
        logger.info(f"Created new client in pool: {pool_key}")
        return client

    def close_client(self, pool_key: str = "default") -> None:
        """
        Close a specific client connection and remove it from the pool.
        
        Args:
            pool_key: Identifier for the client to close
        """
        if pool_key in self._connection_pool:
            client = self._connection_pool[pool_key]
            # Remove from pool
            self._connection_pool.pop(pool_key)
            logger.info(f"Removed client from pool: {pool_key}")

    def close_all(self) -> None:
        """Close all client connections and clear the pool."""
        self._connection_pool.clear()
        logger.info("Cleared all clients from connection pool")

# Create a default instance
default_factory = SupabaseClientFactory() 