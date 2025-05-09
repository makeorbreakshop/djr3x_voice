"""
Debug OpenAI client initialization with stack trace inspection.
"""

import os
import sys
import traceback
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get API key
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("Error: OPENAI_API_KEY environment variable not found")
    sys.exit(1)

print("OpenAI Stack Trace Debug:")
print("------------------------")

# First attempt to import OpenAI
try:
    import openai
    print(f"OpenAI package version: {openai.__version__}")
    print(f"OpenAI package path: {openai.__file__}")
except Exception as e:
    print(f"Error importing openai: {e}")
    sys.exit(1)
    
# Monkey patch OpenAI.__init__ to see what params are actually passed
from openai import OpenAI

orig_init = OpenAI.__init__
def debug_init(self, **kwargs):
    print(f"\nOpenAI.__init__ received kwargs: {list(kwargs.keys())}")
    if 'proxies' in kwargs:
        print(f"\nFound proxies param: {kwargs['proxies']}")
        print("\nStack trace where OpenAI is initialized:")
        traceback.print_stack()
    return orig_init(self, **kwargs)

OpenAI.__init__ = debug_init

# Now try to initialize
try:
    print("\nAttempting to initialize OpenAI client with just api_key...")
    client = OpenAI(api_key=api_key)
    print("Success! Client initialized.")
except Exception as e:
    print(f"\nError during initialization: {e}")
    print("\nFull traceback:")
    traceback.print_exc()

print("\nDebug complete.") 