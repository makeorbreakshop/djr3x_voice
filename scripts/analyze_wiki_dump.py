#!/usr/bin/env python3
"""
Analyze MediaWiki XML dump for content distribution.
Focuses on matching Wookieepedia's stated content page count.
"""

import os
import re
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WikiDumpAnalyzer:
    """Analyzes MediaWiki XML dumps to count content pages."""

    def __init__(self, dump_path: str):
        """Initialize the analyzer."""
        self.dump_path = dump_path

        # Statistics
        self.total_pages = 0
        self.content_pages = 0 # Pages in namespace 0
        self.other_namespace_pages = 0 # Pages not in namespace 0

        # XML namespaces
        self.ns = {
            'mw': 'http://www.mediawiki.org/xml/export-0.11/'
        }

    def analyze_dump(self):
        """Analyze the XML dump with progress tracking."""
        console = Console()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console
        ) as progress:

            task = progress.add_task("Analyzing XML dump (content pages only)...", total=None)
            # Use iterparse for memory-efficient parsing
            context = ET.iterparse(self.dump_path, events=('end',))

            try:
                for event, elem in context:
                    # Process only 'page' elements at the end to ensure they are fully parsed
                    if elem.tag == f"{{{self.ns['mw']}}}page": # Check for namespaced tag
                        self.total_pages += 1

                        ns_elem = elem.find('.//mw:ns', self.ns)
                        if ns_elem is not None and ns_elem.text is not None:
                            try:
                                ns_val = int(ns_elem.text)
                                if ns_val == 0:
                                    self.content_pages += 1
                                else:
                                    self.other_namespace_pages += 1
                            except ValueError:
                                logger.warning(f"Could not parse namespace value: {ns_elem.text} for page titled {elem.find('.//mw:title', self.ns).text}")
                                self.other_namespace_pages += 1 # Count as other if ns is invalid
                        else:
                            logger.warning(f"Namespace element not found or empty for page titled {elem.find('.//mw:title', self.ns).text}")
                            self.other_namespace_pages += 1 # Count as other if ns is missing

                        # Update progress every 1000 pages
                        if self.total_pages % 1000 == 0:
                            progress.update(
                                task,
                                description=f"Processed {self.total_pages:,} pages (Content: {self.content_pages:,})"
                            )

                        # Clear element to free memory after processing
                        elem.clear()
                        # Also clear siblings and parent to free more memory
                        # while elem.getprevious() is not None:
                        #     del elem.getparent()[0]

            except ET.ParseError as e:
                logger.error(f"XML Parse Error: {e} at page count {self.total_pages}")
                raise
            except Exception as e:
                logger.error(f"Error processing dump: {e} at page count {self.total_pages}")
                raise

        # Print final statistics
        console.print("\n[bold cyan]Analysis Complete![/bold cyan]")
        console.print(f"Total Pages Processed: {self.total_pages:,}")
        console.print(f"Content Pages (Namespace 0): {self.content_pages:,}")
        console.print(f"Other Namespace Pages: {self.other_namespace_pages:,}")

def main():
    """Run the analysis."""
    import argparse
    parser = argparse.ArgumentParser(description="Analyze Wookieepedia XML dump for content page count.")
    parser.add_argument("dump_path", help="Path to the XML dump file")
    args = parser.parse_args()

    if not os.path.exists(args.dump_path):
        logger.error(f"Dump file not found: {args.dump_path}")
        return

    analyzer = WikiDumpAnalyzer(args.dump_path)
    analyzer.analyze_dump()

if __name__ == "__main__":
    main() 