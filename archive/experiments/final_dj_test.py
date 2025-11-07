#!/usr/bin/env python3
"""Final comprehensive test of DJ mode functionality."""

import subprocess
import time
import os
import signal
import sys

def test_dj_mode():
    print("🎵 COMPREHENSIVE DJ MODE TEST 🎵")
    print("=" * 50)
    
    # Start the process
    print("1. Starting DJ-R3X system...")
    proc = subprocess.Popen(['dj-r3x'], 
                           stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, 
                           stderr=subprocess.STDOUT, 
                           text=True, 
                           preexec_fn=os.setsid)
    
    try:
        # Give it time to start up
        print("2. Waiting for system startup (8 seconds)...")
        time.sleep(8)
        
        # Test 1: Send DJ start command
        print("3. Sending 'dj start' command...")
        proc.stdin.write("dj start\n")
        proc.stdin.flush()
        time.sleep(3)
        
        # Test 2: Send DJ next command
        print("4. Sending 'dj next' command...")
        proc.stdin.write("dj next\n")
        proc.stdin.flush()
        time.sleep(3)
        
        print("5. Testing complete! Stopping system...")
        
        # Stop the process
        proc.stdin.write("quit\n")
        proc.stdin.flush()
        
        # Wait a bit then force termination
        time.sleep(2)
        
    except Exception as e:
        print(f"Error during testing: {e}")
        
    finally:
        # Force cleanup
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            stdout, _ = proc.communicate(timeout=3)
        except:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                stdout = ""
            except:
                stdout = ""
    
    print("\n" + "=" * 50)
    print("🔍 ANALYZING RESULTS...")
    print("=" * 50)
    
    # Check for key indicators of success
    if stdout:
        checks = {
            "System Startup": "TimelineExecutorService started successfully" in stdout,
            "PLAN_READY Received": "Received PLAN_READY" in stdout,
            "Plan Processing": "Processing plan" in stdout,
            "Plan Execution": "Successfully started plan" in stdout,
            "Speech Playback": "Executing PlayCachedSpeechStep" in stdout,
            "No Critical Errors": "Error handling PLAN_READY" not in stdout
        }
        
        print("RESULTS:")
        for check, passed in checks.items():
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"  {check}: {status}")
            
        success_count = sum(checks.values())
        total_checks = len(checks)
        
        print(f"\nOVERALL: {success_count}/{total_checks} checks passed")
        
        if success_count >= 4:
            print("🎉 DJ MODE IS WORKING! 🎉")
        elif success_count >= 2:
            print("⚠️  DJ MODE PARTIALLY WORKING - Needs more fixes")
        else:
            print("❌ DJ MODE STILL BROKEN")
            
        # Show relevant log excerpts
        if "Received PLAN_READY" in stdout:
            print("\n📋 Timeline Plan Activity:")
            lines = stdout.split('\n')
            for line in lines:
                if any(keyword in line for keyword in ['PLAN_READY', 'Processing plan', 'Executing', 'Plan execution']):
                    print(f"  {line}")
    else:
        print("❌ No output captured - system may have crashed")
    
    print("\n" + "=" * 50)

if __name__ == "__main__":
    test_dj_mode() 