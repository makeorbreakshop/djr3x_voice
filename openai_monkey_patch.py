"""
Monkey patch for OpenAI client to fix proxies issues.

This script fixes the issue with the OpenAI client where it passes a 'proxies'
parameter to the httpx.Client constructor, which no longer accepts it.
"""

import inspect
import types
import httpx
from openai import OpenAI
import openai._base_client

# Print current versions
print(f"OpenAI SDK Version: {openai.__version__}")
print(f"httpx Version: {httpx.__version__}")

# Store original client init
original_init = openai._base_client.BaseClient.__init__

# Define patched init 
def patched_init(self, *args, **kwargs):
    # If 'proxies' in kwargs, remove it before calling original init
    if 'proxies' in kwargs:
        print(f"Removing 'proxies' from OpenAI client initialization")
        kwargs.pop('proxies')
    
    # Call original init with the cleaned kwargs
    return original_init(self, *args, **kwargs)

# Apply the patch
openai._base_client.BaseClient.__init__ = patched_init

print("OpenAI client successfully patched!")

# Test the patch
try:
    client = OpenAI(api_key="dummy_key")
    print("✅ OpenAI client initialization successful!")
except Exception as e:
    print(f"❌ Error: {e}") 