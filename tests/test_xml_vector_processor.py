"""
Tests for the XML Vector Processor.
"""

import os
import sys
import asyncio
import tempfile
from pathlib import Path
from textwrap import dedent
import json
import pytest
from unittest.mock import Mock, patch, AsyncMock

# Add scripts directory to Python path
scripts_dir = str(Path(__file__).parent.parent / 'scripts')
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from scripts.xml_vector_processor import XMLVectorProcessor

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
    </mediawiki>
    """).strip()
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(xml_content)
        return f.name

@pytest.fixture
def temp_vectors_dir():
    """Create a temporary directory for vector storage."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir

@pytest.fixture
def processor(sample_xml, temp_vectors_dir):
    """Create an XMLVectorProcessor instance with sample data."""
    return XMLVectorProcessor(
        xml_file=sample_xml,
        batch_size=2,
        workers=1,
        vectors_dir=temp_vectors_dir
    )

@pytest.mark.asyncio
async def test_process_batch(processor):
    """Test processing a batch of URLs."""
    urls = [
        "https://starwars.fandom.com/wiki/DJ_R3X",
        "https://starwars.fandom.com/wiki/Oga's_Cantina"
    ]
    
    # Mock the vector generation to avoid actual API calls
    with patch('holocron.knowledge.local_processor.LocalDataProcessor.process_and_upload',
               new_callable=AsyncMock) as mock_process:
        mock_process.return_value = True
        
        # Process the batch
        parquet_file = await processor.process_batch(urls, 1, 1)
        
        # Verify the results
        assert parquet_file is not None
        assert mock_process.called
        
        # Check that content was extracted correctly
        call_args = mock_process.call_args[0][0]
        assert len(call_args) == 2  # Two articles
        
        # Check first article (DJ R3X)
        assert call_args[0]['title'] == 'DJ R3X'
        assert 'droid character' in call_args[0]['content']
        assert 'Category:Canon articles' in call_args[0]['metadata']['categories']
        
        # Check second article (Oga's Cantina)
        assert call_args[1]['title'] == "Oga's Cantina"
        assert 'popular establishment' in call_args[1]['content']
        assert 'Category:Locations' in call_args[1]['metadata']['categories']

@pytest.mark.asyncio
async def test_upload_vectors(processor):
    """Test uploading vectors to Pinecone."""
    # Create a mock Parquet file
    test_file = Path(processor.vectors_dir) / "test_vectors.parquet"
    test_file.touch()
    
    # Mock the Pinecone upload
    with patch('scripts.upload_to_pinecone.PineconeUploader.upload_file_async',
               new_callable=AsyncMock) as mock_upload:
        mock_upload.return_value = True
        
        # Test successful upload
        success = await processor.upload_vectors(str(test_file))
        assert success
        assert mock_upload.called
        
        # Test failed upload
        mock_upload.side_effect = Exception("Upload failed")
        success = await processor.upload_vectors(str(test_file))
        assert not success

@pytest.mark.asyncio
async def test_run_pipeline(processor):
    """Test running the complete processing pipeline."""
    # Mock the necessary components
    with patch('scripts.upload_to_pinecone.PineconeUploader.init_index') as mock_init, \
         patch('scripts.xml_vector_processor.XMLVectorProcessor.process_batch',
               new_callable=AsyncMock) as mock_process, \
         patch('scripts.xml_vector_processor.XMLVectorProcessor.upload_vectors',
               new_callable=AsyncMock) as mock_upload:
        
        # Set up mock returns
        mock_process.return_value = "test_vectors.parquet"
        mock_upload.return_value = True
        
        # Run the pipeline
        await processor.run()
        
        # Verify the pipeline steps
        assert mock_init.called
        assert mock_process.called
        assert mock_upload.called
        
        # Check that status was updated
        status_manager = processor.wiki_processor.status_manager
        assert len(status_manager.processed_urls) > 0

@pytest.mark.asyncio
async def test_error_handling(processor):
    """Test error handling in the processing pipeline."""
    # Test with invalid XML content
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml') as bad_xml:
        bad_xml.write("Invalid XML content")
        bad_xml.flush()
        
        processor.xml_file = Path(bad_xml.name)
        
        with pytest.raises(Exception):
            await processor.run()
            
    # Test with processing failure
    with patch('holocron.knowledge.local_processor.LocalDataProcessor.process_and_upload',
               new_callable=AsyncMock) as mock_process:
        mock_process.return_value = False
        
        urls = ["https://starwars.fandom.com/wiki/DJ_R3X"]
        result = await processor.process_batch(urls, 1, 1)
        assert result is None 