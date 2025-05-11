#!/usr/bin/env python3
"""
MediaWiki markup to plain text converter.

This module provides functionality to convert MediaWiki markup to clean plain text
while preserving important structural elements and content relationships.
"""

import re
from typing import List, Dict, Set, Optional, Tuple
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WikiMarkupConverter:
    """Converts MediaWiki markup to clean plain text."""
    
    def __init__(self):
        """Initialize the converter with regex patterns."""
        # Basic formatting
        self.bold_italic_pattern = r"'''''(.*?)'''''|\'''(.*?)\'''|''(.*?)''|'(.*?)'"
        self.link_pattern = r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]"
        self.external_link_pattern = r"\[(?:http|https|ftp)[^\[\]]*?\s+([^\]]+)\]"
        
        # Templates and special content
        self.template_pattern = r"\{\{([^{}]*)\}\}"
        self.table_pattern = r"\{\|.*?\|\}"
        self.ref_pattern = r"<ref[^>]*>.*?</ref>"
        self.html_pattern = r"<[^>]+>"
        
        # Section handling
        self.section_pattern = r"^(={2,6})\s*([^=\n]+?)\s*=*\s*$"
        
        # Lists
        self.list_pattern = r"^[*#:;]+ *(.*?)$"
        
        # File/image handling
        self.file_pattern = r"\[\[(File|Image):[^\]]+\]\]"
        
        # Category handling
        self.category_pattern = r"\[\[Category:[^\]]+\]\]"
        
    def _clean_templates(self, text: str) -> str:
        """
        Clean template markup from text while preserving important content.
        
        Args:
            text: Text containing template markup
            
        Returns:
            Cleaned text with templates processed
        """
        def process_template(match: re.Match) -> str:
            content = match.group(1)
            
            # Skip certain templates entirely
            skip_templates = {'cite', 'ref', 'dead link', 'citation needed'}
            if any(template in content.lower() for template in skip_templates):
                return ""
                
            # Extract useful parts from common templates
            parts = [p.strip() for p in content.split('|')]
            if len(parts) > 1:
                # Get the last non-empty part as it often contains the display text
                useful_parts = [p for p in parts[1:] if p and '=' not in p]
                if useful_parts:
                    return useful_parts[-1]
            
            return ""
            
        # Process nested templates from innermost out
        while re.search(self.template_pattern, text):
            text = re.sub(self.template_pattern, process_template, text)
            
        return text
        
    def _clean_links(self, text: str) -> str:
        """
        Convert wiki links to plain text while preserving meaningful content.
        
        Args:
            text: Text containing wiki links
            
        Returns:
            Text with links converted to plain text
        """
        # Process internal links [[page|text]] -> text
        def clean_internal_link(match: re.Match) -> str:
            link = match.group(1)
            if '|' in link:
                return link.split('|')[-1]
            return link
            
        text = re.sub(self.link_pattern, clean_internal_link, text)
        
        # Process external links [http://... text] -> text
        text = re.sub(self.external_link_pattern, r'\1', text)
        
        return text
        
    def _clean_formatting(self, text: str) -> str:
        """
        Remove wiki formatting while preserving content.
        
        Args:
            text: Text with wiki formatting
            
        Returns:
            Clean plain text
        """
        # Remove bold/italic markers
        text = re.sub(self.bold_italic_pattern, r'\1\2\3\4', text)
        
        # Remove HTML tags
        text = re.sub(self.html_pattern, '', text)
        
        # Remove references
        text = re.sub(self.ref_pattern, '', text)
        
        # Remove files/images
        text = re.sub(self.file_pattern, '', text)
        
        # Remove categories
        text = re.sub(self.category_pattern, '', text)
        
        return text
        
    def _process_sections(self, text: str) -> str:
        """
        Process section headers into a clean format.
        
        Args:
            text: Text with wiki section markers
            
        Returns:
            Text with clean section formatting
        """
        def format_section(match):
            level = len(match.group(1))
            title = match.group(2).strip()
            
            if level == 2:
                return f"\n# {title}\n"
            elif level == 3:
                return f"\n## {title}\n"
            else:
                return f"\n### {title}\n"
                
        # Process line by line to handle multiline content
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                match = re.match(self.section_pattern, line)
                if match:
                    lines.append(format_section(match))
                else:
                    lines.append(line)
                    
        return '\n'.join(lines)
        
    def _process_lists(self, text: str) -> str:
        """
        Convert wiki lists to clean text lists.
        
        Args:
            text: Text containing wiki lists
            
        Returns:
            Text with clean list formatting
        """
        def format_list_item(match: re.Match) -> str:
            return f"• {match.group(1)}\n"
            
        lines = text.split('\n')
        processed_lines = []
        
        for line in lines:
            # Only process lines that start with list markers
            if line.strip() and line.lstrip().startswith(('*', '#', ':', ';')):
                processed_lines.append(re.sub(self.list_pattern, format_list_item, line))
            else:
                processed_lines.append(line)
                
        return '\n'.join(processed_lines)
        
    def _clean_tables(self, text: str) -> str:
        """
        Extract useful content from wiki tables.
        
        Args:
            text: Text containing wiki tables
            
        Returns:
            Text with tables converted to plain text
        """
        def process_table(match: re.Match) -> str:
            table = match.group(0)
            
            # Extract cell contents
            cell_pattern = r"\|\s*([^|\n\[\]{}]+)"
            cells = re.findall(cell_pattern, table)
            
            # Filter and clean cells
            clean_cells = []
            for cell in cells:
                cell = cell.strip()
                if cell and not cell.startswith('{') and not cell.startswith('!'):
                    clean_cells.append(cell)
                    
            # Return cells as plain text
            if clean_cells:
                return '\n'.join(clean_cells)
            return ''
            
        return re.sub(self.table_pattern, process_table, text, flags=re.DOTALL)
        
    def _clean_whitespace(self, text: str) -> str:
        """
        Clean up whitespace in text.
        
        Args:
            text: Text to clean
            
        Returns:
            Text with clean whitespace
        """
        # Replace multiple newlines with double newline
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Replace multiple spaces with single space
        text = re.sub(r' +', ' ', text)
        
        # Clean up spaces around newlines
        text = re.sub(r' *\n *', '\n', text)
        
        return text.strip()
        
    def convert(self, wiki_text: str) -> str:
        """
        Convert wiki markup to clean plain text.
        
        Args:
            wiki_text: Text containing MediaWiki markup
            
        Returns:
            Clean plain text with preserved structure
        """
        # First remove unwanted elements
        text = self._clean_formatting(wiki_text)
        text = self._clean_templates(text)
        text = self._clean_links(text)
        
        # Then process structural elements
        text = self._process_sections(text)
        text = self._clean_tables(text)
        text = self._process_lists(text)
        
        # Finally clean up
        text = self._clean_whitespace(text)
        
        return text
        
def main():
    """Test the converter with sample wiki text."""
    sample = """
    '''Bold text''' and ''italic text'' with [[internal link|display text]] and [http://example.com external link].
    
    == Section heading ==
    Some text with a {{template|param}} and <ref>reference</ref>.
    
    {| class="wikitable"
    ! Header
    |-
    | Cell content
    |}
    
    * List item 1
    * List item 2
    # Numbered item
    
    [[Category:Test]]
    [[File:image.jpg|thumb|Caption]]
    """
    
    converter = WikiMarkupConverter()
    result = converter.convert(sample)
    print("Original:\n", sample)
    print("\nConverted:\n", result)
    
if __name__ == '__main__':
    main() 