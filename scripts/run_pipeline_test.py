#!/usr/bin/env python3
"""
Modified Pipeline Runner for Testing

Runs the Wookieepedia pipeline directly without diagnostic checks:
1. Process XML test file
2. Generate vector embeddings 
3. Verify processing works correctly

Usage:
    python scripts/run_pipeline_test.py
"""

import os
import logging
import subprocess
from pathlib import Path
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
TEST_XML_FILE = "data/wookiepdia-dump/extracted/test_sample.xml"
PROCESSED_ARTICLES_DIR = "data/processed_articles_test"
VECTORS_DIR = "data/vectors_test"

def run_command(cmd: list, description: str) -> bool:
    """Run a shell command and log output."""
    logger.info(f"Running {description}...")
    logger.info(f"Command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            text=True,
            capture_output=True
        )
        
        logger.info(f"{description} completed successfully")
        logger.info(f"Output: {result.stdout}")
        
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"{description} failed with code {e.returncode}")
        logger.error(f"Error: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"{description} failed with error: {e}")
        return False

def run_test_pipeline():
    """Run a test of each pipeline component."""
    # Create directories
    Path(PROCESSED_ARTICLES_DIR).mkdir(parents=True, exist_ok=True)
    Path(VECTORS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Step 1: Process XML
    step1 = run_command(
        ["python", "scripts/process_wiki_dump.py", 
         TEST_XML_FILE, 
         PROCESSED_ARTICLES_DIR,
         "--batch-size", "5"],
        "XML processing"
    )
    
    if not step1:
        logger.error("XML processing failed")
        return False
        
    # Step 2: Generate vectors
    step2 = run_command(
        ["python", "scripts/create_vectors.py",
         "--input-dir", PROCESSED_ARTICLES_DIR,
         "--output-dir", VECTORS_DIR,
         "--batch-size", "5",
         "--workers", "1"],
        "Vector generation"
    )
    
    if not step2:
        logger.error("Vector generation failed")
        return False
        
    # Step 3: Check results
    article_files = list(Path(PROCESSED_ARTICLES_DIR).glob("**/*.json"))
    vector_files = list(Path(VECTORS_DIR).glob("*.parquet"))
    
    logger.info(f"Processing results: {len(article_files)} article files, {len(vector_files)} vector files")
    
    if len(article_files) > 0 and len(vector_files) > 0:
        logger.info("✅ SUCCESS: Full pipeline test passed")
        return True
    else:
        logger.error("❌ FAILURE: Pipeline did not produce expected outputs")
        return False

if __name__ == "__main__":
    success = run_test_pipeline()
    exit(0 if success else 1) 