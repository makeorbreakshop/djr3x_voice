#!/usr/bin/env python3
"""
Script to help get a new ElevenLabs API key
"""

import os
import sys
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

# Get API key from user
api_key = input("Enter your ElevenLabs API key: ").strip()
print(f"\nYou entered a key with length: {len(api_key)}")
print(f"First 4 characters: {api_key[:4]}")
print(f"Last 4 characters: {api_key[-4:]}")

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
        
        # Update the env.visible file
        update_env = input("\nDo you want to update your env.visible file with this key? (y/n): ").lower()
        if update_env == 'y':
            try:
                # Read the current env.visible file
                if os.path.exists('env.visible'):
                    with open('env.visible', 'r') as f:
                        env_content = f.read()
                    
                    # Replace the API key
                    if 'ELEVENLABS_API_KEY=' in env_content:
                        import re
                        new_content = re.sub(r'ELEVENLABS_API_KEY=.*', f'ELEVENLABS_API_KEY={api_key}', env_content)
                        
                        with open('env.visible', 'w') as f:
                            f.write(new_content)
                        
                        print("✅ env.visible updated successfully!")
                    else:
                        print("ELEVENLABS_API_KEY not found in env.visible")
                else:
                    print("env.visible file not found")
            except Exception as e:
                print(f"Error updating env.visible: {str(e)}")
    else:
        print(f"❌ Error: Status code {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"❌ Error: {str(e)}")

print("\nDone! You can now use this API key in your application.") 