#!/usr/bin/env python3
"""
CantinaOS Test Runner

This script provides a convenient way to run tests for the CantinaOS implementation.
It supports running unit tests, integration tests, and specific test modules with
various options for verbosity and coverage reporting.
"""

import os
import sys
import argparse
import subprocess
import time

def print_header(text):
    """Print a formatted header."""
    print("\n" + "=" * 80)
    print(f" {text} ".center(80, "="))
    print("=" * 80 + "\n")

def print_result(result, duration):
    """Print formatted test results."""
    if result == 0:
        print(f"\n✅ Tests completed successfully in {duration:.2f} seconds!\n")
    else:
        print(f"\n❌ Tests failed with exit code {result} after {duration:.2f} seconds.\n")
    return result

def run_tests(args):
    """Run the specified tests with pytest."""
    cmd = ["python", "-m", "pytest"]
    
    # Add verbosity
    if args.verbose:
        cmd.append("-v")
    
    # Add coverage options (don't use both pytest.ini and command line options)
    if args.coverage:
        # Skip coverage options from pytest.ini
        cmd.append("--no-cov")  # Disable any coverage from pytest.ini
        cmd.extend(["--cov=src"])
        
        if args.coverage_html:
            cmd.append("--cov-report=html")
        else:
            cmd.append("--cov-report=term")
    
    # Add specific test module or path
    if args.module:
        cmd.append(args.module)
    
    # Add any additional pytest arguments
    if args.pytest_args:
        cmd.extend(args.pytest_args.split())
    
    # Print command
    print(f"Running: {' '.join(cmd)}\n")
    
    # Run the tests and measure time
    start_time = time.time()
    result = subprocess.run(cmd)
    duration = time.time() - start_time
    
    return print_result(result.returncode, duration)

def setup_environment():
    """Set up the testing environment."""
    # Ensure we're running from the correct directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    # Add src to PYTHONPATH for imports
    sys.path.insert(0, os.path.join(script_dir, "src"))
    
    # Set environment variables for testing
    os.environ["CANTINA_OS_ENV"] = "test"
    
    # Check for virtual environment
    if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("⚠️  Warning: Not running in a virtual environment. Tests may affect system packages.")

def main():
    """Parse arguments and run tests."""
    parser = argparse.ArgumentParser(description="Run CantinaOS tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Increase verbosity")
    parser.add_argument("-c", "--coverage", action="store_true", help="Run with coverage")
    parser.add_argument("--coverage-html", action="store_true", help="Generate HTML coverage report")
    parser.add_argument("-m", "--module", help="Run specific test module or path")
    parser.add_argument("--unit", action="store_true", help="Run only unit tests")
    parser.add_argument("--integration", action="store_true", help="Run only integration tests")
    parser.add_argument("pytest_args", nargs="?", help="Additional pytest arguments")
    
    args = parser.parse_args()
    
    # Set up environment
    setup_environment()
    
    # Determine what tests to run
    if args.unit:
        print_header("Running Unit Tests")
        args.module = "tests/unit"
    elif args.integration:
        print_header("Running Integration Tests")
        args.module = "tests/integration"
    else:
        print_header("Running All Tests")
    
    # Run the tests
    return run_tests(args)

if __name__ == "__main__":
    sys.exit(main()) 