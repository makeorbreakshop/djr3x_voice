#!/usr/bin/env python3
"""
DJ R3X - Lights & Voice MVP Launcher

Simple wrapper script to run the MVP application.
"""

import os
import sys
import subprocess
from dotenv import load_dotenv

def check_environment():
    """Check if environment is properly set up."""
    # Load environment variables
    load_dotenv()
    
    # Required environment variables
    required_vars = ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"]
    missing_vars = []
    
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)
    
    # Check for Python dependencies
    try:
        # Try importing a key dependency
        import pyee
        import asyncio
    except ImportError as e:
        print(f"Error: Missing dependencies: {e}")
        print("Please install required dependencies with: pip install -r requirements.txt")
        return False, []
    
    return True, missing_vars

def main():
    """Main entry point."""
    print("DJ R3X - Lights & Voice MVP Launcher")
    print("====================================")
    
    env_ok, missing_vars = check_environment()
    if not env_ok:
        sys.exit(1)
    
    # Build command with any passed arguments
    cmd = [sys.executable, "src/main.py"]
    
    # If API keys are missing, run in test mode
    if missing_vars:
        print("Warning: Missing API keys. Running in test mode.")
        for var in missing_vars:
            print(f"  - {var}")
        print()
        
        # Add test flag
        cmd.append("--test")
    else:
        print("Environment checks passed, starting application...\n")
        
    # Add any user arguments
    if len(sys.argv) > 1:
        cmd.extend(sys.argv[1:])
    
    # Run the DJ-R3X application
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nApplication terminated by user")
    except Exception as e:
        print(f"Error running application: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 