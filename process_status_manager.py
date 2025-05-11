"""
Process Status Manager for Wookieepedia XML dump processing.
Handles tracking, comparison, and state management for large-scale article processing.
"""

import csv
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Callable

import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class ProcessingStatus:
    """Represents the processing status of an article."""
    title: str
    url: str
    processed: bool = False
    vectorized: bool = False
    uploaded: bool = False
    last_modified: Optional[str] = None
    error: Optional[str] = None
    process_date: Optional[str] = None
    deleted: bool = False  # Track if article has been deleted
    metadata: Optional[Dict] = None  # Store additional metadata

    def to_dict(self) -> Dict:
        """Convert to dictionary with proper metadata handling."""
        data = asdict(self)
        if self.metadata:
            try:
                # Ensure metadata is JSON serializable
                data['metadata'] = json.dumps(self.metadata)
            except Exception as e:
                logger.error(f"Error serializing metadata for {self.url}: {e}")
                data['metadata'] = None
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> 'ProcessingStatus':
        """Create from dictionary with proper metadata handling."""
        if 'metadata' in data and data['metadata']:
            try:
                data['metadata'] = json.loads(data['metadata'])
            except Exception as e:
                logger.error(f"Error deserializing metadata: {e}")
                data['metadata'] = None
        return cls(**data)

class ProcessStatusManager:
    """Manages processing status for Wookieepedia XML dump articles."""
    
    def __init__(self, status_file: str = "data/processing_status.csv",
                 upload_status_file: str = "data/pinecone_upload_status.json"):
        self.status_file = Path(status_file)
        self.upload_status_file = Path(upload_status_file)
        self.status_map: Dict[str, ProcessingStatus] = {}
        self.processed_urls: Set[str] = set()
        self.current_batch: List[str] = []  # Track current processing batch
        self.batch_size = 100  # Default batch size
        
        # Event callbacks
        self.on_status_update: Optional[Callable[[str, Dict], None]] = None
        self.on_batch_complete: Optional[Callable[[int, float], None]] = None
        self.on_error: Optional[Callable[[str, str], None]] = None
        
        self._load_existing_status()
        self._batch_start_time: Optional[datetime] = None

    def _load_existing_status(self) -> None:
        """Load existing processing status from files."""
        if self.status_file.exists():
            try:
                df = pd.read_csv(self.status_file)
                for _, row in df.iterrows():
                    try:
                        status = ProcessingStatus.from_dict(row.to_dict())
                        self.status_map[row['url']] = status
                        if status.processed and not status.deleted:
                            self.processed_urls.add(row['url'])
                    except Exception as e:
                        logger.error(f"Error loading status for row: {e}")
                        continue
                logger.info(f"Loaded {len(self.status_map)} existing status records")
            except Exception as e:
                logger.error(f"Error loading status file: {e}")
                raise

    def compare_urls(self, xml_urls: Set[str]) -> Tuple[Set[str], Set[str], Set[str]]:
        """
        Compare XML dump URLs with existing processed URLs to identify changes.
        
        Args:
            xml_urls: Set of URLs found in the XML dump
            
        Returns:
            Tuple containing sets of:
            - new_urls: URLs not previously processed
            - update_urls: URLs that need reprocessing
            - deleted_urls: URLs no longer in dump
        """
        existing_urls = set(self.status_map.keys())
        
        # Find new URLs
        new_urls = xml_urls - existing_urls
        
        # Find deleted URLs
        deleted_urls = existing_urls - xml_urls
        
        # Find URLs needing update (processed but with errors or incomplete)
        update_urls = {
            url for url in (xml_urls & existing_urls)
            if self.needs_processing(url)
        }
        
        # Mark deleted URLs in status map
        for url in deleted_urls:
            if url in self.status_map:
                self.status_map[url].deleted = True
                if url in self.processed_urls:
                    self.processed_urls.remove(url)
        
        logger.info(f"URL comparison results:")
        logger.info(f"- New URLs: {len(new_urls)}")
        logger.info(f"- URLs needing update: {len(update_urls)}")
        logger.info(f"- Deleted URLs: {len(deleted_urls)}")
        
        return new_urls, update_urls, deleted_urls

    def start_batch(self, batch_size: Optional[int] = None) -> None:
        """Start a new processing batch."""
        if batch_size is not None:
            self.batch_size = batch_size
        self.current_batch = []
        self._batch_start_time = datetime.now()

    def add_to_batch(self, url: str) -> bool:
        """
        Add URL to current batch. Returns True if batch is full.
        
        Args:
            url: URL to add to batch
            
        Returns:
            bool: True if batch is full and should be processed
        """
        self.current_batch.append(url)
        return len(self.current_batch) >= self.batch_size

    def get_current_batch(self) -> List[str]:
        """Get URLs in current batch."""
        return self.current_batch.copy()

    def clear_batch(self) -> None:
        """Clear the current batch and notify listeners."""
        if self._batch_start_time and self.on_batch_complete:
            duration = (datetime.now() - self._batch_start_time).total_seconds()
            self.on_batch_complete(len(self.current_batch), duration)
        self.current_batch = []
        self._batch_start_time = None

    def mark_deleted(self, urls: Set[str]) -> None:
        """
        Mark articles as deleted.
        
        Args:
            urls: Set of URLs to mark as deleted
        """
        for url in urls:
            if url in self.status_map:
                self.status_map[url].deleted = True
                if url in self.processed_urls:
                    self.processed_urls.remove(url)
        logger.info(f"Marked {len(urls)} articles as deleted")

    def get_deleted_articles(self) -> List[Tuple[str, str]]:
        """Get list of deleted articles (title, url)."""
        return [(status.title, status.url) 
                for status in self.status_map.values() 
                if status.deleted]

    def get_batch_stats(self) -> Dict[str, int]:
        """Get statistics about the current batch."""
        return {
            'batch_size': self.batch_size,
            'current_size': len(self.current_batch),
            'remaining_capacity': self.batch_size - len(self.current_batch)
        }

    def save_status(self) -> None:
        """Save current processing status to files."""
        try:
            # Ensure directory exists
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert to DataFrame and save
            records = []
            for status in self.status_map.values():
                try:
                    records.append(status.to_dict())
                except Exception as e:
                    logger.error(f"Error converting status to dict for {status.url}: {e}")
                    continue
            
            df = pd.DataFrame(records)
            df.to_csv(self.status_file, index=False)
            logger.debug(f"Saved {len(records)} status records")
            
        except Exception as e:
            logger.error(f"Error saving status file: {e}")
            raise

    def needs_processing(self, url: str, last_modified: Optional[str] = None) -> bool:
        """
        Check if an article needs processing.
        
        Args:
            url: Article URL
            last_modified: Optional last modified timestamp
            
        Returns:
            bool: True if article needs processing
        """
        if url not in self.status_map:
            return True
            
        status = self.status_map[url]
        
        # Always reprocess if there was an error
        if status.error:
            return True
            
        # Check if content has been modified
        if last_modified and status.last_modified:
            if last_modified > status.last_modified:
                return True
                
        # Check if processing is incomplete
        if not status.processed or status.deleted:
            return True
            
        return False

    def update_status(self, url: str, title: str, processed: bool = False,
                     vectorized: bool = False, uploaded: bool = False,
                     error: Optional[str] = None, metadata: Optional[Dict] = None) -> None:
        """
        Update processing status for an article.
        
        Args:
            url: Article URL
            title: Article title
            processed: Whether article has been processed
            vectorized: Whether vectors have been generated
            uploaded: Whether vectors have been uploaded
            error: Optional error message
            metadata: Optional metadata dictionary
        """
        try:
            # Create or update status
            if url in self.status_map:
                status = self.status_map[url]
                status.processed = processed
                status.vectorized = vectorized
                status.uploaded = uploaded
                status.error = error
                if metadata:
                    status.metadata = metadata
            else:
                status = ProcessingStatus(
                    title=title,
                    url=url,
                    processed=processed,
                    vectorized=vectorized,
                    uploaded=uploaded,
                    error=error,
                    metadata=metadata,
                    process_date=datetime.now().isoformat()
                )
                self.status_map[url] = status
            
            # Update processed URLs set
            if processed and not error:
                self.processed_urls.add(url)
            elif url in self.processed_urls:
                self.processed_urls.remove(url)
            
            # Notify listeners
            if self.on_status_update:
                self.on_status_update(url, status.to_dict())
            if error and self.on_error:
                self.on_error(url, error)
                
        except Exception as e:
            logger.error(f"Error updating status for {url}: {e}")
            if self.on_error:
                self.on_error(url, str(e))

    def get_processing_stats(self) -> Dict[str, int]:
        """Get current processing statistics."""
        total = len(self.status_map)
        processed = len(self.processed_urls)
        failed = len([s for s in self.status_map.values() if s.error])
        deleted = len([s for s in self.status_map.values() if s.deleted])
        
        return {
            'total': total,
            'processed': processed,
            'failed': failed,
            'deleted': deleted,
            'remaining': total - processed - deleted
        }

    def get_failed_articles(self) -> List[Tuple[str, str, str]]:
        """Get list of failed articles (title, url, error)."""
        return [(status.title, status.url, status.error)
                for status in self.status_map.values()
                if status.error]

    def reset_failed_articles(self) -> int:
        """
        Reset status of failed articles for retry.
        
        Returns:
            int: Number of articles reset
        """
        count = 0
        for status in self.status_map.values():
            if status.error:
                status.error = None
                status.processed = False
                status.vectorized = False
                status.uploaded = False
                count += 1
        return count 