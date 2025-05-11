"""
Tests for the WikiDumpProcessor class.
"""

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

# Add parent directory to Python path
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.holocron.wiki_processing.process_wiki_dump import WikiDumpProcessor

@pytest.fixture
def sample_xml():
    """Create a temporary XML file with sample Wookieepedia content."""
    xml_content = dedent("""<?xml version="1.0" encoding="UTF-8"?>
    <mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">
        <page>
            <title>DJ R3X</title>
            <ns>0</ns>
            <revision>
                <id>12345</id>
                <text>'''DJ R3X''' is a droid character from [[Star Wars: Galaxy's Edge]].
[[Category:Canon articles]]
[[Category:Droids]]

== Description ==
DJ R3X is an RX-series pilot droid who works as a DJ at [[Oga's Cantina]].

=== History ===
* Former Star Tours pilot
* Now works as a DJ
* Plays music in the cantina

== References ==
* Source 1
* Source 2</text>
            </revision>
        </page>
        <page>
            <title>Oga's Cantina</title>
            <ns>0</ns>
            <revision>
                <id>67890</id>
                <text>'''Oga's Cantina''' is a location in [[Star Wars: Galaxy's Edge]].
[[Category:Canon articles]]
[[Category:Locations]]

== Description ==
A popular establishment in Black Spire Outpost.

== Notable Features ==
* DJ R3X performs here
* Serves various drinks

== References ==
* Source A
* Source B</text>
            </revision>
        </page>
        <page>
            <title>Star Tours</title>
            <ns>0</ns>
            <revision>
                <id>11111</id>
                <text>#REDIRECT [[Star Tours – The Adventures Continue]]</text>
            </revision>
        </page>
    </mediawiki>
    """).strip()
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(xml_content)
        return f.name

@pytest.fixture
def processor(sample_xml, tmp_path):
    """Create a WikiDumpProcessor instance with sample data."""
    return WikiDumpProcessor(sample_xml, str(tmp_path))

@pytest.mark.asyncio
async def test_collect_urls(processor):
    """Test collecting URLs from XML dump."""
    urls = await processor._collect_urls()
    
    expected_urls = {
        "https://starwars.fandom.com/wiki/DJ_R3X",
        "https://starwars.fandom.com/wiki/Oga's_Cantina",
        "https://starwars.fandom.com/wiki/Star_Tours"
    }
    
    assert urls == expected_urls

@pytest.mark.asyncio
async def test_process_dump(processor):
    """Test processing the entire dump."""
    # Process the dump
    await processor.process_dump()
    
    # Verify statistics
    assert processor.total_processed > 0
    assert processor.total_articles >= processor.total_processed

@pytest.mark.asyncio
async def test_batch_processing(processor):
    """Test processing articles in batches."""
    # First collect URLs
    urls = await processor._collect_urls()
    
    # Process in batches (batch_size=2)
    await processor._process_urls_in_batches(urls)
    
    # Should have processed all articles
    assert processor.total_processed == 3
    
    # Check batch processing created correct number of batches
    # With 3 articles and batch_size=2, should have 2 batches:
    # Batch 1: 2 articles
    # Batch 2: 1 article
    status_manager = processor.status_manager
    assert len(status_manager) == 3
    assert all(status.processed for status in status_manager.values())

@pytest.mark.asyncio
async def test_error_handling(processor):
    """Test error handling during processing."""
    # Override _extract_article_content to simulate errors
    async def mock_extract_content(url: str) -> str:
        if "Oga" in url:
            raise Exception("Test error")
        return "Content"
    
    processor._extract_article_content = mock_extract_content
    
    # Process dump
    await processor.process_dump()
    
    # Check error was recorded
    failed = processor.get_failed_articles()
    assert len(failed) == 1
    assert any("Oga" in url for _, url, _ in failed)

@pytest.mark.asyncio
async def test_deleted_article_handling(processor):
    """Test handling of deleted articles."""
    # First process all articles
    await processor.process_dump()
    
    # Create new XML with one article removed
    xml_content = dedent("""<?xml version="1.0" encoding="UTF-8"?>
    <mediawiki xmlns="http://www.mediawiki.org/xml/export-0.10/">
        <page>
            <title>DJ R3X</title>
            <ns>0</ns>
            <revision>
                <text>DJ R3X is a droid character...</text>
            </revision>
        </page>
        <page>
            <title>Star Tours</title>
            <ns>0</ns>
            <revision>
                <text>Star Tours is an attraction...</text>
            </revision>
        </page>
    </mediawiki>
    """).strip()
    
    # Create new temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(xml_content)
        new_xml = f.name
    
    # Process new dump
    new_processor = WikiDumpProcessor(new_xml, "dummy_output")
    await new_processor.process_dump()
    
    # Verify Oga's Cantina is marked as deleted
    deleted = new_processor.get_deleted_articles()
    assert len(deleted) == 1
    assert any("Oga" in url for _, url in deleted)
    
    # Cleanup
    os.unlink(new_xml)

def test_get_title_from_url():
    """Test extracting title from URL."""
    processor = WikiDumpProcessor("dummy.xml", "dummy_output")
    
    url = "https://starwars.fandom.com/wiki/DJ_R3X"
    assert processor._get_title_from_url(url) == "DJ R3X"
    
    url = "https://starwars.fandom.com/wiki/Oga's_Cantina"
    assert processor._get_title_from_url(url) == "Oga's Cantina"

@pytest.mark.asyncio
async def test_extract_article_content(processor):
    """Test extraction of article content."""
    # Test DJ R3X article
    content = await processor._extract_article_content("https://starwars.fandom.com/wiki/DJ_R3X")
    assert content is not None
    assert content["title"] == "DJ R3X"
    assert content["is_canonical"] is True
    assert "Category:Canon articles" in content["categories"]
    assert "Category:Droids" in content["categories"]
    assert len(content["sections"]) == 2  # Introduction and Description
    assert "DJ R3X is a droid character" in content["plain_text"]
    assert "Former Star Tours pilot" in content["plain_text"]
    
    # Test Oga's Cantina article
    content = await processor._extract_article_content("https://starwars.fandom.com/wiki/Oga's_Cantina")
    assert content is not None
    assert content["title"] == "Oga's Cantina"
    assert content["is_canonical"] is True
    assert "Category:Canon articles" in content["categories"]
    assert "Category:Locations" in content["categories"]
    assert "popular establishment in Black Spire Outpost" in content["plain_text"]
    
    # Test redirect article
    content = await processor._extract_article_content("https://starwars.fandom.com/wiki/Star_Tours")
    assert content is None 