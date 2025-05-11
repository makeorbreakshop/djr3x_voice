"""
Data Processor for the Holocron Knowledge System

This module provides functionality to process scraped content into chunks,
generate embeddings, and upload to the Supabase vector database.
"""

import os
import re
import json
import time
import logging
import asyncio
import httpx
from typing import List, Dict, Any, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor

# Import patches for httpx
try:
    # Try to import from holocron package first
    from holocron import patches
    logging.info("Using global patches from holocron package")
except ImportError:
    # Fallback to direct patching if import fails
    logging.warning("Could not import holocron.patches - applying patch locally")
    original_httpx_init = httpx.Client.__init__
    def patched_httpx_init(self, *args, **kwargs):
        if 'proxy' in kwargs:
            del kwargs['proxy']
        return original_httpx_init(self, *args, **kwargs)
    httpx.Client.__init__ = patched_httpx_init

from openai import OpenAI, AsyncOpenAI
import tiktoken
from supabase import create_client, Client
from dotenv import load_dotenv

from config.app_settings import (
    HOLOCRON_CHUNK_SIZE,
    HOLOCRON_CHUNK_OVERLAP,
    EMBEDDING_MODEL,
    SUPABASE_URL,
    SUPABASE_KEY,
    HOLOCRON_TABLE_NAME
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
BATCH_TOKEN_LIMIT = 8000  # Increased from 1500 based on testing (OpenAI allows up to 8K)
MAX_PARALLEL_REQUESTS = 5  # Keep at 5 for stability
MAX_TOKENS_PER_CHUNK = 1000  # Target token count per chunk
TOKENIZER = tiktoken.get_encoding("cl100k_base")
UPLOAD_BATCH_SIZE = 25  # Increased from 5 based on testing

# Read OpenAI API key directly from .env file
def read_api_key_from_env_file():
    env_path = os.path.join(os.getcwd(), '.env')
    if not os.path.exists(env_path):
        logger.error(f".env file not found at {env_path}")
        return None
        
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('OPENAI_API_KEY='):
                key_value = line.split('=', 1)[1].strip()
                # Remove quotes if present
                if (key_value.startswith('"') and key_value.endswith('"')) or \
                   (key_value.startswith("'") and key_value.endswith("'")):
                    key_value = key_value[1:-1]
                return key_value
    logger.error("OPENAI_API_KEY not found in .env file")
    return None

class HolocronDataProcessor:
    """
    Process text data for the Holocron knowledge base.
    
    This class handles chunking text into appropriate segments,
    generating embeddings, and uploading to Supabase.
    """
    
    def __init__(self):
        """Initialize the data processor."""
        # Load environment variables
        load_dotenv()
        
        # Read OpenAI API key directly from .env file
        api_key = read_api_key_from_env_file()
        if not api_key:
            logger.error("Failed to load OpenAI API key from .env file")
            raise ValueError("OpenAI API key not found")
            
        # Create async OpenAI client
        self.openai_client = AsyncOpenAI(
            api_key=api_key,
            http_client=httpx.AsyncClient(
                base_url="https://api.openai.com/v1",
                follow_redirects=True,
                timeout=60.0
            )
        )
        
        # Initialize Supabase client
        self.supabase_url = SUPABASE_URL
        self.supabase_key = SUPABASE_KEY
        self.supabase = create_client(self.supabase_url, self.supabase_key)
        
        # Configure from app settings
        self.table_name = HOLOCRON_TABLE_NAME
        
    def count_tokens(self, text: str) -> int:
        """
        Count the number of tokens in a text string.
        
        Args:
            text: The text to count tokens for
            
        Returns:
            Number of tokens
        """
        return len(TOKENIZER.encode(text))
        
    def chunk_article(self, article: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Chunk an article into appropriate sized segments for embedding.
        
        Args:
            article: Dictionary containing article data
            
        Returns:
            List of chunk dictionaries with text and metadata
        """
        chunks = []
        
        # Extract basic article information
        title = article["title"]
        url = article["url"]
        categories = article.get("categories", [])
        
        # First create a chunk with the article title and introduction
        intro_section = next((s for s in article["sections"] if s["heading"] == "Introduction"), None)
        
        # FIXED: Always create an introduction chunk even if small
        if intro_section and intro_section['content']:
            intro_text = f"# {title}\n\n{intro_section['content']}"
            intro_tokens = self.count_tokens(intro_text)
            
            # Create intro chunk regardless of size
            chunks.append({
                "content": intro_text,
                "content_tokens": intro_tokens,
                "metadata": {
                    "title": title,
                    "source": url,
                    "section": "Introduction",
                    "categories": categories
                }
            })
        # FIXED: Even with no introduction section, create a title chunk
        elif not intro_section or not intro_section.get('content'):
            # Create a chunk with just the title
            title_text = f"# {title}\n\nStar Wars entity: {title}."
            title_tokens = self.count_tokens(title_text)
            chunks.append({
                "content": title_text,
                "content_tokens": title_tokens,
                "metadata": {
                    "title": title,
                    "source": url,
                    "section": "Title",
                    "categories": categories
                }
            })
        
        # Process each section into chunks
        for section in article["sections"]:
            section_heading = section["heading"]
            section_content = section["content"]
            
            # Skip empty sections or sections already handled
            if not section_content or section_heading == "Introduction":
                continue
                
            # Create section header text
            section_text = f"# {title} - {section_heading}\n\n{section_content}"
            section_tokens = self.count_tokens(section_text)
            
            # FIXED: Always include the section even if it's small
            chunks.append({
                "content": section_text,
                "content_tokens": section_tokens,
                "metadata": {
                    "title": title,
                    "source": url,
                    "section": section_heading,
                    "categories": categories
                }
            })
            
            # Only split larger sections into paragraphs if needed
            if section_tokens > MAX_TOKENS_PER_CHUNK:
                # Split into paragraphs
                paragraphs = re.split(r'\n\s*\n', section_content)
                current_chunk = f"# {title} - {section_heading}\n\n"
                current_chunk_tokens = self.count_tokens(current_chunk)
                
                for paragraph in paragraphs:
                    paragraph = paragraph.strip()
                    if not paragraph:
                        continue
                        
                    paragraph_text = paragraph + "\n\n"
                    paragraph_tokens = self.count_tokens(paragraph_text)
                    
                    # If adding this paragraph would exceed the limit, save the current chunk and start a new one
                    if current_chunk_tokens + paragraph_tokens > MAX_TOKENS_PER_CHUNK:
                        # Only add chunk if it contains actual content
                        if current_chunk_tokens > self.count_tokens(f"# {title} - {section_heading}\n\n"):
                            chunks.append({
                                "content": current_chunk.strip(),
                                "content_tokens": current_chunk_tokens,
                                "metadata": {
                                    "title": title,
                                    "source": url,
                                    "section": section_heading,
                                    "categories": categories
                                }
                            })
                        
                        # Start a new chunk with the section header
                        current_chunk = f"# {title} - {section_heading}\n\n{paragraph_text}"
                        current_chunk_tokens = self.count_tokens(current_chunk)
                    else:
                        # Add paragraph to the current chunk
                        current_chunk += paragraph_text
                        current_chunk_tokens += paragraph_tokens
                
                # Add the final chunk if it's not empty
                if current_chunk_tokens > self.count_tokens(f"# {title} - {section_heading}\n\n"):
                    chunks.append({
                        "content": current_chunk.strip(),
                        "content_tokens": current_chunk_tokens,
                        "metadata": {
                            "title": title,
                            "source": url,
                            "section": section_heading,
                            "categories": categories
                        }
                    })
        
        # FIXED: If no chunks were created at all, create a minimal chunk with title
        if not chunks:
            minimal_text = f"# {title}\n\nThis is a Star Wars entity or concept known as {title}."
            minimal_tokens = self.count_tokens(minimal_text)
            chunks.append({
                "content": minimal_text,
                "content_tokens": minimal_tokens,
                "metadata": {
                    "title": title,
                    "source": url,
                    "section": "Overview",
                    "categories": categories
                }
            })
        
        return chunks
        
    async def process_articles(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Process multiple articles into chunks.
        
        Args:
            articles: List of article dictionaries
            
        Returns:
            List of chunk dictionaries ready for embedding
        """
        all_chunks = []
        
        for article in articles:
            # Log the article being processed
            logger.info(f"Processing article: {article.get('title', 'Unknown Title')} ({len(article.get('sections', []))} sections)")
            
            chunks = self.chunk_article(article)
            if not chunks:
                logger.warning(f"Article '{article.get('title', 'Unknown')}' generated 0 chunks")
            else:
                logger.info(f"Article '{article.get('title', 'Unknown')}' generated {len(chunks)} chunks")
                
            all_chunks.extend(chunks)
            
        logger.info(f"Created {len(all_chunks)} chunks from {len(articles)} articles")
        return all_chunks
        
    async def generate_embeddings(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Generate embeddings for chunks in optimized batches.
        
        Args:
            chunks: List of chunk dictionaries with text and metadata
            
        Returns:
            List of chunks with embeddings added
        """
        if not chunks:
            return []

        # Group chunks into batches based on token count
        batches = []
        current_batch = []
        current_tokens = 0
        
        for chunk in chunks:
            chunk_tokens = chunk['content_tokens']
            if current_tokens + chunk_tokens > BATCH_TOKEN_LIMIT:
                batches.append(current_batch)
                current_batch = [chunk]
                current_tokens = chunk_tokens
            else:
                current_batch.append(chunk)
                current_tokens += chunk_tokens
        
        if current_batch:
            batches.append(current_batch)

        # Process batches in parallel with semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        start_time = time.time()
        
        async def process_batch(batch):
            async with semaphore:
                try:
                    texts = [chunk["content"] for chunk in batch]
                    response = await self.openai_client.embeddings.create(
                        input=texts,
                        model=EMBEDDING_MODEL
                    )
                    
                    # Map embeddings back to chunks
                    for i, embedding_data in enumerate(response.data):
                        batch[i]["embedding"] = embedding_data.embedding
                    
                    return batch
                except Exception as e:
                    logger.error(f"Error generating embeddings for batch: {e}")
                    # Return batch without embeddings on error
                    return batch

        # Process all batches concurrently
        tasks = [process_batch(batch) for batch in batches]
        processed_batches = await asyncio.gather(*tasks)
        
        # Flatten results and log performance
        processed_chunks = [chunk for batch in processed_batches for chunk in batch]
        end_time = time.time()
        
        total_chunks = len(processed_chunks)
        total_time = end_time - start_time
        chunks_per_second = total_chunks / total_time if total_time > 0 else 0
        
        logger.info(f"Generated {total_chunks} embeddings in {total_time:.2f}s ({chunks_per_second:.2f} chunks/s)")
        
        return processed_chunks
        
    async def upload_to_supabase(self, chunks: List[Dict[str, Any]]) -> bool:
        """
        Upload chunks with embeddings to Supabase.
        
        Args:
            chunks: List of chunk dictionaries with embeddings
            
        Returns:
            Success status
        """
        logger.info(f"Uploading {len(chunks)} chunks to Supabase table '{self.table_name}'")
        
        # Process in larger batches based on testing
        upload_batch_size = UPLOAD_BATCH_SIZE
        success_count = 0
        
        for i in range(0, len(chunks), upload_batch_size):
            batch = chunks[i:i+upload_batch_size]
            logger.info(f"Uploading batch {i//upload_batch_size + 1}/{(len(chunks)-1)//upload_batch_size + 1}")
            
            try:
                # Prepare all data for batch insertion
                data = [{
                    "content": chunk["content"],
                    "content_tokens": chunk["content_tokens"],
                    "metadata": chunk["metadata"],
                    "embedding": chunk["embedding"]
                } for chunk in batch]
                
                # Use batch insert for better performance
                result = await asyncio.to_thread(
                    lambda: self.supabase.table(self.table_name).insert(data).execute()
                )
                
                # Count successful batch insert
                success_count += len(batch)
                logger.info(f"Successfully inserted batch of {len(batch)} chunks")
                
                # Rate limiting - reduced sleep time since we're doing batch inserts
                if i + upload_batch_size < len(chunks):
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Error uploading batch to Supabase: {e}")
                
        logger.info(f"Successfully uploaded {success_count}/{len(chunks)} chunks to Supabase")
        return success_count > 0

    # Add these new methods for the test script
    def process_article(self, article: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Process a single article into chunks.
        
        Args:
            article: Dictionary containing article data
            
        Returns:
            List of chunk dictionaries ready for embedding
        """
        return self.chunk_article(article)
        
    async def upload_chunks(self, chunks: List[Dict[str, Any]]) -> bool:
        """
        Generate embeddings for chunks and upload to Supabase.
        
        Args:
            chunks: List of chunk dictionaries
            
        Returns:
            Success status
        """
        # Generate embeddings
        chunks_with_embeddings = await self.generate_embeddings(chunks)
        
        # Upload to Supabase
        return await self.upload_to_supabase(chunks_with_embeddings)
        
    async def process_and_upload(self, articles: List[Dict[str, Any]]) -> bool:
        """
        Process articles, generate embeddings, and upload to Supabase.
        
        Args:
            articles: List of article dictionaries
            
        Returns:
            Success status
        """
        try:
            all_chunks = []
            
            # First pass: chunk all articles
            for article in articles:
                chunks = self.chunk_article(article)
                all_chunks.extend(chunks)
            
            # Generate embeddings in optimized batches
            chunks_with_embeddings = await self.generate_embeddings(all_chunks)
            
            # Upload chunks
            success = await self.upload_chunks(chunks_with_embeddings)
            
            return success
        except Exception as e:
            logger.error(f"Error in process_and_upload: {e}")
            return False

async def main():
    """Run the data processor as a standalone script."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Process and upload Star Wars knowledge to the Holocron database")
    parser.add_argument('--input', type=str, default="data/wookieepedia_articles.json",
                      help="Path to the JSON file containing the articles")
    args = parser.parse_args()
    
    # Check if input file exists
    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        return
        
    # Load articles from file
    with open(args.input, 'r', encoding='utf-8') as f:
        articles = json.load(f)
        
    logger.info(f"Loaded {len(articles)} articles from {args.input}")
    
    # Process and upload
    processor = HolocronDataProcessor()
    success = await processor.process_and_upload(articles)
    
    if success:
        logger.info("Successfully processed and uploaded articles to the Holocron database")
    else:
        logger.error("Failed to process and upload articles")
        
if __name__ == "__main__":
    asyncio.run(main()) 