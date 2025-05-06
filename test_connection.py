#!/usr/bin/env python3
"""
Quick test script to verify Arduino eyes connection before running DJ-R3X
"""

import asyncio
from eyes_integration import EyesIntegration, VoiceState
import sys
from colorama import init, Fore, Style

# Initialize colorama
init()

async def test_connection():
    print(f"{Fore.CYAN}Testing DJ-R3X Eyes Connection...{Style.RESET_ALL}")
    print(f"{Fore.CYAN}================================{Style.RESET_ALL}")
    
    try:
        # Try to connect to Arduino
        eyes = EyesIntegration()
        if not await eyes.connect():
            print(f"{Fore.RED}❌ Failed to connect to Arduino!{Style.RESET_ALL}")
            print("\nPlease check:")
            print("1. Arduino is connected via USB")
            print("2. eyes.ino has been uploaded to Arduino")
            print(f"3. Serial port is correct (/dev/tty.usbmodem833301)")
            return False
            
        print(f"{Fore.GREEN}✅ Successfully connected to Arduino!{Style.RESET_ALL}")
        
        # Test basic states
        print("\nTesting eye states...")
        
        states = [
            (VoiceState.IDLE, "Idle state"),
            (VoiceState.LISTENING, "Listening state"),
            (VoiceState.PROCESSING, "Processing state"),
            (VoiceState.SPEAKING, "Speaking state (default)"),
        ]
        
        for state, desc in states:
            print(f"\nTesting {desc}...")
            if await eyes.set_voice_state(state):
                print(f"{Fore.GREEN}✅ Success{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}❌ Failed{Style.RESET_ALL}")
            await asyncio.sleep(2)
        
        # Test speaking patterns
        print("\nTesting speaking patterns...")
        for pattern in range(4):
            print(f"\nTesting pattern {pattern}...")
            if await eyes.start_speaking(pattern):
                print(f"{Fore.GREEN}✅ Success{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}❌ Failed{Style.RESET_ALL}")
            await asyncio.sleep(2)
        
        # Return to idle
        await eyes.set_voice_state(VoiceState.IDLE)
        
        print(f"\n{Fore.GREEN}✅ All tests completed successfully!{Style.RESET_ALL}")
        print("\nYou can now run run_rex.py to start the full system.")
        
        # Clean up
        await eyes.disconnect()
        return True
        
    except Exception as e:
        print(f"{Fore.RED}❌ Error during testing: {str(e)}{Style.RESET_ALL}")
        return False

if __name__ == "__main__":
    try:
        success = asyncio.run(test_connection())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Test interrupted by user{Style.RESET_ALL}")
        sys.exit(1)
    except Exception as e:
        print(f"{Fore.RED}Unexpected error: {str(e)}{Style.RESET_ALL}")
        sys.exit(1) 