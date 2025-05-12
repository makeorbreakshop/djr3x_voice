#!/usr/bin/env python3
"""
MediaWiki XML dump processor for Wookieepedia content.

This script processes a MediaWiki XML dump file, extracting Canon content
and preparing it for the Holocron Knowledge Base.
"""

import os
import re
import json
import logging
import argparse
import asyncio
import sys
from typing import Dict, Generator, Any, Optional, Set, Tuple, List
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Ensure src directory is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

# Import local version
try:
    from process_status_manager import ProcessStatusManager
except ImportError:
    # Try importing from src directory
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
    from holocron.wiki_processing.process_status_manager import ProcessStatusManager
# Import from project structure
from holocron.wiki_processing.wiki_markup_converter import WikiMarkupConverter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ArticleData:
    """Structured data for a processed article."""
    title: str
    content: str
    plain_text: str  # New field for converted plain text
    categories: Set[str]
    is_canonical: bool
    namespace: int
    revision_id: str
    url: str  # Added URL field

class WikiDumpProcessor:
    """Processes MediaWiki XML dumps with memory-efficient streaming."""
    
    def __init__(self, dump_path: str, output_dir: str):
        """
        Initialize the processor.
        
        Args:
            dump_path: Path to the XML dump file
            output_dir: Directory for output files
        """
        self.dump_path = dump_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize markup converter
        self.markup_converter = WikiMarkupConverter()
        
        # Tracking
        self.processed_count = 0
        self.canon_count = 0
        self.legends_count = 0
        
        # XML namespaces
        self.ns = {
            'mw': 'http://www.mediawiki.org/xml/export-0.11/'
        }
    
    def _extract_categories(self, text: str) -> Set[str]:
        """
        Extract categories from article text.
        
        Args:
            text: Article wikitext
            
        Returns:
            Set of category names
        """
        categories = set()
        
        # Match [[Category:Name]] pattern (standard wiki syntax)
        category_pattern = r'\[\[Category:([^\]|]+)(?:\|[^\]]+)?\]\]'
        matches = re.finditer(category_pattern, text)
        
        for match in matches:
            category = match.group(1).strip()
            categories.add(category)
            
        return categories
    
    def _is_canonical_content(self, title: str, categories: Set[str], text: str) -> bool:
        """
        Determine if content is Canon based on categories, text, and title.
        
        Args:
            title: Article title
            categories: Set of article categories
            text: Article text content
            
        Returns:
            True if content is canonical, False if legends, None if unclear
        """
        # Convert categories to lowercase for case-insensitive matching
        categories_lower = {cat.lower() for cat in categories}
        
        # Check if article is explicitly marked Canon or Legends
        canon_categories = {"canon articles", "canon", "canon media"}
        legends_categories = {"legends articles", "legends", "legends media"}
        
        # Check for any Canon category match
        for category in categories_lower:
            for canon_term in canon_categories:
                if canon_term in category:
                    return True
        
        # Check for any Legends category match
        for category in categories_lower:
            for legends_term in legends_categories:
                if legends_term in category:
                    return False
        
        # Check for "Category" namespace prefix in text (common in wikitext)
        if "[[Category:Canon" in text or "[[Category:canon" in text:
            return True
            
        if "[[Category:Legends" in text or "[[Category:legends" in text:
            return False
            
        # Check content for Canon tab markers (used in tabbed articles)
        canon_markers = [
            r"==Canon==",
            r"== Canon ==",
            r"tabsection=Canon",
            r"tab=Canon",
            r"\{\{Canon\}\}",
            r"\{\{Canonentry\}\}",
            r"\{\{Canon entry\}\}"
        ]
        
        if any(re.search(marker, text, re.IGNORECASE) for marker in canon_markers):
            return True
            
        # Check content for Legends tab markers (used in tabbed articles)
        legends_markers = [
            r"==Legends==",
            r"== Legends ==",
            r"tabsection=Legends",
            r"tab=Legends",
            r"\{\{Legends\}\}",
            r"\{\{Legendsentry\}\}",
            r"\{\{Legends entry\}\}"
        ]
        
        if any(re.search(marker, text, re.IGNORECASE) for marker in legends_markers):
            return False
            
        # Check for Canon-only or Legends-only sources
        if re.search(r"Star Wars: (Episode VII|The Force Awakens|The Last Jedi|The Rise of Skywalker|Rogue One|Solo)", text):
            return True
            
        if re.search(r"(Tales of the Jedi|Dark Empire|Shadows of the Empire|Heir to the Empire)", text):
            return False
            
        # Check for default assumption based on titles for common content
        if re.match(r".*(TPM|AOTC|ROTS|ANH|ESB|ROTJ|TFA|TLJ|TROS|Rebels|Resistance|Mandalorian)", title, re.IGNORECASE):
            return True
            
        # For split articles with tabs, default to processing both
        if re.search(r"tabsection", text) or "{{Tab" in text:
            return True
            
        # Default to processing the content
        return True
    
    def process_page(self, page: ET.Element) -> Optional[ArticleData]:
        """
        Process a single page from the XML dump.
        
        Args:
            page: XML Element representing a page
            
        Returns:
            ArticleData if processable, None otherwise
        """
        try:
            # Get basic page info
            title = page.find('.//mw:title', self.ns).text
            ns = int(page.find('.//mw:ns', self.ns).text)
            
            # Skip non-article namespaces
            if ns != 0:  # 0 is the main article namespace
                return None
                
            # Get latest revision
            revision = page.find('.//mw:revision', self.ns)
            if revision is None:
                return None
                
            revision_id = revision.find('.//mw:id', self.ns).text
            content = revision.find('.//mw:text', self.ns).text or ""
            
            # Skip redirects
            if content.lower().startswith('#redirect'):
                return None
                
            # Extract categories
            categories = self._extract_categories(content)
            
            # Determine if content is canonical
            is_canonical = self._is_canonical_content(title, categories, content)
            
            # Convert wiki markup to plain text
            plain_text = self.markup_converter.convert(content)
            
            # Generate URL from title (similar to Wookieepedia URL structure)
            # Replace spaces with underscores, remove special characters
            url_title = title.replace(' ', '_').replace('/', '_').replace('\\', '_')
            url = f"https://starwars.fandom.com/wiki/{url_title}"
            
            return ArticleData(
                title=title,
                content=content,
                plain_text=plain_text,
                categories=categories,
                is_canonical=is_canonical,
                namespace=ns,
                revision_id=revision_id,
                url=url  # Add URL to ArticleData
            )
            
        except Exception as e:
            logger.error(f"Error processing page {title if 'title' in locals() else 'unknown'}: {e}")
            return None
    
    def process_dump(self, batch_size: int = 1000) -> Generator[list[ArticleData], None, None]:
        """
        Process the XML dump in batches.
        
        Args:
            batch_size: Number of articles to process per batch
            
        Yields:
            Lists of processed ArticleData objects
        """
        current_batch = []
        context = ET.iterparse(self.dump_path, events=('end',))
        
        # Statistics
        total_articles = 0
        content_articles = 0
        
        try:
            for event, elem in context:
                if elem.tag.endswith('page'):
                    self.processed_count += 1
                    
                    # Process the page
                    article_data = self.process_page(elem)
                    
                    if article_data:
                        content_articles += 1
                        
                        # Update statistics
                        if article_data.is_canonical:
                            self.canon_count += 1
                        else:
                            self.legends_count += 1
                            
                        current_batch.append(article_data)
                        
                        # Yield batch if full
                        if len(current_batch) >= batch_size:
                            yield current_batch
                            current_batch = []
                            
                    # Log progress
                    if self.processed_count % 1000 == 0:
                        logger.info(
                            f"Processed {self.processed_count:,} pages "
                            f"(Content pages: {content_articles:,}, "
                            f"Canon: {self.canon_count:,}, "
                            f"Legends: {self.legends_count:,})"
                        )
                    
                    # Clear element to free memory
                    elem.clear()
                    
            # Yield final batch if any
            if current_batch:
                yield current_batch
                
        except Exception as e:
            logger.error(f"Error processing dump: {e}")
            if current_batch:
                yield current_batch
    
    def save_batch(self, batch: list[ArticleData], batch_num: int):
        """Save a batch of processed articles to individual JSON files."""
        batch_dir = self.output_dir / f"batch_{batch_num:04d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        
        for article in batch:
            # Convert ArticleData to dictionary
            article_dict = asdict(article)
            # Convert set to list for JSON serialization
            article_dict['categories'] = list(article_dict['categories'])
            
            # Create safe filename from article title
            safe_title = article.title.replace('/', '_').replace('\\', '_')
            filename = f"{safe_title}.json"
            file_path = batch_dir / filename
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(article_dict, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Saved {len(batch)} individual article files to {batch_dir}")
            
def main():
    parser = argparse.ArgumentParser(description='Process Wookieepedia XML dump')
    parser.add_argument('dump_path', help='Path to the XML dump file')
    parser.add_argument('output_dir', help='Directory for output files')
    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='Number of articles per batch (default: 1000)'
    )
    args = parser.parse_args()
    
    processor = WikiDumpProcessor(args.dump_path, args.output_dir)
    
    try:
        for batch_num, batch in enumerate(processor.process_dump(args.batch_size), 1):
            processor.save_batch(batch, batch_num)
            
        logger.info("\nProcessing complete!")
        logger.info(f"Total pages processed: {processor.processed_count:,}")
        logger.info(f"Content articles: {processor.canon_count + processor.legends_count:,}")
        logger.info(f"Canon articles: {processor.canon_count:,}")
        logger.info(f"Legends articles: {processor.legends_count:,}")
        
    except KeyboardInterrupt:
        logger.info("\nProcessing interrupted!")
        logger.info(f"Processed {processor.processed_count:,} pages before stopping")
        logger.info(f"Content articles: {processor.canon_count + processor.legends_count:,}")
        logger.info(f"Canon articles: {processor.canon_count:,}")
        logger.info(f"Legends articles: {processor.legends_count:,}")
        
if __name__ == '__main__':
    main() 