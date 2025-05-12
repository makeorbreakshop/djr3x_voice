#!/usr/bin/env python3
"""
Analyze Wookiepedia XML dump content types to count the effect of filtering.

This script processes a MediaWiki XML dump file and identifies different
content types like redirects, disambiguation pages, stubs, and meta/utility pages.
It provides statistics on what percentage of content would be filtered out
with different filtering strategies.
"""

import os
import re
import logging
import argparse
import asyncio
import sys
from typing import Dict, Set, Tuple, List, Optional
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor

# Add src directory to Python path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from holocron.wiki_processing import WikiMarkupConverter, ContentFilter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ContentTypeStats:
    """Statistics about content types in the XML dump."""
    total_pages: int = 0
    namespace_0_pages: int = 0
    redirects: int = 0
    disambiguation_pages: int = 0
    stub_articles: int = 0
    meta_utility_pages: int = 0
    canon_content: int = 0
    legends_content: int = 0
    other_content: int = 0
    template_heavy_pages: int = 0
    short_pages: int = 0
    filtered_content: int = 0

class XMLContentAnalyzer:
    """Analyzes content types in MediaWiki XML dumps."""
    
    def __init__(self, dump_path: str):
        """
        Initialize the content analyzer.
        
        Args:
            dump_path: Path to the XML dump file
        """
        self.dump_path = dump_path
        self.markup_converter = WikiMarkupConverter()
        self.content_filter = ContentFilter()  # Use our improved content filter
        self.stats = ContentTypeStats()
        
        # XML namespaces
        self.ns = {
            'mw': 'http://www.mediawiki.org/xml/export-0.11/'
        }
        
        # Canon & Legends categorization
        self.canon_categories = {"canon articles", "canon", "canon media"}
        self.legends_categories = {"legends articles", "legends", "legends media"}
    
    def _extract_categories(self, text: str) -> Set[str]:
        """Extract categories from article text."""
        categories = set()
        category_pattern = r'\[\[Category:([^\]|]+)(?:\|[^\]]+)?\]\]'
        matches = re.finditer(category_pattern, text, re.IGNORECASE)
        
        for match in matches:
            category = match.group(1).strip().lower()
            categories.add(category)
            
        return categories
    
    def _is_canonical_content(self, categories: Set[str], content: str) -> Optional[bool]:
        """
        Determine if content is Canon or Legends based on categories and content.
        
        Returns:
            True if Canon, False if Legends, None if neither
        """
        # Check if article is explicitly marked Canon or Legends
        for category in categories:
            for canon_term in self.canon_categories:
                if canon_term in category:
                    return True
            
            for legends_term in self.legends_categories:
                if legends_term in category:
                    return False
        
        # Check content for Canon/Legends indicators
        if "{{Canon}}" in content or "{{Canon article}}" in content:
            return True
            
        if "{{Legends}}" in content or "{{Legends article}}" in content:
            return False
            
        # If no clear indicators, return None
        return None
    
    async def analyze_dump(self) -> ContentTypeStats:
        """
        Analyze the XML dump and return statistics.
        
        Returns:
            ContentTypeStats object with analysis results
        """
        def parse_xml():
            logger.info(f"Starting XML analysis from {self.dump_path}")
            try:
                # Parse XML with iterparse for memory efficiency
                context = ET.iterparse(self.dump_path, events=('end',))
                
                for i, (event, elem) in enumerate(context):
                    if elem.tag.endswith('page'):
                        self.stats.total_pages += 1
                        
                        # Progress logging
                        if self.stats.total_pages % 10000 == 0:
                            logger.info(f"Processed {self.stats.total_pages:,} pages...")
                            
                        # Process the page
                        self._process_page(elem)
                        
                        # Clear element to save memory
                        elem.clear()
                
                logger.info(f"Finished XML parsing, analyzed {self.stats.total_pages:,} total pages")
                return self.stats
                
            except Exception as e:
                logger.error(f"Error during XML parsing: {e}")
                return self.stats
        
        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, parse_xml)
        
        return result
    
    def _process_page(self, page: ET.Element) -> None:
        """Process a single page from the XML dump."""
        try:
            # Get basic page info
            title_elem = page.find('.//mw:title', self.ns)
            ns_elem = page.find('.//mw:ns', self.ns)
            
            if title_elem is None or ns_elem is None:
                return
                
            title = title_elem.text
            ns = int(ns_elem.text)
            
            # Only analyze main namespace (0)
            if ns == 0:
                self.stats.namespace_0_pages += 1
                
                # Get latest revision
                revision = page.find('.//mw:revision', self.ns)
                if revision is None:
                    return
                    
                text_elem = revision.find('.//mw:text', self.ns)
                if text_elem is None:
                    return
                    
                content = text_elem.text or ""
                
                # Convert wiki markup to plain text for better content analysis
                plain_text = self.markup_converter.convert(content)
                
                # Use our content filter to check page type
                should_process, reason = self.content_filter.should_process(title, content, plain_text)
                
                # Update stats based on page type
                if reason == "redirect":
                    self.stats.redirects += 1
                    self.stats.filtered_content += 1
                    return
                    
                if reason == "disambiguation":
                    self.stats.disambiguation_pages += 1
                    self.stats.filtered_content += 1
                    return
                    
                if reason == "stub":
                    self.stats.stub_articles += 1
                    self.stats.filtered_content += 1
                    return
                    
                if reason == "meta_utility":
                    self.stats.meta_utility_pages += 1
                    self.stats.filtered_content += 1
                    return
                
                # This is a real content page, check Canon/Legends status
                categories = self._extract_categories(content)
                canon_status = self._is_canonical_content(categories, content)
                
                if canon_status is True:
                    self.stats.canon_content += 1
                elif canon_status is False:
                    self.stats.legends_content += 1
                else:
                    self.stats.other_content += 1
                
        except Exception as e:
            logger.error(f"Error processing page {title if 'title' in locals() else 'unknown'}: {e}")
    
    def print_stats(self) -> None:
        """Print analysis statistics."""
        logger.info(f"\n=== Wookieepedia XML Dump Analysis ===")
        logger.info(f"Total Pages: {self.stats.total_pages:,}")
        logger.info(f"Namespace 0 (Content) Pages: {self.stats.namespace_0_pages:,}")
        logger.info(f"\n--- Content Types ---")
        logger.info(f"Redirects: {self.stats.redirects:,} ({self.stats.redirects / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"Disambiguation Pages: {self.stats.disambiguation_pages:,} ({self.stats.disambiguation_pages / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"Stub Articles: {self.stats.stub_articles:,} ({self.stats.stub_articles / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"Meta/Utility Pages: {self.stats.meta_utility_pages:,} ({self.stats.meta_utility_pages / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"\n--- Content Classification ---")
        logger.info(f"Canon Content: {self.stats.canon_content:,} ({self.stats.canon_content / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"Legends Content: {self.stats.legends_content:,} ({self.stats.legends_content / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"Other Content: {self.stats.other_content:,} ({self.stats.other_content / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"\n--- Filter Impact ---")
        logger.info(f"Total Filtered Content: {self.stats.filtered_content:,} ({self.stats.filtered_content / self.stats.namespace_0_pages * 100:.1f}%)")
        logger.info(f"Remaining Content Pages: {self.stats.namespace_0_pages - self.stats.filtered_content:,} ({(self.stats.namespace_0_pages - self.stats.filtered_content) / self.stats.namespace_0_pages * 100:.1f}%)")
    
    def save_stats(self, output_file: str) -> None:
        """Save statistics to a JSON file."""
        # Convert stats to dictionary
        stats_dict = self.stats.__dict__
        
        # Add percentage calculations
        if self.stats.namespace_0_pages > 0:
            pct = {
                "redirects_pct": self.stats.redirects / self.stats.namespace_0_pages * 100,
                "disambiguation_pct": self.stats.disambiguation_pages / self.stats.namespace_0_pages * 100,
                "stub_pct": self.stats.stub_articles / self.stats.namespace_0_pages * 100,
                "meta_utility_pct": self.stats.meta_utility_pages / self.stats.namespace_0_pages * 100,
                "canon_pct": self.stats.canon_content / self.stats.namespace_0_pages * 100,
                "legends_pct": self.stats.legends_content / self.stats.namespace_0_pages * 100,
                "other_pct": self.stats.other_content / self.stats.namespace_0_pages * 100,
                "filtered_pct": self.stats.filtered_content / self.stats.namespace_0_pages * 100,
                "remaining_pct": (self.stats.namespace_0_pages - self.stats.filtered_content) / self.stats.namespace_0_pages * 100,
            }
            stats_dict.update(pct)
        
        with open(output_file, 'w') as f:
            json.dump(stats_dict, f, indent=2)
            
        logger.info(f"Statistics saved to {output_file}")

async def main():
    parser = argparse.ArgumentParser(description='Analyze Wookieepedia XML dump content types')
    parser.add_argument('dump_path', help='Path to the XML dump file')
    parser.add_argument('--output', help='Path to save statistics JSON', default='analysis_results/content_type_stats.json')
    args = parser.parse_args()
    
    analyzer = XMLContentAnalyzer(args.dump_path)
    
    try:
        await analyzer.analyze_dump()
        analyzer.print_stats()
        
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        analyzer.save_stats(args.output)
        
    except KeyboardInterrupt:
        logger.info("\nAnalysis interrupted!")
        logger.info(f"Processed {analyzer.stats.total_pages:,} pages before stopping")
        
if __name__ == '__main__':
    asyncio.run(main()) 