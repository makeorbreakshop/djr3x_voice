#!/usr/bin/env python3
"""Direct test of DJ mode with timeline fixes."""

import asyncio
import subprocess
import time
import signal
import os

def test_dj():
    # Start DJ system in background
    print("Starting DJ-R3X system...")
    proc = subprocess.Popen(['dj-r3x'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Give it 8 seconds to fully start
    print("Waiting for system startup...")
    time.sleep(8)
    
    # Send DJ start command via echo to stdin
    print("Sending 'dj start' command...")
    
    try:
        # Send the command
        proc.stdin.write("dj start\n")
        proc.stdin.flush()
        
        # Wait a bit for processing
        time.sleep(5)
        
        # Check if process is still running
        if proc.poll() is None:
            print("System is still running after dj start - GOOD!")
            
            # Try to get some output
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
            
            # Look for timeline logs
            output = stdout + stderr
            if "TimelineExecutorService" in output:
                print("✅ TimelineExecutorService found in logs")
            if "Received PLAN_READY" in output:
                print("✅ PLAN_READY event received!")
            if "Processing plan" in output:
                print("✅ Plan processing started!")
            
            print("Last 1000 chars of output:")
            print(output[-1000:])
            
        else:
            print("❌ System crashed!")
            stdout, stderr = proc.communicate()
            print("STDOUT:", stdout[-500:])
            print("STDERR:", stderr[-500:])
            
    except Exception as e:
        print(f"Error testing: {e}")
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except:
            proc.kill()

if __name__ == "__main__":
    test_dj() 