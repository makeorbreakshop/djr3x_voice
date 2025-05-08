"""
OpenAI Embeddings Generator

This component handles the generation of embeddings for semantic search using
OpenAI's text-embedding models.
"""

import os
from openai import OpenAI
from typing import List
import numpy as np
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class OpenAIEmbeddings:
    """Generates embeddings using OpenAI's embedding models."""
    
    def __init__(self):
        """Initialize the embeddings generator."""
        # Get API key from environment
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Missing OpenAI API key")
        
        # Initialize client with just the API key
        # No additional parameters that might cause conflicts
        self.client = OpenAI(api_key=api_key)
        self.model = "text-embedding-ada-002"
        self.embedding_dim = 1536  # Dimensions for ada-002
    
    def embed_query(self, text: str) -> List[float]:
        """
        Generate embeddings for a single query text.
        
        Args:
            text: The text to embed
            
        Returns:
            List of embedding values
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating query embedding: {e}")
            # Return zero vector as fallback
            return [0.0] * self.embedding_dim
    
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts
            )
            return [data.embedding for data in response.data]
        except Exception as e:
            logger.error(f"Error generating batch embeddings: {e}")
            # Return zero vectors as fallback
            return [[0.0] * self.embedding_dim for _ in texts]
    
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