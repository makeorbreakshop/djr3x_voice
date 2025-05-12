#!/usr/bin/env python3
"""
Sample Filtered Pages from Wookieepedia XML Dump

This script analyzes the Wookieepedia XML dump and extracts samples of pages
that are being filtered out as "stubs" to help understand filtering issues.
It saves both the page title and content for analysis.
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
from collections import defaultdict

# Add src directory to Python path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from holocron.wiki_processing import WikiMarkupConverter, ContentFilter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FilteredPageSampler:
    """Samples pages that are being filtered out from the XML dump."""
    
    def __init__(self, dump_path: str, output_dir: str, sample_size: int = 50):
        """
        Initialize the sampler.
        
        Args:
            dump_path: Path to the XML dump file
            output_dir: Directory to save sample files
            sample_size: Number of samples to extract per filter type
        """
        self.dump_path = dump_path
        self.output_dir = Path(output_dir)
        self.sample_size = sample_size
        self.markup_converter = WikiMarkupConverter()
        self.content_filter = ContentFilter()
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # XML namespaces
        self.ns = {
            'mw': 'http://www.mediawiki.org/xml/export-0.11/'
        }
        
        # Initialize samples dictionary with all possible filter types
        self.samples = {
            "redirect": [],
            "disambiguation": [],
            "stub": [],
            "meta_utility": []
        }
        
        # Keep track of categories for analysis
        self.category_stats = defaultdict(int)
        self.canon_counts = defaultdict(int)
        self.legends_counts = defaultdict(int)
        
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
        
    def _sample_pages(self):
        """Process the XML dump and collect samples of filtered pages."""
        logger.info(f"Processing XML dump: {self.dump_path}")
        
        # Setup for iterparse to handle large XML files
        context = ET.iterparse(self.dump_path, events=('end',))
        
        processed = 0
        for event, elem in context:
            if elem.tag.endswith('page'):
                processed += 1
                
                # Logging progress
                if processed % 10000 == 0:
                    logger.info(f"Processed {processed:,} pages")
                    
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
                    
                    # Use our content filter to check page type
                    should_process, reason = self.content_filter.should_process(title, content, plain_text)
                    
                    # Extract categories for additional analysis
                    categories = self._extract_categories(content)
                    for category in categories:
                        self.category_stats[category] += 1
                    
                    # Check if this is a Canon or Legends article
                    canon_status = self._is_canonical_content(categories, content)
                    
                    # If this page is being filtered out, check if we want to sample it
                    if not should_process:
                        # Count category distribution across filtered pages
                        if canon_status is True:
                            self.canon_counts[reason] += 1
                        elif canon_status is False:
                            self.legends_counts[reason] += 1
                            
                        # Only collect samples if we haven't reached the sample size
                        if len(self.samples[reason]) < self.sample_size:
                            page_data = {
                                "title": title,
                                "content": content,
                                "plain_text": plain_text,
                                "categories": list(categories),
                                "canon_status": "Canon" if canon_status is True else "Legends" if canon_status is False else "Unknown"
                            }
                            self.samples[reason].append(page_data)
                
                # Clear the element to save memory
                elem.clear()
                
                # Break if we have enough samples for all filter types
                if all(len(samples) >= self.sample_size for samples in self.samples.values()):
                    break
                    
        logger.info(f"Completed processing {processed:,} pages")
                
    def analyze_and_save(self):
        """Analyze the XML dump and save samples of filtered pages."""
        # Sample pages from the dump
        self._sample_pages()
        
        # Save samples to files
        for filter_type, samples in self.samples.items():
            output_file = self.output_dir / f"{filter_type}_samples.json"
            with open(output_file, 'w') as f:
                json.dump(samples, f, indent=2)
            logger.info(f"Saved {len(samples)} samples of {filter_type} pages to {output_file}")
            
        # Save category statistics
        category_file = self.output_dir / "category_stats.json"
        with open(category_file, 'w') as f:
            # Sort categories by frequency
            sorted_categories = sorted(
                self.category_stats.items(), 
                key=lambda x: x[1], 
                reverse=True
            )
            json.dump(dict(sorted_categories), f, indent=2)
        logger.info(f"Saved category statistics to {category_file}")
        
        # Save Canon/Legends statistics
        status_file = self.output_dir / "canon_legends_stats.json"
        with open(status_file, 'w') as f:
            json.dump({
                "canon": dict(self.canon_counts),
                "legends": dict(self.legends_counts)
            }, f, indent=2)
        logger.info(f"Saved Canon/Legends statistics to {status_file}")
        
        # Generate summary report
        summary = {
            "total_samples": {filter_type: len(samples) for filter_type, samples in self.samples.items()},
            "canon_counts": dict(self.canon_counts),
            "legends_counts": dict(self.legends_counts),
            "top_categories": dict(sorted(self.category_stats.items(), key=lambda x: x[1], reverse=True)[:20])
        }
        
        summary_file = self.output_dir / "summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Saved summary to {summary_file}")
    
    def generate_analysis(self):
        """Generate an analysis of the sampled pages with common patterns."""
        # Generate stub analysis to help understand why pages are being filtered as stubs
        stub_samples = self.samples.get("stub", [])
        if not stub_samples:
            logger.warning("No stub samples found for analysis")
            return
            
        # Analyze stub content patterns
        stub_analysis = {
            "content_lengths": [],
            "has_infobox": 0,
            "has_sections": 0,
            "has_references": 0,
            "has_quality_templates": 0,
            "canon_count": 0,
            "legends_count": 0,
            "unknown_count": 0,
            "common_categories": defaultdict(int)
        }
        
        # Simple reference pattern
        ref_pattern = re.compile(r'<ref>.*?</ref>|{{cite', re.IGNORECASE)
        infobox_pattern = re.compile(r'\{\{[Ii]nfobox\s+\w+', re.IGNORECASE)
        section_pattern = re.compile(r'^==([^=].*?)==\s*$', re.MULTILINE)
        quality_template_patterns = [
            re.compile(r'\{\{[Cc]anon\}\}'),
            re.compile(r'\{\{[Ee]ra\|[^}]+\}\}'),
            re.compile(r'\{\{[Ff]action\|[^}]+\}\}'),
            re.compile(r'\{\{[Cc]ite\|[^}]+\}\}'),
            re.compile(r'\{\{[Rr]eference\|[^}]+\}\}'),
            re.compile(r'\{\{[Qq]uote\|[^}]+\}\}')
        ]
        
        for sample in stub_samples:
            # Content length
            content = sample["content"]
            plain_text = sample["plain_text"]
            stub_analysis["content_lengths"].append(len(plain_text.strip()))
            
            # Check for quality features
            if infobox_pattern.search(content):
                stub_analysis["has_infobox"] += 1
                
            if section_pattern.findall(content):
                stub_analysis["has_sections"] += 1
                
            if ref_pattern.search(content):
                stub_analysis["has_references"] += 1
                
            # Check for quality templates
            quality_templates = 0
            for pattern in quality_template_patterns:
                if pattern.search(content):
                    quality_templates += 1
                    
            if quality_templates >= 1:
                stub_analysis["has_quality_templates"] += 1
                
            # Count Canon/Legends status
            if sample["canon_status"] == "Canon":
                stub_analysis["canon_count"] += 1
            elif sample["canon_status"] == "Legends":
                stub_analysis["legends_count"] += 1
            else:
                stub_analysis["unknown_count"] += 1
                
            # Track categories
            for category in sample["categories"]:
                stub_analysis["common_categories"][category] += 1
        
        # Calculate statistics
        if stub_samples:
            total = len(stub_samples)
            stub_analysis["avg_content_length"] = sum(stub_analysis["content_lengths"]) / total
            stub_analysis["min_content_length"] = min(stub_analysis["content_lengths"])
            stub_analysis["max_content_length"] = max(stub_analysis["content_lengths"])
            stub_analysis["infobox_pct"] = stub_analysis["has_infobox"] / total * 100
            stub_analysis["sections_pct"] = stub_analysis["has_sections"] / total * 100
            stub_analysis["references_pct"] = stub_analysis["has_references"] / total * 100
            stub_analysis["quality_templates_pct"] = stub_analysis["has_quality_templates"] / total * 100
            stub_analysis["canon_pct"] = stub_analysis["canon_count"] / total * 100
            stub_analysis["legends_pct"] = stub_analysis["legends_count"] / total * 100
            stub_analysis["unknown_pct"] = stub_analysis["unknown_count"] / total * 100
            
        # Save analysis to file
        stub_analysis["common_categories"] = dict(sorted(
            stub_analysis["common_categories"].items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:30])
        
        analysis_file = self.output_dir / "stub_analysis.json"
        with open(analysis_file, 'w') as f:
            # Convert defaultdict to dict for JSON serialization
            json.dump(stub_analysis, f, indent=2)
        logger.info(f"Saved stub analysis to {analysis_file}")

def main():
    parser = argparse.ArgumentParser(description="Sample filtered pages from Wookieepedia XML dump")
    parser.add_argument("--dump", required=True, help="Path to the Wookieepedia XML dump file")
    parser.add_argument("--output", default="analysis_results/filtered_samples", help="Output directory for samples")
    parser.add_argument("--sample-size", type=int, default=50, help="Number of samples per filter type")
    args = parser.parse_args()
    
    sampler = FilteredPageSampler(args.dump, args.output, args.sample_size)
    sampler.analyze_and_save()
    sampler.generate_analysis()
    
if __name__ == "__main__":
    main() 