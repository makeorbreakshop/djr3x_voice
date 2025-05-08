"""
Common test fixtures and setup for all tests.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
import httpx

# Fix the proxy parameter issue in Supabase library
@pytest.fixture(autouse=True)
def patch_supabase_client():
    """
    Patch the GoTrue client's _http_client initialization to prevent 'proxy' parameter errors.
    This is needed because the current version of httpx Client doesn't accept 'proxy' parameter
    but the GoTrue code still tries to pass it.
    """
    original_client_init = httpx.Client.__init__
    
    def patched_init(self, *args, **kwargs):
        # Remove the 'proxy' parameter if present
        if 'proxy' in kwargs:
            del kwargs['proxy']
        return original_client_init(self, *args, **kwargs)
    
    with patch('httpx.Client.__init__', patched_init):
        yield 