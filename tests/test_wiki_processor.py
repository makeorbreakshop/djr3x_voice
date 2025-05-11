#!/usr/bin/env python3
"""
Test suite for the Wookieepedia XML processing pipeline.

This module tests both the XML processor and markup converter
to ensure they work correctly together.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Set

# Add scripts directory to Python path
scripts_dir = str(Path(__file__).parent.parent / 'scripts')
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from process_wiki_dump import WikiDumpProcessor, ArticleData
from wiki_markup_converter import WikiMarkupConverter

class TestWikiProcessor(unittest.TestCase):
    """Test cases for the wiki processing pipeline."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test resources."""
        # Create a temporary directory for test outputs
        cls.temp_dir = tempfile.mkdtemp()
        
        # Create a small test XML file
        cls.test_xml = Path(cls.temp_dir) / "test_dump.xml"
        cls._create_test_xml()
        
    @classmethod
    def tearDownClass(cls):
        """Clean up test resources."""
        # Remove temporary test files
        if cls.test_xml.exists():
            cls.test_xml.unlink()
            
        # Remove output directory and its contents
        output_dir = Path(cls.temp_dir) / "output"
        if output_dir.exists():
            for file in output_dir.iterdir():
                file.unlink()
            output_dir.rmdir()
            
        # Remove temp directory
        os.rmdir(cls.temp_dir)
        
    @classmethod
    def _create_test_xml(cls):
        """Create a test XML file with sample articles."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">
  <page>
    <title>Test Canon Article</title>
    <ns>0</ns>
    <revision>
      <id>12345</id>
      <text>'''Test Article''' about [[Star Wars]] Canon content.
[[Category:Canon articles]]

== Description ==
This is a test article with some ''wiki markup''.

=== Details ===
* Item 1
* Item 2

{| class="wikitable"
! Header 1
! Header 2
|-
| Cell 1
| Cell 2
|}

[[Category:Test]]</text>
    </revision>
  </page>
  <page>
    <title>Test Legends Article</title>
    <ns>0</ns>
    <revision>
      <id>67890</id>
      <text>'''Legends Article''' about [[Star Wars]].
[[Category:Legends articles]]

== Content ==
This is a legends article.

* Point 1
* Point 2

[[Category:Test]]</text>
    </revision>
  </page>
  <page>
    <title>Test Redirect</title>
    <ns>0</ns>
    <revision>
      <id>11111</id>
      <text>#REDIRECT [[Test Canon Article]]</text>
    </revision>
  </page>
</mediawiki>"""
        
        with open(cls.test_xml, 'w') as f:
            f.write(xml_content)
            
    def setUp(self):
        """Set up test cases."""
        self.output_dir = Path(self.temp_dir) / "output"
        self.processor = WikiDumpProcessor(str(self.test_xml), str(self.output_dir))
        
    def test_canon_detection(self):
        """Test Canon content detection logic."""
        # Test explicit Canon category
        categories = {"Category:Canon articles", "Category:Test"}
        text = "Some regular content"
        self.assertTrue(self.processor._is_canonical_content(categories, text))
        
        # Test Legends category (should be False)
        categories = {"Category:Legends articles", "Category:Test"}
        self.assertFalse(self.processor._is_canonical_content(categories, text))
        
        # Test content-based detection
        categories = {"Category:Test"}
        text = "This is part of the Star Wars Canon timeline"
        self.assertTrue(self.processor._is_canonical_content(categories, text))
        
    def test_category_extraction(self):
        """Test category extraction from wiki text."""
        text = """Some content
[[Category:Test1]]
More content
[[Category:Test2|sort key]]
[[Category:Test3]]"""
        
        expected = {"Category:Test1", "Category:Test2", "Category:Test3"}
        result = self.processor._extract_categories(text)
        self.assertEqual(result, expected)
        
    def test_markup_conversion(self):
        """Test wiki markup conversion to plain text."""
        converter = WikiMarkupConverter()
        
        # Test basic formatting
        wiki_text = "'''Bold''' and ''italic'' text"
        plain_text = converter.convert(wiki_text)
        self.assertEqual(plain_text, "Bold and italic text")
        
        # Test links
        wiki_text = "A [[link|display text]] and an [http://example.com external link]"
        plain_text = converter.convert(wiki_text)
        self.assertEqual(plain_text, "A display text and an external link")
        
        # Test sections
        wiki_text = "== Section ==\nContent\n=== Subsection ===\nMore content"
        plain_text = converter.convert(wiki_text)
        self.assertIn("Section", plain_text)  # Section title should be present
        self.assertIn("Content", plain_text)  # Content should be preserved
        self.assertIn("Subsection", plain_text)  # Subsection title should be present
        self.assertIn("More content", plain_text)  # Content should be preserved
        
    def test_full_pipeline(self):
        """Test the complete processing pipeline."""
        # Process the test XML file
        batches = list(self.processor.process_dump(batch_size=10))
        
        # Should have one batch with two articles (excluding redirect)
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        
        # Verify article count
        self.assertEqual(len(batch), 2)
        
        # Find the Canon article
        canon_article = next(
            (article for article in batch if article.is_canonical),
            None
        )
        self.assertIsNotNone(canon_article)
        
        # Verify Canon article content
        self.assertEqual(canon_article.title, "Test Canon Article")
        self.assertTrue("Category:Canon articles" in canon_article.categories)
        
        # Verify plain text conversion
        self.assertIn("Test Article about Star Wars Canon content", canon_article.plain_text)
        self.assertIn("Description", canon_article.plain_text)  # Section title should be present
        self.assertIn("This is a test article with some wiki markup", canon_article.plain_text)
        self.assertIn("Details", canon_article.plain_text)  # Section title should be present
        self.assertIn("Item 1", canon_article.plain_text)
        self.assertIn("Item 2", canon_article.plain_text)
        self.assertIn("Cell 1", canon_article.plain_text)
        self.assertIn("Cell 2", canon_article.plain_text)
        
        # Verify no HTML or wiki markup remains
        self.assertNotIn("'''", canon_article.plain_text)
        self.assertNotIn("[[", canon_article.plain_text)
        self.assertNotIn("]]", canon_article.plain_text)
        self.assertNotIn("{|", canon_article.plain_text)
        self.assertNotIn("|}", canon_article.plain_text)
        
if __name__ == '__main__':
    unittest.main() 