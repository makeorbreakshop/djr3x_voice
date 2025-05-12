"""
OpenAI Embeddings Generator

This component handles the generation of embeddings for semantic search using
OpenAI's text-embedding models.
"""

import os
import time
import asyncio
import random
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from openai import AsyncOpenAI, RateLimitError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
EMBEDDING_MODEL = "text-embedding-ada-002"
EMBEDDING_DIMENSIONS = 1536
MAX_RETRIES = 5
MAX_BATCH_SIZE = 100
TOKEN_RATE_LIMIT = 150000  # Per minute (OpenAI's limit)
MIN_RETRY_DELAY = 1  # seconds

class OpenAIEmbeddings:
    """Generates embeddings using OpenAI's embedding models."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the embeddings generator."""
        # Get API key from environment if not provided
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Missing OpenAI API key")
        
        # Initialize client
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.model = EMBEDDING_MODEL
        self.embedding_dim = EMBEDDING_DIMENSIONS
        
        # Rate limiting state
        self.token_usage_window = []  # Track token usage for rate limiting
        self.last_request_time = 0
        self.semaphore = asyncio.Semaphore(5)  # Limit concurrent requests
    
    async def embed_query(self, text: str) -> List[float]:
        """
        Generate embeddings for a single query text.
        
        Args:
            text: The text to embed
            
        Returns:
            List of embedding values
        """
        try:
            # Estimate tokens (4 chars ≈ 1 token)
            estimated_tokens = len(text) // 4
            
            # Apply rate limiting
            await self._adaptive_rate_limit(estimated_tokens)
            
            response = await self.client.embeddings.create(
                model=self.model,
                input=text
            )
            
            # Record usage
            actual_tokens = response.usage.total_tokens
            self._record_token_usage(actual_tokens)
            
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating query embedding: {e}")
            # Return zero vector as fallback
            return [0.0] * self.embedding_dim
    
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        # Split into batches to avoid hitting rate limits and context limits
        results = []
        for i in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[i:i+MAX_BATCH_SIZE]
            batch_embeddings = await self._embed_batch(batch)
            results.extend(batch_embeddings)
        return results
    
    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts with retry logic."""
        # Estimate tokens (4 chars ≈ 1 token)
        estimated_tokens = sum(len(text) // 4 for text in texts)
        
        # Apply rate limiting
        await self._adaptive_rate_limit(estimated_tokens)
        
        for retry in range(MAX_RETRIES):
            try:
                async with self.semaphore:
                    # Record request time
                    self.last_request_time = time.time()
                    
                    # Make API request
                    response = await self.client.embeddings.create(
                        model=self.model,
                        input=texts
                    )
                    
                    # Record usage
                    actual_tokens = response.usage.total_tokens
                    self._record_token_usage(actual_tokens)
                    
                    return [data.embedding for data in response.data]
                    
            except Exception as e:
                if isinstance(e, RateLimitError) or "rate limit" in str(e).lower():
                    # Exponential backoff with jitter
                    wait_time = min(30, (2 ** retry) * MIN_RETRY_DELAY * (0.8 + 0.4 * random.random()))
                    logger.warning(f"Rate limit hit, retrying in {wait_time:.1f}s (attempt {retry+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Error generating batch embeddings: {e}")
                    if retry < MAX_RETRIES - 1:
                        # Brief delay before retry for non-rate-limit errors
                        await asyncio.sleep(1)
                    else:
                        # Return zero vectors as fallback after all retries
                        return [[0.0] * self.embedding_dim for _ in texts]
        
        # Return zero vectors if all retries failed
        logger.error(f"Failed to create embeddings after {MAX_RETRIES} retries")
        return [[0.0] * self.embedding_dim for _ in texts]
    
    def _record_token_usage(self, tokens: int) -> None:
        """Record token usage for rate limiting."""
        current_time = time.time()
        self.token_usage_window.append((current_time, tokens))
        
        # Clean up old records (older than 1 minute)
        one_minute_ago = current_time - 60
        self.token_usage_window = [
            (t, tok) for t, tok in self.token_usage_window 
            if t >= one_minute_ago
        ]
    
    async def _adaptive_rate_limit(self, estimated_tokens: int) -> None:
        """
        Adaptively limit rate based on recent token usage.
        
        Args:
            estimated_tokens: Estimated tokens for upcoming request
        """
        current_time = time.time()
        
        # Calculate tokens used in the last minute
        one_minute_ago = current_time - 60
        recent_usage = sum(
            tokens for timestamp, tokens in self.token_usage_window 
            if timestamp >= one_minute_ago
        )
        
        # Check if we'd exceed the rate limit
        if recent_usage + estimated_tokens > TOKEN_RATE_LIMIT:
            # Calculate how long to wait
            usage_ratio = (recent_usage + estimated_tokens) / TOKEN_RATE_LIMIT
            wait_time = min(10, usage_ratio * 2)  # Cap at 10 seconds
            
            logger.info(f"Rate limit approaching, waiting {wait_time:.2f}s before next request")
            await asyncio.sleep(wait_time)
            
        # Always ensure some time between requests to avoid bursts
        time_since_last = current_time - self.last_request_time
        if time_since_last < 0.1:
            await asyncio.sleep(0.1 - time_since_last)
    
    def normalize_embedding(self, embedding: List[float]) -> List[float]:
        """
        Normalize an embedding vector to unit length.
        
        Args:
            embedding: The embedding vector to normalize
            
        Returns:
            Normalized embedding vector
        """
        norm = np.linalg.norm(embedding)
        if norm > 0:
            return (np.array(embedding) / norm).tolist()
        return embedding

async def create_embeddings_batch(articles: List[Dict[str, Any]], config: Optional[Dict] = None) -> List[List[float]]:
    """
    Create embeddings for a batch of articles.
    
    This function extracts relevant content from articles and generates embeddings.
    It includes adaptive rate limiting and efficient batching for optimal performance.
    
    Args:
        articles: List of article dictionaries
        config: Optional configuration parameters
        
    Returns:
        List of embedding vectors for each article
    """
    # Initialize embeddings client
    embeddings_client = OpenAIEmbeddings()
    
    # Extract text to embed from each article
    texts_to_embed = []
    for article in articles:
        title = article.get('title', '')
        content = article.get('content', '')
        
        # Skip empty content
        if not content:
            texts_to_embed.append('')
            continue
            
        # Combine title and content
        text = f"{title}\n\n{content}" if title else content
        texts_to_embed.append(text)
    
    # Generate embeddings with optimized batching
    try:
        embeddings = await embeddings_client.embed_texts(texts_to_embed)
        return embeddings
    except Exception as e:
        logger.error(f"Error generating embeddings batch: {e}")
        # Return None for failed embeddings
        return [None] * len(articles) 