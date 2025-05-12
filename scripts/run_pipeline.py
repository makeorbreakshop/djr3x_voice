#!/usr/bin/env python3
"""
Wookieepedia Processing Pipeline Runner

This script runs the full Wookieepedia XML dump processing pipeline:
1. Process XML dump into individual JSON article files
2. Generate vector embeddings for processed articles
3. Upload vectors to Pinecone with URL tracking

Usage:
    python scripts/run_pipeline.py [options]
"""

import os
import sys
import argparse
import logging
import subprocess
import time
from pathlib import Path
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Constants
XML_DUMP_FILE = "data/wookiepedia-dump/dump.xml"
PROCESSED_ARTICLES_DIR = "data/processed_articles"
VECTORS_DIR = "data/vectors"

def run_command(cmd: list, description: str) -> bool:
    """
    Run a shell command and log output.
    
    Args:
        cmd: Command list to run
        description: Description of the command
        
    Returns:
        True if successful, False otherwise
    """
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
        logger.debug(f"Output: {result.stdout}")
        
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"{description} failed with code {e.returncode}")
        logger.error(f"Error: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"{description} failed with error: {e}")
        return False

def run_pipeline(xml_file: str, process_xml: bool, generate_vectors: bool, 
                upload_vectors: bool, reset_tracking: bool, batch_size: int,
                delay: float, workers: int, test_mode: bool) -> bool:
    """
    Run the full pipeline.
    
    Args:
        xml_file: Path to XML dump file
        process_xml: Whether to process XML
        generate_vectors: Whether to generate vectors
        upload_vectors: Whether to upload vectors
        reset_tracking: Whether to reset URL tracking
        batch_size: Batch size for processing
        delay: Delay between batches
        workers: Number of worker processes
        test_mode: Whether to run in test mode
        
    Returns:
        True if successful, False otherwise
    """
    # Step 0: Verify pipeline with diagnostic tool first
    logger.info("Verifying pipeline components...")
    if not run_command(
        ["python", "scripts/test_pipeline.py"],
        "Pipeline diagnostic"
    ):
        logger.error("Pipeline diagnostic failed. Fix issues before continuing.")
        return False
        
    # Step 1: Process XML dump (if enabled)
    if process_xml:
        logger.info("Starting XML processing step...")
        
        # Clear output directory if it exists
        if os.path.exists(PROCESSED_ARTICLES_DIR):
            logger.info(f"Clearing existing processed articles directory: {PROCESSED_ARTICLES_DIR}")
            # Don't delete the directory itself, just its contents
            for item in Path(PROCESSED_ARTICLES_DIR).glob("*"):
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    for subitem in item.glob("*"):
                        subitem.unlink()
                    item.rmdir()
        
        # Create output directory
        Path(PROCESSED_ARTICLES_DIR).mkdir(parents=True, exist_ok=True)
        
        # Run XML processor
        if not run_command(
            ["python", "scripts/process_wiki_dump.py", 
             xml_file,  # Input file
             PROCESSED_ARTICLES_DIR,  # Output directory
             "--batch-size", str(batch_size)],
            "XML processing"
        ):
            logger.error("XML processing failed. Check logs for details.")
            return False
            
        logger.info("XML processing completed successfully")
    
    # Step 2: Generate vectors (if enabled)
    if generate_vectors:
        logger.info("Starting vector generation step...")
        
        # Create output directory
        Path(VECTORS_DIR).mkdir(parents=True, exist_ok=True)
        
        # Run vector generator
        if not run_command(
            ["python", "scripts/create_vectors.py", "--input-dir", PROCESSED_ARTICLES_DIR, 
             "--output-dir", VECTORS_DIR, "--batch-size", str(batch_size), "--workers", str(workers)],
            "Vector generation"
        ):
            logger.error("Vector generation failed. Check logs for details.")
            return False
            
        logger.info("Vector generation completed successfully")
    
    # Step 3: Upload vectors to Pinecone (if enabled)
    if upload_vectors:
        logger.info("Starting vector upload step...")
        
        # Reset URL tracking if requested
        if reset_tracking:
            if not run_command(
                ["python", "scripts/upload_with_url_tracking.py", "--reset-tracking"],
                "URL tracking reset"
            ):
                logger.error("Failed to reset URL tracking. Check logs for details.")
                return False
        
        # Run vector uploader
        cmd = ["python", "scripts/upload_with_url_tracking.py", 
               "--batch-size", str(batch_size), 
               "--delay", str(delay),
               "--vectors-dir", VECTORS_DIR]
               
        if test_mode:
            cmd.append("--test")
            
        if not run_command(cmd, "Vector upload"):
            logger.error("Vector upload failed. Check logs for details.")
            return False
            
        logger.info("Vector upload completed successfully")
    
    logger.info("Pipeline completed successfully!")
    return True

def main():
    """Main function to run the pipeline."""
    parser = argparse.ArgumentParser(description="Run the full Wookieepedia processing pipeline")
    parser.add_argument("--xml-file", type=str, default=XML_DUMP_FILE, help="Path to XML dump file")
    parser.add_argument("--skip-xml", action="store_true", help="Skip XML processing step")
    parser.add_argument("--skip-vectors", action="store_true", help="Skip vector generation step")
    parser.add_argument("--skip-upload", action="store_true", help="Skip vector upload step")
    parser.add_argument("--reset-tracking", action="store_true", help="Reset URL tracking before upload")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for processing")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between batches for upload")
    parser.add_argument("--workers", type=int, default=5, help="Number of worker processes")
    parser.add_argument("--test", action="store_true", help="Run in test mode (no actual uploads)")
    args = parser.parse_args()
    
    # Run the pipeline
    success = run_pipeline(
        xml_file=args.xml_file,
        process_xml=not args.skip_xml,
        generate_vectors=not args.skip_vectors,
        upload_vectors=not args.skip_upload,
        reset_tracking=args.reset_tracking,
        batch_size=args.batch_size,
        delay=args.delay,
        workers=args.workers,
        test_mode=args.test
    )
    
    # Exit with status code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 