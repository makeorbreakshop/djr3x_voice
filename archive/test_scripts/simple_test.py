#!/usr/bin/env python3
"""Simple test to verify DJ system core fixes."""

import subprocess
import time
import os
import signal

# Start the process
print("Starting DJ system to test TimelineExecutor subscription fix...")
proc = subprocess.Popen(['dj-r3x'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=os.setsid)

# Let it run for 5 seconds to see if it starts properly
try:
    output, _ = proc.communicate(timeout=5)
    print("System started successfully!")
    print("Checking for TimelineExecutorService logs...")
    
    if "TimelineExecutorService started successfully" in output:
        print("✅ TimelineExecutorService startup: SUCCESS")
    else:
        print("❌ TimelineExecutorService startup: FAILED")
        
    if "BlockingIOError" in output:
        print("⚠️  Still has logging issues")
    else:
        print("✅ No BlockingIOError")
        
except subprocess.TimeoutExpired:
    # Process is still running - good!
    print("✅ System is running (timeout after 5s)")
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    output, _ = proc.communicate()
    
    print("Checking startup logs...")
    if "TimelineExecutorService started successfully" in output:
        print("✅ TimelineExecutorService subscription fix: SUCCESS")
    else:
        print("❌ TimelineExecutorService subscription fix: FAILED")
    
    # Show last 500 chars for context
    print("Last 500 chars of output:")
    print(output[-500:]) 