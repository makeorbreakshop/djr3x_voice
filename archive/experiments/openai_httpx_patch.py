"""
Monkey patch for httpx.Client to fix proxies issues with OpenAI.

This script patches httpx.Client to accept and ignore the 'proxies' parameter,
which OpenAI 1.3.5 is trying to pass to it but httpx no longer accepts.
"""

import inspect
import types
import httpx
import openai
from openai import OpenAI

# Print current versions
print(f"OpenAI SDK Version: {openai.__version__}")
print(f"httpx Version: {httpx.__version__}")

# Store original httpx Client init
original_httpx_init = httpx.Client.__init__

# Create a patched version of httpx.Client.__init__ that ignores 'proxies'
def patched_httpx_init(self, *args, **kwargs):
    # Remove 'proxies' if present
    if 'proxies' in kwargs:
        print(f"Removing 'proxies' parameter from httpx.Client initialization")
        kwargs.pop('proxies')
    
    # Call original init with the cleaned kwargs
    return original_httpx_init(self, *args, **kwargs)

# Apply the patch to httpx.Client
httpx.Client.__init__ = patched_httpx_init

print("httpx.Client successfully patched to ignore 'proxies' parameter!")

# Test the patch with OpenAI client
try:
    client = OpenAI(api_key="dummy_key")
    print("✅ OpenAI client initialization successful!")
except Exception as e:
    print(f"❌ Error: {e}") 