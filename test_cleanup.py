#!/usr/bin/env python3
"""
Resource Cleanup Test for DJ R3X Voice MVP

This script tests that all resources are properly cleaned up when the application shuts down.
It runs the application for a short time and then sends a shutdown signal.
"""

import os
import time
import signal
import subprocess
import psutil
import atexit

def find_zombie_processes():
    """Find any zombie processes."""
    zombies = []
    for proc in psutil.process_iter(['pid', 'name', 'status']):
        try:
            if proc.info['status'] == psutil.STATUS_ZOMBIE:
                zombies.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return zombies

def cleanup_at_exit():
    """Ensure all child processes are terminated at exit."""
    current_process = psutil.Process()
    children = current_process.children(recursive=True)
    
    for child in children:
        try:
            print(f"Terminating child process: {child.pid}")
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    
    # Give them time to terminate
    gone, alive = psutil.wait_procs(children, timeout=1)
    
    # Force kill if still alive
    for child in alive:
        try:
            print(f"Force killing child process: {child.pid}")
            child.kill()
        except psutil.NoSuchProcess:
            pass

def main():
    """Run the test."""
    # Register cleanup handler
    atexit.register(cleanup_at_exit)
    
    print("DJ R3X Voice MVP Resource Cleanup Test")
    print("======================================")
    
    # Start with a clean slate
    zombies_before = find_zombie_processes()
    if zombies_before:
        print(f"Warning: Found {len(zombies_before)} zombie processes before starting test.")
    
    print("Starting DJ R3X in test mode...")
    
    # Start the application in test mode
    cmd = ["python3", "run_r3x_mvp.py", "--test"]
    proc = subprocess.Popen(cmd)
    
    # Give it some time to start up
    print("Waiting for application to initialize...")
    time.sleep(5)
    
    # Record running processes
    print("Checking initial process state...")
    current_process = psutil.Process()
    children_before = current_process.children(recursive=True)
    print(f"Found {len(children_before)} child processes.")
    
    # Send shutdown signal
    print("Sending shutdown signal (CTRL+C)...")
    proc.send_signal(signal.SIGINT)
    
    # Wait for graceful shutdown
    print("Waiting for graceful shutdown...")
    try:
        proc.wait(timeout=10)  # 10 second timeout
        print(f"Process exited with code: {proc.returncode}")
    except subprocess.TimeoutExpired:
        print("ERROR: Process did not exit within timeout period!")
        proc.terminate()
        proc.wait(timeout=5)
    
    # Check for cleanup
    print("Checking for cleanup...")
    time.sleep(2)  # Give a bit more time for cleanup to complete
    
    # Check for zombie processes
    zombies_after = find_zombie_processes()
    new_zombies = len(zombies_after) - len(zombies_before)
    if new_zombies > 0:
        print(f"WARNING: Found {new_zombies} new zombie processes after shutdown.")
    else:
        print("No new zombie processes detected. Good!")
    
    # Check for remaining child processes
    children_after = current_process.children(recursive=True)
    remaining = len(children_after)
    if remaining > 0:
        print(f"WARNING: Found {remaining} child processes still running after shutdown.")
        for child in children_after:
            try:
                print(f"  - PID {child.pid}: {child.name()} ({child.status()})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    else:
        print("All child processes terminated properly. Good!")
    
    # Final verdict
    if new_zombies == 0 and remaining == 0:
        print("\nTEST PASSED: All resources appear to be cleaned up properly.")
        return 0
    else:
        print("\nTEST FAILED: Some resources were not cleaned up properly.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code) 