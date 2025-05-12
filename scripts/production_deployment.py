#!/usr/bin/env python3
"""
Production Deployment for Wookiepedia XML Processing

This script implements the final production deployment steps for the Wookiepedia XML dump processing:
1. Sets up monitoring and alerting
2. Creates backup procedures
3. Runs verification tests
4. Provides deployment checklist
5. Executes the production pipeline

Usage:
    python scripts/production_deployment.py [--test-run] [--backup] [--monitor]
"""

import os
import sys
import json
import time
import argparse
import asyncio
import logging
import signal
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add project root to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.xml_vector_processor import XMLVectorProcessor
from process_status_manager import ProcessStatusManager
from processing_dashboard import ProcessingDashboard

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/deployment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Constants
XML_DUMP_PATH = "data/wookiepedia-dump/Star_Wars_Pages_Current.xml"
BACKUP_DIR = "data/backups"
VECTORS_DIR = "data/vectors"
STATUS_FILE = "data/processing_status.csv"
UPLOAD_STATUS_FILE = "data/pinecone_upload_status.json"
FINGERPRINTS_FILE = "data/content_fingerprints.json"
VECTOR_FINGERPRINTS_FILE = "data/vector_fingerprints.json"
MONITOR_INTERVAL = 600  # Check status every 10 minutes

class ProductionDeployment:
    """Handles the production deployment for Wookiepedia XML processing."""
    
    def __init__(self, xml_path: str = XML_DUMP_PATH):
        """Initialize the deployment manager."""
        self.xml_path = Path(xml_path)
        self.backup_dir = Path(BACKUP_DIR)
        self.vectors_dir = Path(VECTORS_DIR)
        self.status_manager = ProcessStatusManager(STATUS_FILE, UPLOAD_STATUS_FILE)
        self.dashboard = ProcessingDashboard(self.status_manager)
        self.monitoring_active = False
        self.alert_thresholds = {
            'error_rate': 0.05,  # Alert if error rate exceeds 5%
            'processing_stalled_minutes': 30,  # Alert if no progress for 30 minutes
            'upload_rate_min': 0.5  # Alert if upload rate drops below 0.5 vectors/second
        }
        
    def create_backup(self) -> bool:
        """Create backup of critical files."""
        try:
            # Create backup directory with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.backup_dir / f"backup_{timestamp}"
            backup_path.mkdir(parents=True, exist_ok=True)
            
            # Files to backup
            backup_files = [
                Path(STATUS_FILE),
                Path(UPLOAD_STATUS_FILE),
                Path(FINGERPRINTS_FILE),
                Path(VECTOR_FINGERPRINTS_FILE)
            ]
            
            # Copy each file to backup directory
            for file_path in backup_files:
                if file_path.exists():
                    shutil.copy2(file_path, backup_path / file_path.name)
                    logger.info(f"Backed up {file_path} to {backup_path}")
            
            # Create backup of latest vector files (limited to most recent 5)
            vector_files = sorted(self.vectors_dir.glob("*.parquet"))[-5:]
            vectors_backup = backup_path / "vectors"
            vectors_backup.mkdir(exist_ok=True)
            
            for vector_file in vector_files:
                shutil.copy2(vector_file, vectors_backup / vector_file.name)
                logger.info(f"Backed up {vector_file} to {vectors_backup}")
                
            # Create backup report
            with open(backup_path / "backup_report.json", 'w') as f:
                json.dump({
                    'timestamp': timestamp,
                    'files_backed_up': [str(f.name) for f in backup_files if f.exists()],
                    'vector_files_backed_up': [str(f.name) for f in vector_files]
                }, f, indent=2)
                
            logger.info(f"Backup completed successfully to {backup_path}")
            return True
                
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return False
            
    def restore_from_backup(self, backup_path: str) -> bool:
        """Restore from a backup."""
        try:
            backup_dir = Path(backup_path)
            if not backup_dir.exists() or not backup_dir.is_dir():
                logger.error(f"Backup directory {backup_path} does not exist")
                return False
                
            # Files to restore
            files_to_restore = [
                ('processing_status.csv', STATUS_FILE),
                ('pinecone_upload_status.json', UPLOAD_STATUS_FILE),
                ('content_fingerprints.json', FINGERPRINTS_FILE),
                ('vector_fingerprints.json', VECTOR_FINGERPRINTS_FILE)
            ]
            
            # Restore each file
            for src_name, dest_path in files_to_restore:
                src_file = backup_dir / src_name
                if src_file.exists():
                    shutil.copy2(src_file, dest_path)
                    logger.info(f"Restored {src_file} to {dest_path}")
                    
            # Restore vector files if vectors directory exists
            vectors_backup = backup_dir / "vectors"
            if vectors_backup.exists() and vectors_backup.is_dir():
                for vector_file in vectors_backup.glob("*.parquet"):
                    shutil.copy2(vector_file, self.vectors_dir / vector_file.name)
                    logger.info(f"Restored {vector_file} to {self.vectors_dir}")
                    
            logger.info(f"Restore completed successfully from {backup_path}")
            return True
                
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False
            
    async def run_test(self) -> bool:
        """Run a test with a small sample of articles."""
        try:
            logger.info("Starting test run with small sample")
            
            # Create test processor with small batch size
            processor = XMLVectorProcessor(
                xml_file=self.xml_path,
                batch_size=10,
                workers=1,
                vectors_dir=str(self.vectors_dir)
            )
            
            # Override run method to process only 2 batches
            original_run = processor.run
            
            async def limited_run():
                # Get URLs to process
                xml_urls = await processor.wiki_processor._collect_urls()
                logger.info(f"Found {len(xml_urls)} articles in XML dump")
                
                # Get sample of 20 URLs
                sample_urls = list(xml_urls)[:20]
                if not sample_urls:
                    logger.error("No URLs available for testing")
                    return
                
                # Process sample URLs
                batches = [
                    sample_urls[i:i + processor.batch_size]
                    for i in range(0, len(sample_urls), processor.batch_size)
                ]
                
                # Process only first 2 batches
                for i, batch_urls in enumerate(batches[:2]):
                    batch_num = i + 1
                    
                    # Process batch
                    logger.info(f"Processing test batch {batch_num}")
                    parquet_file = await processor.process_batch(
                        batch_urls,
                        batch_num,
                        2  # total_batches
                    )
                    
                    if parquet_file:
                        # Upload vectors
                        if await processor.upload_vectors(parquet_file):
                            # Update status
                            for url in batch_urls:
                                processor.wiki_processor.status_manager.update_status(
                                    url=url,
                                    title=processor.wiki_processor._get_title_from_url(url),
                                    processed=True,
                                    vectorized=True,
                                    uploaded=True
                                )
                        else:
                            logger.error(f"Failed to upload vectors for test batch {batch_num}")
                            
                    # Save status
                    processor.wiki_processor.status_manager.save_status()
                    
                logger.info("Test run completed successfully")
            
            # Replace run method with limited version
            processor.run = limited_run
            
            # Run test
            await processor.run()
            logger.info("Test run completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Test run failed: {e}")
            return False
            
    async def start_monitoring(self):
        """Start monitoring the processing pipeline."""
        self.monitoring_active = True
        
        logger.info("Starting monitoring system")
        last_status = {}
        last_check_time = time.time()
        
        while self.monitoring_active:
            try:
                # Get current processing stats
                current_stats = self.status_manager.get_processing_stats()
                
                # Calculate processing rate
                current_time = time.time()
                elapsed_minutes = (current_time - last_check_time) / 60
                
                if last_status:
                    # Calculate rates
                    new_processed = current_stats['processed'] - last_status['processed']
                    new_vectorized = current_stats['vectorized'] - last_status['vectorized'] 
                    new_uploaded = current_stats['uploaded'] - last_status['uploaded']
                    new_errors = current_stats['errors'] - last_status['errors']
                    
                    processing_rate = new_processed / elapsed_minutes if elapsed_minutes > 0 else 0
                    upload_rate = new_uploaded / elapsed_minutes if elapsed_minutes > 0 else 0
                    error_rate = new_errors / max(new_processed, 1)
                    
                    logger.info(f"Processing rate: {processing_rate:.2f} articles/minute")
                    logger.info(f"Upload rate: {upload_rate:.2f} articles/minute")
                    logger.info(f"Error rate: {error_rate:.2%}")
                    
                    # Check for alerts
                    if processing_rate == 0 and elapsed_minutes >= self.alert_thresholds['processing_stalled_minutes']:
                        self._send_alert(f"ALERT: Processing stalled for {elapsed_minutes:.1f} minutes")
                        
                    if error_rate > self.alert_thresholds['error_rate']:
                        self._send_alert(f"ALERT: High error rate detected: {error_rate:.2%}")
                        
                    if upload_rate > 0 and upload_rate < self.alert_thresholds['upload_rate_min']:
                        self._send_alert(f"ALERT: Low upload rate: {upload_rate:.2f} articles/minute")
                
                # Update last status
                last_status = current_stats.copy()
                last_check_time = current_time
                
                # Check for processing status flush needed
                self.status_manager.save_status()
                
                # Wait for next check
                await asyncio.sleep(MONITOR_INTERVAL)
                
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying
                
    def _send_alert(self, message: str):
        """Send an alert (currently just logs, would integrate with notification system)."""
        logger.warning(message)
        # In production, would integrate with email/SMS/Slack notification
        print(f"\n⚠️ {message}")
        
    def stop_monitoring(self):
        """Stop the monitoring system."""
        self.monitoring_active = False
        logger.info("Monitoring stopped")
        
    def print_deployment_checklist(self):
        """Print the deployment checklist."""
        checklist = [
            "✅ DEPLOYMENT CHECKLIST",
            "------------------------",
            "1. Verify XML dump file exists and is readable",
            "2. Check database connections and credentials",
            "3. Create backup of existing data",
            "4. Verify API keys for OpenAI and Pinecone",
            "5. Confirm Pinecone index is accessible",
            "6. Run test with small sample",
            "7. Check disk space for vector storage",
            "8. Start monitoring system",
            "9. Begin full production run",
            "10. Schedule periodic backups",
        ]
        
        print("\n".join(checklist))
        
        # Verify essential files
        xml_dump_exists = self.xml_path.exists()
        print(f"\nStatus checks:")
        print(f"XML dump file: {'✅ Found' if xml_dump_exists else '❌ Missing'}")
        print(f"Vectors directory: {'✅ Found' if self.vectors_dir.exists() else '❌ Missing'}")
        print(f"Status file: {'✅ Found' if Path(STATUS_FILE).exists() else '❌ Missing'}")
        
    async def run_production(self):
        """Run the full production pipeline."""
        # Create backup before starting
        self.create_backup()
        
        # Start monitoring in background
        monitoring_task = asyncio.create_task(self.start_monitoring())
        
        try:
            # Run the processor
            processor = XMLVectorProcessor(
                xml_file=self.xml_path,
                batch_size=100,
                workers=3,
                vectors_dir=str(self.vectors_dir)
            )
            
            await processor.run()
            logger.info("Production run completed successfully")
            
        except Exception as e:
            logger.error(f"Production run failed: {e}")
            self._send_alert(f"CRITICAL: Production run failed: {e}")
            
        finally:
            # Stop monitoring
            self.stop_monitoring()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass
            
            # Create final backup
            self.create_backup()

async def main():
    """Parse arguments and run the deployment."""
    parser = argparse.ArgumentParser(
        description="Production Deployment for Wookiepedia XML Processing"
    )
    parser.add_argument(
        '--test-run',
        action='store_true',
        help='Run a test with a small sample'
    )
    parser.add_argument(
        '--backup',
        action='store_true',
        help='Create a backup of critical files'
    )
    parser.add_argument(
        '--monitor',
        action='store_true',
        help='Start monitoring without processing'
    )
    parser.add_argument(
        '--xml-path',
        type=str,
        default=XML_DUMP_PATH,
        help=f'Path to XML dump file (default: {XML_DUMP_PATH})'
    )
    args = parser.parse_args()
    
    deployment = ProductionDeployment(xml_path=args.xml_path)
    
    # Print deployment checklist
    deployment.print_deployment_checklist()
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\nReceived interrupt, shutting down gracefully...")
        deployment.stop_monitoring()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    
    if args.backup:
        # Just create a backup
        deployment.create_backup()
    elif args.test_run:
        # Run a test
        await deployment.run_test()
    elif args.monitor:
        # Just run monitoring
        print("Starting monitoring system. Press Ctrl+C to stop.")
        await deployment.start_monitoring()
    else:
        # Ask for confirmation before running production
        confirm = input("\nAre you sure you want to start the full production run? (y/n): ")
        if confirm.lower() == 'y':
            await deployment.run_production()
        else:
            print("Production run cancelled")

if __name__ == "__main__":
    asyncio.run(main()) 