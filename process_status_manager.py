#!/usr/bin/env python3
"""
Process Status Manager

Tracks the status of article and URL processing throughout the Wookieepedia export pipeline.
Provides persistent storage and status tracking for multi-step processing.
"""

import os
import csv
import json
import logging
import fcntl
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional, List, Set
from datetime import datetime
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ProcessingStatus:
    """Tracks the processing status of an article."""
    url: str
    processed: bool = False
    vectorized: bool = False
    uploaded: bool = False
    error: bool = False
    priority: str = "low"
    timestamp: str = None
    batch_id: Optional[str] = None
    
    def __post_init__(self):
        """Set default timestamp if none provided."""
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class ProcessStatusManager:
    """Manages the status of article processing."""
    
    def __init__(self, csv_path: str = "data/processing_status.csv", 
                 flush_interval: int = 10, batch_updates: bool = True):
        """
        Initialize the status manager.
        
        Args:
            csv_path: Path to CSV file storing status information
            flush_interval: How often to flush updates to disk (in seconds)
            batch_updates: Whether to batch status updates before writing to disk
        """
        self.csv_path = Path(csv_path)
        self.statuses: Dict[str, ProcessingStatus] = {}
        self.dirty_statuses: Dict[str, ProcessingStatus] = {}  # Statuses that need to be saved
        self.loaded = False
        self.current_batch_id = None
        self.last_flush_time = time.time()
        self.flush_interval = flush_interval
        self.batch_updates = batch_updates
        
        # Create directory if it doesn't exist
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create empty status file if it doesn't exist
        if not self.csv_path.exists():
            self._save_statuses({})
        
        # Load existing statuses
        self._load_statuses()

    @contextmanager
    def _file_lock(self):
        """Context manager for file locking to prevent concurrent access."""
        lock_path = str(self.csv_path) + '.lock'
        with open(lock_path, 'w') as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def start_new_batch(self) -> str:
        """Start a new processing batch and return the batch ID."""
        self.current_batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.current_batch_id

    def _load_statuses(self) -> None:
        """Load processing statuses from CSV file with file locking."""
        with self._file_lock():
            try:
                if not self.csv_path.exists():
                    logger.info(f"No status file found at {self.csv_path}, will create new file")
                    self.loaded = True
                    return

                with open(self.csv_path, 'r', newline='') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        url = row.get('url', '')
                        if not url:
                            continue
                            
                        self.statuses[url] = ProcessingStatus(
                            url=url,
                            processed=row.get('processed', '').lower() == 'true',
                            vectorized=row.get('vectorized', '').lower() == 'true',
                            uploaded=row.get('uploaded', '').lower() == 'true',
                            error=row.get('error', '').lower() == 'true',
                            priority=row.get('priority', 'low'),
                            timestamp=row.get('timestamp', ''),
                            batch_id=row.get('batch_id', None)
                        )
                
                logger.info(f"Loaded {len(self.statuses)} processing statuses from {self.csv_path}")
                self.loaded = True
                
            except Exception as e:
                logger.error(f"Error loading statuses: {e}")
                # Create an empty statuses dict
                self.statuses = {}
                self.loaded = True

    def _save_statuses(self, statuses_to_save: Dict[str, ProcessingStatus]) -> None:
        """Save processing statuses to CSV file with file locking."""
        with self._file_lock():
            try:
                # First load existing statuses to merge
                existing_statuses = {}
                if self.csv_path.exists():
                    with open(self.csv_path, 'r', newline='') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            url = row.get('url', '')
                            if url:
                                existing_statuses[url] = ProcessingStatus(
                                    url=url,
                                    processed=row.get('processed', '').lower() == 'true',
                                    vectorized=row.get('vectorized', '').lower() == 'true',
                                    uploaded=row.get('uploaded', '').lower() == 'true',
                                    error=row.get('error', '').lower() == 'true',
                                    priority=row.get('priority', 'low'),
                                    timestamp=row.get('timestamp', ''),
                                    batch_id=row.get('batch_id', None)
                                )

                # Merge new statuses with existing ones
                merged_statuses = {**existing_statuses, **statuses_to_save}

                # Write merged statuses back to file
                fieldnames = ['url', 'processed', 'vectorized', 'uploaded', 'error', 'priority', 'timestamp', 'batch_id']
                with open(self.csv_path, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for status in merged_statuses.values():
                        writer.writerow(asdict(status))
                        
                logger.info(f"Saved {len(merged_statuses)} processing statuses to {self.csv_path}")
                
            except Exception as e:
                logger.error(f"Error saving statuses: {e}")

    def _flush_dirty_statuses(self, force: bool = False) -> None:
        """
        Flush dirty (updated) statuses to disk if enough time has passed.
        
        Args:
            force: Force flush regardless of time interval
        """
        current_time = time.time()
        if (force or (current_time - self.last_flush_time >= self.flush_interval)) and self.dirty_statuses:
            # Only save dirty statuses to avoid reading/writing the entire file
            statuses_to_save = self.dirty_statuses.copy()
            self._save_statuses(statuses_to_save)
            self.dirty_statuses.clear()
            self.last_flush_time = current_time

    def update_batch_status(self, statuses: Dict[str, ProcessingStatus]) -> None:
        """
        Update status for a batch of URLs atomically.
        
        Args:
            statuses: Dictionary of URL to ProcessingStatus objects
        """
        if not self.current_batch_id:
            self.start_new_batch()

        # Update batch_id for all statuses
        for status in statuses.values():
            status.batch_id = self.current_batch_id

        # Update in-memory statuses
        self.statuses.update(statuses)
        
        if self.batch_updates:
            # Add to dirty statuses for batched updates
            self.dirty_statuses.update(statuses)
            self._flush_dirty_statuses()
        else:
            # Save immediately if not batching
            self._save_statuses(statuses)

    def reset_status_file(self) -> None:
        """Reset the status file to start fresh."""
        logger.info("Resetting status file...")
        self.statuses = {}
        self.dirty_statuses = {}
        with self._file_lock():
            if self.csv_path.exists():
                self.csv_path.unlink()
            self._save_statuses({})
        self.loaded = True
        self.last_flush_time = time.time()

    def get_status(self, url: str) -> Optional[ProcessingStatus]:
        """Get the status of a URL."""
        if not self.loaded:
            self._load_statuses()
        return self.statuses.get(url)

    def get_batch_status(self, batch_id: str) -> Dict[str, ProcessingStatus]:
        """Get all statuses for a specific batch."""
        if not self.loaded:
            self._load_statuses()
        return {url: status for url, status in self.statuses.items() if status.batch_id == batch_id}

    def mark_vectorized(self, urls: List[str], success: bool = True) -> None:
        """
        Mark multiple URLs as vectorized in a batch.
        
        Args:
            urls: List of URLs to mark
            success: Whether vectorization was successful
        """
        batch_statuses = {}
        for url in urls:
            status = self.get_status(url) or ProcessingStatus(url=url)
            status.vectorized = True
            status.error = not success
            batch_statuses[url] = status
        
        self.update_batch_status(batch_statuses)

    def get_stats(self) -> Dict[str, int]:
        """Get processing statistics."""
        if not self.loaded:
            self._load_statuses()
            
        stats = {
            'total': len(self.statuses),
            'processed': sum(1 for s in self.statuses.values() if s.processed),
            'vectorized': sum(1 for s in self.statuses.values() if s.vectorized),
            'uploaded': sum(1 for s in self.statuses.values() if s.uploaded),
            'error': sum(1 for s in self.statuses.values() if s.error),
            'current_batch': sum(1 for s in self.statuses.values() if s.batch_id == self.current_batch_id) if self.current_batch_id else 0
        }
        
        return stats

    def get_to_process(self, limit: int = 100, priority: Optional[str] = None) -> List[str]:
        """
        Get URLs that need processing.
        
        Args:
            limit: Maximum number of URLs to return
            priority: Optional priority to filter by
            
        Returns:
            List of URLs to process
        """
        # Ensure statuses are loaded
        if not self.loaded:
            self._load_statuses()
            
        to_process = []
        for url, status in self.statuses.items():
            if not status.processed and not status.error:
                if priority is None or status.priority == priority:
                    to_process.append(url)
                    if len(to_process) >= limit:
                        break
                        
        return to_process
        
    def get_to_vectorize(self, limit: int = 100) -> List[str]:
        """
        Get URLs that need vectorization.
        
        Args:
            limit: Maximum number of URLs to return
            
        Returns:
            List of URLs to vectorize
        """
        # Ensure statuses are loaded
        if not self.loaded:
            self._load_statuses()
            
        to_vectorize = []
        for url, status in self.statuses.items():
            if status.processed and not status.vectorized and not status.error:
                to_vectorize.append(url)
                if len(to_vectorize) >= limit:
                    break
                    
        return to_vectorize
        
    def get_to_upload(self, limit: int = 100) -> List[str]:
        """
        Get URLs that need uploading.
        
        Args:
            limit: Maximum number of URLs to return
            
        Returns:
            List of URLs to upload
        """
        # Ensure statuses are loaded
        if not self.loaded:
            self._load_statuses()
            
        to_upload = []
        for url, status in self.statuses.items():
            if status.vectorized and not status.uploaded and not status.error:
                to_upload.append(url)
                if len(to_upload) >= limit:
                    break
                    
        return to_upload
    
    def mark_processed(self, url: str, success: bool = True) -> None:
        """Mark a URL as processed."""
        status = self.get_status(url) or ProcessingStatus(url=url)
        status.processed = True
        status.error = not success
        
        self.statuses[url] = status
        self.dirty_statuses[url] = status
        self._flush_dirty_statuses()
    
    def mark_uploaded(self, url: str, success: bool = True) -> None:
        """Mark a URL as uploaded."""
        status = self.get_status(url) or ProcessingStatus(url=url)
        status.uploaded = True
        status.error = not success
        
        self.statuses[url] = status
        self.dirty_statuses[url] = status
        self._flush_dirty_statuses()
    
    def mark_error(self, url: str) -> None:
        """Mark a URL as having an error."""
        status = self.get_status(url) or ProcessingStatus(url=url)
        status.error = True
        
        self.statuses[url] = status
        self.dirty_statuses[url] = status
        self._flush_dirty_statuses()
    
    def set_priority(self, url: str, priority: str) -> None:
        """Set the priority for a URL."""
        status = self.get_status(url) or ProcessingStatus(url=url)
        status.priority = priority
        
        self.statuses[url] = status
        self.dirty_statuses[url] = status
        self._flush_dirty_statuses()
    
    def reset_error(self, url: str) -> None:
        """Reset error flag for a URL."""
        status = self.get_status(url)
        if status:
            status.error = False
            self.statuses[url] = status
            self.dirty_statuses[url] = status
            self._flush_dirty_statuses()
    
    def reset_status(self, url: str) -> None:
        """Reset all status flags for a URL."""
        if url in self.statuses:
            self.statuses[url] = ProcessingStatus(url=url)
            self.dirty_statuses[url] = self.statuses[url]
            self._flush_dirty_statuses()
    
    def save(self) -> None:
        """Force save all statuses to disk."""
        self._flush_dirty_statuses(force=True)
    
    def get_all_statuses(self) -> Dict[str, ProcessingStatus]:
        """Get all statuses."""
        if not self.loaded:
            self._load_statuses()
        return self.statuses.copy() 