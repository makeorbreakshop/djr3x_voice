#!/usr/bin/env python3
"""
Analyze Canon vs. Legends Distribution

This script scans the Wookieepedia XML dump to count how many articles
would be classified as Canon, Legends, or undetermined using the new
explicit classification logic.

Usage:
    python scripts/analyze_canon_legends_distribution.py [path/to/xml_dump]
"""

import os
import sys
import re
import asyncio
import logging
import argparse
import xml.etree.ElementTree as ET
from typing import Dict, Set, Optional, Tuple
from pathlib import Path
from tqdm import tqdm

# Add project root to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.holocron.wiki_processing import WikiDumpProcessor
from src.holocron.wiki_processing.content_filter import ContentFilter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/canon_legends_analysis.log")
    ]
)
logger = logging.getLogger(__name__)

class CanonLegendsAnalyzer:
    """Analyzes Canon vs. Legends distribution in Wookieepedia XML dump."""
    
    def __init__(self, xml_path: str):
        """
        Initialize analyzer.
        
        Args:
            xml_path: Path to XML dump file
        """
        self.xml_path = xml_path
        self.content_filter = ContentFilter()
        
        # Statistics
        self.total_pages = 0
        self.namespace_0_pages = 0
        self.filtered_pages = 0
        self.valid_content_pages = 0
        self.canon_articles = 0
        self.legends_articles = 0
        self.undetermined_articles = 0
        
        # XML namespace
        self.ns = {
            'mw': 'http://www.mediawiki.org/xml/export-0.11/'
        }
        
    def _extract_categories(self, text: str) -> Set[str]:
        """Extract categories from article text."""
        categories = set()
        
        # Match [[Category:Name]] pattern
        category_pattern = r'\[\[Category:([^\]]+)\]\]'
        matches = re.finditer(category_pattern, text)
        
        for match in matches:
            category = match.group(1).split('|')[0].strip()
            categories.add(category.lower())
            
        return categories
    
    def _is_canonical_content(self, categories: Set[str], text: str) -> Optional[bool]:
        """Determine if content is Canon using explicit markers only."""
        # Look for explicit Canon template
        canon_match = re.search(r'\{\{[Cc]anon\}\}', text)
        if canon_match:
            # Print first few matches for debugging
            if self.canon_articles < 5:
                # Extract some context around the match
                start = max(0, canon_match.start() - 50)
                end = min(len(text), canon_match.end() + 50)
                context = text[start:end]
                logger.info(f"CANON EXAMPLE 1: {context}")
            return True
            
        # Look for Top|can template (Canon)
        canon_top_match = re.search(r'\{\{Top\|can[^}]*\}\}', text)
        if canon_top_match:
            # Print first few matches for debugging
            if self.canon_articles < 10:
                # Extract some context around the match
                start = max(0, canon_top_match.start() - 50)
                end = min(len(text), canon_top_match.end() + 50)
                context = text[start:end]
                logger.info(f"CANON EXAMPLE 2: {context}")
            return True
            
        # Look for canon= parameter in templates
        canon_param_match = re.search(r'\{\{Top\|canon=', text)
        if canon_param_match:
            # Print first few matches for debugging
            if self.canon_articles < 15:
                # Extract some context around the match
                start = max(0, canon_param_match.start() - 50)
                end = min(len(text), canon_param_match.end() + 50)
                context = text[start:end]
                logger.info(f"CANON EXAMPLE 3: {context}")
            return True
            
        # Look for [Category:Canon] mentions
        canon_category = re.search(r'\[\[[Cc]ategory:[Cc]anon', text)
        if canon_category:
            if self.canon_articles < 20:
                start = max(0, canon_category.start() - 50)
                end = min(len(text), canon_category.end() + 50)
                context = text[start:end]
                logger.info(f"CANON EXAMPLE 4: {context}")
            return True

        # Look for explicit references to canon content
        if re.search(r'from a \[\[canon\]\] source', text) or re.search(r'is a \[\[canon\]\] ', text):
            if self.canon_articles < 25:
                context = text[:200]  # Just take beginning for context
                logger.info(f"CANON EXAMPLE 5: {context}")
            return True
            
        # Look for explicit Legends template - try multiple variants
        legends_match = re.search(r'\{\{[Ll]egends\}\}|\{\{[Ss]tar [Ww]ars [Ll]egends\}\}', text)
        if legends_match:
            # Print first few matches for debugging
            if self.legends_articles < 5:
                # Extract some context around the match
                start = max(0, legends_match.start() - 50)
                end = min(len(text), legends_match.end() + 50)
                context = text[start:end]
                logger.info(f"LEGENDS EXAMPLE 1: {context}")
            return False
            
        # Look for Top|leg template (Legends)
        legends_top_match = re.search(r'\{\{Top\|leg[^}]*\}\}', text)
        if legends_top_match:
            # Print first few matches for debugging
            if self.legends_articles < 10:
                # Extract some context around the match
                start = max(0, legends_top_match.start() - 50)
                end = min(len(text), legends_top_match.end() + 50)
                context = text[start:end]
                logger.info(f"LEGENDS EXAMPLE 2: {context}")
            return False
        
        # Look for Category or other indicators
        if re.search(r'\[\[[Cc]ategory:[Cc]anon articles\]\]', text):
            return True
        if re.search(r'\[\[[Cc]ategory:[Ll]egends articles\]\]', text):
            return False
            
        # Deal with special cases where we have more confidence
        # Check for Disney-era content (typically Canon)
        if re.search(r'(?:Disney|Disney XD|Disney\+|Forces of Destiny|Resistance|High Republic|Sequel trilogy)', text, re.IGNORECASE):
            if self.canon_articles < 30:
                context = text[:150]  # Just take beginning for context
                logger.info(f"LIKELY CANON (Disney): {context}")
            return True
            
        # If article mentions "Legends" frequently but doesn't have a proper tag
        if text.count("Legends") > 5 and len(text) < 5000:
            if self.legends_articles < 15:
                context = text[:150]
                logger.info(f"LIKELY LEGENDS (Multiple mentions): {context}")
            return False
            
        # If this is one of the first few undetermined articles, log it
        if self.undetermined_articles < 5:
            # Extract the beginning of the text for debugging
            context = text[:100]
            logger.info(f"UNDETERMINED EXAMPLE: {context}")
                
        # Couldn't determine
        return None
    
    async def analyze(self):
        """Analyze the XML dump and count articles."""
        logger.info(f"Analyzing XML dump: {self.xml_path}")
        logger.info(f"File exists: {os.path.exists(self.xml_path)}")
        
        try:
            # Process the XML dump in streaming mode
            context = ET.iterparse(self.xml_path, events=('end',))
            
            # Use tqdm for progress
            with tqdm(desc="Processing pages", unit="pages") as progress:
                for event, elem in context:
                    if elem.tag.endswith('page'):
                        self.total_pages += 1
                        progress.update(1)
                        
                        # Get basic page info
                        title_elem = elem.find('.//mw:title', self.ns)
                        ns_elem = elem.find('.//mw:ns', self.ns)
                        
                        if title_elem is None or ns_elem is None:
                            elem.clear()
                            continue
                            
                        title = title_elem.text
                        ns = int(ns_elem.text)
                        
                        # Only process main namespace
                        if ns == 0:
                            self.namespace_0_pages += 1
                            
                            # Get content
                            revision = elem.find('.//mw:revision', self.ns)
                            text_elem = revision.find('.//mw:text', self.ns) if revision is not None else None
                            
                            if text_elem is not None and text_elem.text is not None:
                                content = text_elem.text
                                
                                # Create plain text version for filtering
                                plain_text = content  # Simplified for analysis, no conversion
                                
                                # Skip if it should be filtered
                                should_process, _ = self.content_filter.should_process(title, content, plain_text)
                                
                                if should_process:
                                    self.valid_content_pages += 1
                                    
                                    # Check Canon/Legends status
                                    categories = self._extract_categories(content)
                                    is_canon = self._is_canonical_content(categories, content)
                                    
                                    if is_canon is True:
                                        self.canon_articles += 1
                                    elif is_canon is False:
                                        self.legends_articles += 1
                                    else:
                                        self.undetermined_articles += 1
                                else:
                                    self.filtered_pages += 1
                        
                        # Clear the element to save memory
                        elem.clear()
                        
                        # Log progress periodically
                        if self.total_pages % 10000 == 0:
                            logger.info(f"Processed {self.total_pages:,} pages")
                            logger.info(f"Content pages: {self.valid_content_pages:,}")
                            logger.info(f"Canon: {self.canon_articles:,}, Legends: {self.legends_articles:,}, Undetermined: {self.undetermined_articles:,}")
            
            # Print final statistics
            logger.info("\nAnalysis complete!")
            logger.info(f"Total pages: {self.total_pages:,}")
            logger.info(f"Namespace 0 pages: {self.namespace_0_pages:,}")
            logger.info(f"Filtered pages: {self.filtered_pages:,}")
            logger.info(f"Valid content pages: {self.valid_content_pages:,}")
            logger.info(f"Canon articles: {self.canon_articles:,} ({self.canon_articles/self.valid_content_pages*100:.1f}%)")
            logger.info(f"Legends articles: {self.legends_articles:,} ({self.legends_articles/self.valid_content_pages*100:.1f}%)")
            logger.info(f"Undetermined articles: {self.undetermined_articles:,} ({self.undetermined_articles/self.valid_content_pages*100:.1f}%)")
            
        except Exception as e:
            logger.error(f"Error analyzing XML dump: {e}")
            raise

async def main():
    parser = argparse.ArgumentParser(description="Analyze Canon vs. Legends distribution")
    parser.add_argument("xml_path", help="Path to XML dump file")
    args = parser.parse_args()
    
    analyzer = CanonLegendsAnalyzer(args.xml_path)
    await analyzer.analyze()

if __name__ == "__main__":
    asyncio.run(main()) 