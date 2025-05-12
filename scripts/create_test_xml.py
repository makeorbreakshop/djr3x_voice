#!/usr/bin/env python3
"""
Create a small test XML file from a Wookieepedia XML dump.
Extracts just the first few pages to allow for quicker testing.

Usage:
    python scripts/create_test_xml.py [--input-file INPUT_FILE] [--output-file OUTPUT_FILE] [--pages PAGES]
"""

import xml.etree.ElementTree as ET
import argparse
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def extract_sample_pages(input_file, output_file, num_pages=5):
    """
    Extract a small sample of pages from a MediaWiki XML dump.
    
    Args:
        input_file: Path to input XML dump file
        output_file: Path to output small XML file
        num_pages: Number of pages to extract
    """
    logger.info(f"Extracting {num_pages} pages from {input_file} to {output_file}")
    
    # Namespaces used in MediaWiki XML
    namespaces = {
        'mw': 'http://www.mediawiki.org/xml/export-0.11/'
    }
    
    # Create root element for the output XML
    root = ET.Element('mediawiki')
    root.set('xmlns', namespaces['mw'])
    
    # Parse the input file with incremental parsing
    context = ET.iterparse(input_file, events=('end',))
    
    pages_found = 0
    pages_extracted = 0
    siteinfo_added = False
    
    try:
        # First, extract siteinfo
        for event, elem in context:
            if elem.tag.endswith('siteinfo'):
                logger.info("Found and extracted siteinfo element")
                root.append(elem)
                siteinfo_added = True
                break
                
        # Extract content pages (namespace 0)
        for event, elem in context:
            if elem.tag.endswith('page'):
                pages_found += 1
                
                # Extract namespace
                ns_elem = elem.find('.//mw:ns', namespaces)
                
                if ns_elem is not None and ns_elem.text == '0':
                    # This is a content page
                    root.append(elem)
                    pages_extracted += 1
                    logger.info(f"Extracted page {pages_extracted}/{num_pages}")
                    
                    # Stop after extracting enough pages
                    if pages_extracted >= num_pages:
                        break
                else:
                    # Clear element to free memory
                    elem.clear()
                    
                # Log progress
                if pages_found % 1000 == 0:
                    logger.info(f"Scanned {pages_found} pages, extracted {pages_extracted}")
                    
    except Exception as e:
        logger.error(f"Error processing XML: {e}")
        return False
    
    # Write the output XML file
    tree = ET.ElementTree(root)
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    
    logger.info(f"Successfully extracted {pages_extracted} pages to {output_file}")
    logger.info(f"Scanned {pages_found} pages total")
    
    return True

def main():
    parser = argparse.ArgumentParser(description='Create a small test XML file from a Wookieepedia XML dump')
    parser.add_argument('--input-file', default='data/wookiepdia-dump/extracted/wookieepedia.xml', 
                        help='Path to input XML dump file')
    parser.add_argument('--output-file', default='data/wookiepdia-dump/extracted/test_sample.xml',
                        help='Path to output small XML file')
    parser.add_argument('--pages', type=int, default=5, help='Number of pages to extract')
    args = parser.parse_args()
    
    # Ensure output directory exists
    Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
    
    # Extract sample pages
    success = extract_sample_pages(args.input_file, args.output_file, args.pages)
    
    if success:
        logger.info("Test XML file created successfully")
    else:
        logger.error("Failed to create test XML file")

if __name__ == '__main__':
    main() 