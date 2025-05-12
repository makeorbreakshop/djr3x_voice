"""
Local Data Processor for generating vector embeddings from text data.
"""

import os
import json
import logging
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LocalDataProcessor:
    """Processes text data into embeddings for vector search."""
    
    def __init__(self, vectors_dir: str = "data/vectors", chunk_size: int = 1000, chunk_overlap: int = 100):
        """
        Initialize the processor.
        
        Args:
            vectors_dir: Directory for storing vector parquet files
            chunk_size: Maximum size of text chunks for embedding
            chunk_overlap: Number of characters to overlap between chunks
        """
        self.vectors_dir = Path(vectors_dir)
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        # Load environment variables
        load_dotenv()
        
        # Initialize OpenAI client
        self.client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )
        
        logger.info(f"LocalDataProcessor initialized with vectors directory: {vectors_dir}")
        
    def chunk_text(self, text: str, title: str = "") -> List[str]:
        """
        Split text into overlapping chunks for embedding.
        
        Args:
            text: Text to chunk
            title: Optional title to prepend to each chunk
            
        Returns:
            List of text chunks
        """
        if not text:
            return []
            
        # Prepend title if provided
        if title:
            text = f"{title}\n\n{text}"
            
        # Split into chunks with overlap
        chunks = []
        start = 0
        
        while start < len(text):
            # Get chunk with overlap
            end = start + self.chunk_size
            chunk = text[start:end]
            
            # Clean up chunk
            chunk = chunk.strip()
            
            # Add to chunks if not empty
            if chunk:
                chunks.append(chunk)
                
            # Move start position, accounting for overlap
            start = end - self.chunk_overlap
            
        return chunks
        
    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for text using OpenAI API.
        
        Args:
            text: Text to embed
            
        Returns:
            Vector embedding
        """
        try:
            # Generate embedding
            response = await self.client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            
            # Extract vector
            vector = response.data[0].embedding
            
            return vector
            
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None
        
    async def process_and_upload(self, contents: List[Dict]) -> bool:
        """
        Process content into vectors and save to parquet file.
        
        Args:
            contents: List of content dictionaries to process
            
        Returns:
            True if processing successful, False otherwise
        """
        try:
            logger.info(f"Processing {len(contents)} content items")
            
            # Generate a timestamp for the output file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self.vectors_dir / f"vectors_{timestamp}.parquet"
            
            # Process each content item
            vectors = []
            for content in contents:
                # Get content text and metadata
                text = content.get('content', '')
                metadata = content.get('metadata', {})
                
                # Skip if no text
                if not text:
                    continue
                    
                # Create chunks
                chunks = self.chunk_text(text, content.get('title', ''))
                
                # Process each chunk
                for i, chunk in enumerate(chunks):
                    # Generate embedding
                    vector = await self.generate_embedding(chunk)
                    if not vector:
                        continue
                        
                    # Update metadata with chunk info
                    chunk_metadata = metadata.copy()
                    chunk_metadata.update({
                        'chunk_index': i,
                        'total_chunks': len(chunks),
                        'chunk_text': chunk
                    })
                    
                    # Create vector record
                    vector_record = {
                        'id': f"vec_{timestamp}_{len(vectors)}",
                        'values': vector,
                        'metadata': json.dumps(chunk_metadata)
                    }
                    vectors.append(vector_record)
            
            if not vectors:
                logger.warning("No vectors generated")
                return False
                
            # Create DataFrame and save to parquet
            df = pd.DataFrame(vectors)
            df.to_parquet(output_file)
            
            logger.info(f"Saved {len(vectors)} vectors to {output_file}")
            return True
            
        except Exception as e:
            logger.error(f"Error processing content: {e}")
            return False 