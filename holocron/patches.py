"""
Global patches for third-party libraries.
Import this module before creating any Supabase clients.
"""

import httpx
import logging

logger = logging.getLogger(__name__)

# Store the original init method
original_httpx_init = httpx.Client.__init__

# Create a patched version that removes the 'proxy' parameter
def patched_httpx_init(self, *args, **kwargs):
    """Remove 'proxy' parameter which is no longer supported in httpx."""
    if 'proxy' in kwargs:
        logger.debug("Removing unsupported 'proxy' parameter from httpx.Client")
        del kwargs['proxy']
    return original_httpx_init(self, *args, **kwargs)

# Apply the patch globally
httpx.Client.__init__ = patched_httpx_init

logger.info("Applied global httpx Client patch for proxy parameter") 