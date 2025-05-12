#!/usr/bin/env python3
"""
Wookieepedia Full Processing Pipeline

This script runs the complete Wookieepedia dump processing pipeline:
1. Process XML dump to filter content and identify Canon vs Legends
2. Generate vector embeddings from the processed content
3. Upload vectors to Pinecone with URL-based deduplication

Usage:
    python run_holocron_export.py --xml-file PATH --batch-size SIZE [--workers N] [--test]
"""

import os
import sys
import argparse
import logging
import subprocess
from pathlib import Path
from datetime import datetime

# Import process status manager for initialization
from process_status_manager import ProcessStatusManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/full_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

def run_command(cmd, desc):
    """Run a command with proper logging."""
    logger.info(f"STEP: {desc}")
    logger.info(f"Running command: {' '.join(cmd)}")
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Stream output
    for line in process.stdout:
        logger.info(line.strip())
    
    process.wait()
    
    if process.returncode != 0:
        logger.error(f"Command failed with exit code {process.returncode}")
        return False
    
    logger.info(f"Command completed successfully")
    return True

def process_xml_dump(xml_file, output_dir, batch_size):
    """Process XML dump into filtered content."""
    # Initialize the status manager to ensure it exists
    status_manager = ProcessStatusManager()
    
    # Create the output directory
    os.makedirs(output_dir, exist_ok=True)
    
    cmd = [
        "python", "-c",
        f"""
import sys
sys.path.append('.')
from scripts.process_wiki_dump import main
sys.argv = ['process_wiki_dump.py', '{xml_file}', '{output_dir}', '--batch-size', '{batch_size}']
main()
        """
    ]
    
    return run_command(cmd, "Processing XML dump")

def generate_vectors(output_dir, batch_size, workers):
    """Generate vector embeddings for processed content."""
    # Find all processed XML/JSON files
    processed_files = list(Path(output_dir).glob("*.json"))
    if not processed_files:
        logger.error(f"No processed files found in {output_dir}")
        return False
    
    logger.info(f"Found {len(processed_files)} processed files")
    
    cmd = [
        "python", "scripts/create_vectors.py",
        "--input-dir", output_dir,
        "--output-dir", "data/vectors",
        "--batch-size", str(batch_size),
        "--workers", str(workers)
    ]
    
    return run_command(cmd, "Generating vector embeddings")

def upload_vectors(batch_size, test_mode=False, max_files=None):
    """Upload vectors to Pinecone with deduplication."""
    cmd = [
        "python", "scripts/upload_with_url_tracking.py",
        "--batch-size", str(batch_size),
        "--delay", "0.5",
        "--vectors-dir", "data/vectors"
    ]
    
    if test_mode:
        cmd.append("--test")
        
    if max_files is not None:
        cmd.extend(["--max-files", str(max_files)])
    
    return run_command(cmd, "Uploading vectors to Pinecone")

def main():
    parser = argparse.ArgumentParser(description="Run the full Wookieepedia export pipeline")
    parser.add_argument("--xml-file", required=True, help="Path to XML dump file")
    parser.add_argument("--output-dir", default="data/processed_articles", help="Output directory for processed content")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for processing")
    parser.add_argument("--workers", type=int, default=3, help="Number of worker processes")
    parser.add_argument("--test", action="store_true", help="Run in test mode without actual uploads")
    parser.add_argument("--skip-steps", type=int, default=0, help="Skip initial steps (1=skip XML, 2=skip XML+vectors)")
    parser.add_argument("--max-files", type=int, help="Maximum number of files to process during upload")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("holocron_export.log"),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)
    
    # Create output directories
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs("data/vectors", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    logger.info("=== Starting Wookieepedia Export Pipeline ===")
    logger.info(f"XML file: {args.xml_file}")
    logger.info(f"Output directory: {args.output_dir}")
    logger.info(f"Batch size: {args.batch_size}")
    logger.info(f"Workers: {args.workers}")
    logger.info(f"Test mode: {args.test}")
    logger.info(f"Debug mode: {args.debug}")
    
    # Step 1: Process XML dump
    if args.skip_steps < 1:
        if not process_xml_dump(args.xml_file, args.output_dir, args.batch_size):
            logger.error("XML processing failed. Aborting pipeline.")
            return
    else:
        logger.info("Skipping XML processing step")
    
    # Step 2: Generate vectors
    if args.skip_steps < 2:
        if not generate_vectors(args.output_dir, args.batch_size, args.workers):
            logger.error("Vector generation failed. Aborting pipeline.")
            return
    else:
        logger.info("Skipping vector generation step")
    
    # Step 3: Upload vectors
    if not upload_vectors(args.batch_size, args.test, args.max_files):
        logger.error("Vector upload failed.")
        return
    
    logger.info("=== Wookieepedia Export Pipeline Completed Successfully ===")

if __name__ == "__main__":
    main() 