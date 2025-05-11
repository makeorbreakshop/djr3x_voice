#!/usr/bin/env python3
"""
Runner script for Holocron Knowledge Export

This script automatically sets the required environment variables
from the .env file and runs the export_alternative.py script.
"""
import os
import sys
import subprocess

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Using existing environment variables.")

# Get Supabase URL and extract project ID for the DB host
supabase_url = os.environ.get("SUPABASE_URL")
if supabase_url:
    # Extract project ID from URL
    if "https://" in supabase_url:
        project_id = supabase_url.split("https://")[1].split(".")[0]
    else:
        project_id = supabase_url.split("//")[1].split(".")[0]
    
    # Set database connection variables
    os.environ["SUPABASE_DB_HOST"] = f"postgres.{project_id}.supabase.co"
    if not os.environ.get("SUPABASE_DB_USER"):
        os.environ["SUPABASE_DB_USER"] = "postgres"
    if not os.environ.get("SUPABASE_DB_NAME"):
        os.environ["SUPABASE_DB_NAME"] = "postgres"
    if not os.environ.get("SUPABASE_DB_PORT"):
        os.environ["SUPABASE_DB_PORT"] = "5432"

# Check if we have all the required variables
if not os.environ.get("SUPABASE_DB_HOST"):
    print("Error: Could not determine SUPABASE_DB_HOST")
    sys.exit(1)

if not os.environ.get("SUPABASE_DB_PASSWORD"):
    print("Error: SUPABASE_DB_PASSWORD not found in environment variables")
    sys.exit(1)

# Print connection details (without password)
print("Using the following database connection details:")
print(f"Host: {os.environ.get('SUPABASE_DB_HOST')}")
print(f"User: {os.environ.get('SUPABASE_DB_USER')}")
print(f"Database: {os.environ.get('SUPABASE_DB_NAME')}")
print(f"Port: {os.environ.get('SUPABASE_DB_PORT')}")

# Run the export script
print("\nStarting export...")
subprocess.run([sys.executable, "export_alternative.py"]) 