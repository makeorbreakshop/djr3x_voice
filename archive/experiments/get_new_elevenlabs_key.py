#!/usr/bin/env python3
"""
Script to help get a new ElevenLabs API key
"""

import getpass
import requests

# Instructions for the user
print("ElevenLabs API Key Helper")
print("-----------------------")
print("1. Go to https://elevenlabs.io/app")
print("2. Click on your profile icon in the top right")
print("3. Select 'Profile' or 'API Key'")
print("4. Click the 🔑 icon next to one of your API keys")
print("5. Copy the API key and paste it below")
print()

# Get the API key without echoing it to the terminal or persisting it to disk.
api_key = getpass.getpass("Enter your ElevenLabs API key: ").strip()
print("\nAPI key received securely.")

# Test the API key
print("\nTesting your API key...")
url = "https://api.elevenlabs.io/v1/user"
headers = {
    "Accept": "application/json",
    "xi-api-key": api_key
}

try:
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print("\n✅ API key is valid! User info retrieved successfully.")
        user_info = response.json()
        print(f"Subscription: {user_info.get('subscription', {}).get('tier', 'Unknown')}")
        print(f"Character quota: {user_info.get('subscription', {}).get('character_count', 'Unknown')} / {user_info.get('subscription', {}).get('character_limit', 'Unknown')}")
        
        print("The key was validated but was not stored. Add it to your secret manager separately.")
    else:
        print(f"❌ Error: Status code {response.status_code}")
except Exception as e:
    print(f"❌ Unable to validate the key: {type(e).__name__}")

print("\nDone! You can now use this API key in your application.")
