#!/usr/bin/env python3
"""
Debug Filtered Pages from Wookieepedia XML Dump

This script uses the debug version of ContentFilter to analyze specific pages
from the Wookieepedia XML dump and provide detailed information about why
they're being filtered incorrectly.
"""

import os
import re
import logging
import argparse
import random
import sys
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add src directory to Python path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from holocron.wiki_processing import WikiMarkupConverter
from holocron.wiki_processing.content_filter_debug import ContentFilterDebug

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PageDebugger:
    """Debugs filtering issues for specific pages in the XML dump."""
    
    def __init__(self, dump_path: str, output_dir: str):
        """
        Initialize the debugger.
        
        Args:
            dump_path: Path to the XML dump file
            output_dir: Directory to save debug results
        """
        self.dump_path = dump_path
        self.output_dir = Path(output_dir)
        self.markup_converter = WikiMarkupConverter()
        self.content_filter = ContentFilterDebug()
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # XML namespaces
        self.ns = {
            'mw': 'http://www.mediawiki.org/xml/export-0.11/'
        }
    
    def _extract_categories(self, text: str):
        """Extract categories from article text."""
        categories = set()
        category_pattern = r'\[\[Category:([^\]|]+)(?:\|[^\]]+)?\]\]'
        matches = re.finditer(category_pattern, text, re.IGNORECASE)
        
        for match in matches:
            category = match.group(1).strip().lower()
            categories.add(category)
            
        return categories
        
    def _is_canonical_content(self, categories, content):
        """Determine if content is Canon, Legends, or unknown."""
        canon_indicators = {"canon articles", "canon"}
        legends_indicators = {"legends articles", "legends"}
        
        # Check categories
        for category in categories:
            if any(indicator in category.lower() for indicator in canon_indicators):
                return True
            if any(indicator in category.lower() for indicator in legends_indicators):
                return False
                
        # Check content for {{canon}} or {{legends}} templates
        if re.search(r'\{\{\s*canon\s*\}\}', content, re.IGNORECASE):
            return True
        if re.search(r'\{\{\s*legends\s*\}\}', content, re.IGNORECASE):
            return False
            
        # Cannot determine
        return None
    
    def find_pages_by_titles(self, titles: List[str]) -> List[Dict[str, Any]]:
        """
        Find specific pages by title in the XML dump and analyze their filtering.
        
        Args:
            titles: List of page titles to find and analyze
            
        Returns:
            List[Dict[str, Any]]: Debug information for each found page
        """
        logger.info(f"Searching for {len(titles)} specific page titles in XML dump")
        
        # Convert titles to lowercase for case-insensitive matching
        titles_lower = [title.lower() for title in titles]
        remaining_titles = set(titles_lower)
        
        # Results storage
        results = []
        
        # Setup for iterparse to handle large XML files
        context = ET.iterparse(self.dump_path, events=('end',))
        
        processed = 0
        for event, elem in context:
            if elem.tag.endswith('page'):
                processed += 1
                
                # Logging progress
                if processed % 10000 == 0:
                    logger.info(f"Processed {processed:,} pages, found {len(results)}/{len(titles)} targets")
                    
                title_elem = elem.find('.//mw:title', self.ns)
                ns_elem = elem.find('.//mw:ns', self.ns)
                
                if title_elem is None or ns_elem is None:
                    elem.clear()
                    continue
                    
                title = title_elem.text
                title_lower = title.lower()
                ns = int(ns_elem.text)
                
                # Check if this is one of our target pages
                if title_lower in remaining_titles and ns == 0:
                    # Get latest revision
                    revision = elem.find('.//mw:revision', self.ns)
                    if revision is None:
                        elem.clear()
                        continue
                        
                    text_elem = revision.find('.//mw:text', self.ns)
                    if text_elem is None:
                        elem.clear()
                        continue
                        
                    content = text_elem.text or ""
                    
                    # Convert wiki markup to plain text for better content analysis
                    plain_text = self.markup_converter.convert(content)
                    
                    # Use debug content filter to check page type with detailed info
                    should_process, reason, debug_info = self.content_filter.should_process_debug(title, content, plain_text)
                    
                    # Extract categories for additional analysis
                    categories = self._extract_categories(content)
                    
                    # Check if this is a Canon or Legends article
                    canon_status = self._is_canonical_content(categories, content)
                    
                    # Add page data to results
                    page_data = {
                        "title": title,
                        "content": content,
                        "plain_text": plain_text,
                        "categories": list(categories),
                        "canon_status": "Canon" if canon_status is True else "Legends" if canon_status is False else "Unknown",
                        "should_process": should_process,
                        "filter_reason": reason,
                        "debug_info": debug_info
                    }
                    results.append(page_data)
                    
                    # Remove from remaining titles
                    remaining_titles.discard(title_lower)
                    
                    logger.info(f"Found and analyzed page: {title}")
                    
                    # Break if we've found all titles
                    if not remaining_titles:
                        break
                
                # Clear the element to save memory
                elem.clear()
                
        logger.info(f"Completed processing {processed:,} pages")
        logger.info(f"Found {len(results)}/{len(titles)} requested pages")
        
        if remaining_titles:
            logger.warning(f"Could not find {len(remaining_titles)} pages: {', '.join(remaining_titles)}")
            
        return results
    
    def find_random_filtered_stubs(self, count: int = 50) -> List[Dict[str, Any]]:
        """
        Find random pages that are being filtered as stubs and analyze them.
        
        Args:
            count: Number of random filtered stub pages to find
            
        Returns:
            List[Dict[str, Any]]: Debug information for random filtered stub pages
        """
        logger.info(f"Finding {count} random pages filtered as stubs")
        
        # Results storage
        results = []
        
        # Setup for iterparse to handle large XML files
        context = ET.iterparse(self.dump_path, events=('end',))
        
        processed = 0
        for event, elem in context:
            if elem.tag.endswith('page'):
                processed += 1
                
                # Logging progress
                if processed % 10000 == 0:
                    logger.info(f"Processed {processed:,} pages, found {len(results)}/{count} stubs")
                    
                title_elem = elem.find('.//mw:title', self.ns)
                ns_elem = elem.find('.//mw:ns', self.ns)
                
                if title_elem is None or ns_elem is None:
                    elem.clear()
                    continue
                    
                title = title_elem.text
                ns = int(ns_elem.text)
                
                # Only analyze main namespace (0)
                if ns == 0:
                    # Get latest revision
                    revision = elem.find('.//mw:revision', self.ns)
                    if revision is None:
                        elem.clear()
                        continue
                        
                    text_elem = revision.find('.//mw:text', self.ns)
                    if text_elem is None:
                        elem.clear()
                        continue
                        
                    content = text_elem.text or ""
                    
                    # Convert wiki markup to plain text for better content analysis
                    plain_text = self.markup_converter.convert(content)
                    
                    # Use debug content filter to check page type with detailed info
                    should_process, reason, debug_info = self.content_filter.should_process_debug(title, content, plain_text)
                    
                    # If this page is filtered as a stub, add it to our results
                    if reason == "stub":
                        # Extract categories for additional analysis
                        categories = self._extract_categories(content)
                        
                        # Check if this is a Canon or Legends article
                        canon_status = self._is_canonical_content(categories, content)
                        
                        # Add page data to results
                        page_data = {
                            "title": title,
                            "content": content,
                            "plain_text": plain_text,
                            "categories": list(categories),
                            "canon_status": "Canon" if canon_status is True else "Legends" if canon_status is False else "Unknown",
                            "should_process": should_process,
                            "filter_reason": reason,
                            "debug_info": debug_info
                        }
                        results.append(page_data)
                        
                        logger.info(f"Found filtered stub: {title}")
                        
                        # Break if we've found enough stubs
                        if len(results) >= count:
                            break
                
                # Clear the element to save memory
                elem.clear()
                
        logger.info(f"Completed processing {processed:,} pages")
        logger.info(f"Found {len(results)}/{count} filtered stub pages")
            
        return results
    
    def save_results(self, results: List[Dict[str, Any]], filename: str):
        """
        Save debug results to a file.
        
        Args:
            results: Debug results to save
            filename: Output filename
        """
        output_file = self.output_dir / filename
        
        # Create a report version with simplified data for easier viewing
        report_data = []
        
        for page in results:
            # Create a simplified version for the report
            report_entry = {
                "title": page["title"],
                "categories": page["categories"],
                "canon_status": page["canon_status"],
                "filter_reason": page["filter_reason"],
                # Include excerpt of content for context (first 300 chars)
                "content_excerpt": page["plain_text"][:300] + "..." if len(page["plain_text"]) > 300 else page["plain_text"],
                "content_length": len(page["plain_text"]),
            }
            
            # Add detailed debug info for stubs
            if page["filter_reason"] == "stub" and "stub_details" in page["debug_info"]:
                stub_details = page["debug_info"]["stub_details"]
                report_entry.update({
                    "text_length": stub_details["text_length"],
                    "min_content_length": stub_details["min_content_length"],
                    "has_explicit_stub_template": stub_details["has_explicit_stub_template"],
                    "has_references": stub_details["has_references"],
                    "has_multiple_sections": stub_details["has_multiple_sections"],
                    "section_count": stub_details["section_count"],
                    "has_infobox": stub_details["has_infobox"],
                    "has_canon_or_era_marker": stub_details["has_canon_or_era_marker"],
                    "quality_template_count": stub_details["quality_template_count"],
                    "failed_checks": stub_details["failed_checks"],
                    "passed_checks": stub_details["passed_checks"]
                })
            
            report_data.append(report_entry)
        
        # Save report version
        report_file = self.output_dir / f"report_{filename}"
        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2)
        logger.info(f"Saved debug report to {report_file}")
        
        # Save full version with complete content
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"Saved full debug results to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Debug filtered pages from Wookieepedia XML dump")
    parser.add_argument("--dump", required=True, help="Path to the Wookieepedia XML dump file")
    parser.add_argument("--output", default="analysis_results/filtered_debug", help="Output directory for debug results")
    parser.add_argument("--titles", nargs="*", default=[], help="Specific page titles to analyze")
    parser.add_argument("--titles-file", help="File containing page titles to analyze, one per line")
    parser.add_argument("--random-stubs", type=int, default=0, help="Number of random stub pages to find and analyze")
    args = parser.parse_args()
    
    debugger = PageDebugger(args.dump, args.output)
    
    # Collect titles from command line and file if provided
    titles = list(args.titles)
    if args.titles_file:
        with open(args.titles_file, 'r') as f:
            titles.extend([line.strip() for line in f if line.strip()])
    
    # Find and analyze specific pages if titles are provided
    if titles:
        logger.info(f"Analyzing {len(titles)} specific page titles")
        results = debugger.find_pages_by_titles(titles)
        debugger.save_results(results, "specific_pages_debug.json")
    
    # Find and analyze random stub pages if requested
    if args.random_stubs > 0:
        logger.info(f"Finding {args.random_stubs} random filtered stub pages")
        results = debugger.find_random_filtered_stubs(args.random_stubs)
        debugger.save_results(results, "random_stubs_debug.json")
    
if __name__ == "__main__":
    main() 