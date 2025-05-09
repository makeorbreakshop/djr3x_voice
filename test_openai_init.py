"""
Test OpenAI Client Initialization.

This script tests different ways of initializing the OpenAI client
to identify what's causing the 'proxies' parameter error.
"""

import os
import sys
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get API key
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("Error: OPENAI_API_KEY environment variable not found")
    sys.exit(1)

print("OpenAI SDK version test:")
print("------------------------")

# Test 1: Simple initialization with just api_key
try:
    print("Test 1: Initializing with just api_key")
    client = OpenAI(api_key=api_key)
    print("✅ Success: Client initialized with api_key only")
except Exception as e:
    print(f"❌ Error: {str(e)}")

# Try a simple API call to confirm it works
try:
    print("\nTest 2: Making a simple API call")
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Say hello!"}],
        max_tokens=10
    )
    print(f"✅ Success: Response: {response.choices[0].message.content}")
except Exception as e:
    print(f"❌ Error: {str(e)}")

print("\nTest complete.") 