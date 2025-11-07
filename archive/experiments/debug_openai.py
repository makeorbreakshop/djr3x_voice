"""
Debug OpenAI client initialization.
"""

import os
import inspect
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get OpenAI module details
print("OpenAI Package Inspection:")
print("------------------------")

try:
    import openai
    print(f"OpenAI package version: {openai.__version__}")
    print(f"OpenAI package location: {openai.__file__}")
    
    # Get OpenAI class
    from openai import OpenAI
    
    # Inspect OpenAI class
    print("\nInspecting OpenAI class initialization:")
    print(inspect.signature(OpenAI.__init__))
    
    # Print all parameters for OpenAI.__init__
    print("\nOpenAI.__init__ parameters:")
    for param_name, param in inspect.signature(OpenAI.__init__).parameters.items():
        print(f"- {param_name}: {param.default}")
    
    # Introspect the base client
    print("\nInspecting _base_client.py:")
    base_client_path = os.path.join(os.path.dirname(openai.__file__), "_base_client.py")
    if os.path.exists(base_client_path):
        with open(base_client_path, "r") as f:
            lines = f.readlines()
            # Look for proxies in the file
            proxies_lines = [line.strip() for line in lines if "proxies" in line]
            print(f"Found {len(proxies_lines)} lines containing 'proxies':")
            for i, line in enumerate(proxies_lines[:10]):  # Show max 10 lines
                print(f"{i+1}. {line}")
    
    # Check for monkey patching
    print("\nChecking for monkey patching:")
    orig_init = OpenAI.__init__
    print(f"Original __init__ id: {id(orig_init)}")
    print(f"Original __init__ module: {orig_init.__module__}")
    
except Exception as e:
    print(f"Error during inspection: {e}")

print("\nDebug complete.") 