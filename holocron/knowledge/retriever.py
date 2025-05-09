"""
Holocron Knowledge Retriever

This component handles retrieval of knowledge from the Holocron database using
semantic search with pgvector.
"""

import os
from typing import List, Dict, Any, Optional, Union
import logging
from dotenv import load_dotenv
import asyncio
import numpy as np
from openai import OpenAI

from ..database.vector_search import VectorSearchResult

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class HolocronRetriever:
    """Retrieves relevant knowledge from the Holocron database."""
    
    def __init__(self, embedding_dimension: int = 1536):
        self.embedding_dimension = embedding_dimension
        self._db = None
        self._client = OpenAI()
    
    @property
    def db(self):
        """Lazy load the database connection."""
        if self._db is None:
            # Import here to avoid circular dependency
            from ..database.holocron_db import HolocronDB
            self._db = HolocronDB(embedding_dimension=self.embedding_dimension)
        return self._db
    
    async def search(
        self,
        query: str,
        limit: int = 5,
        threshold: float = 0.5
    ) -> List[VectorSearchResult]:
        """
        Search for relevant knowledge using semantic similarity.
        
        Args:
            query: The search query
            limit: Maximum number of results
            threshold: Minimum similarity threshold
            
        Returns:
            List of relevant knowledge entries
        """
        try:
            # Generate embedding for query
            response = self._client.embeddings.create(
                model="text-embedding-ada-002",
                input=query
            )
            query_embedding = response.data[0].embedding
            
            # Search database
            results = await self.db.search_similar(
                embedding=query_embedding,
                limit=limit,
                threshold=threshold
            )
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching knowledge: {str(e)}")
            return []
    
    def close(self):
        """Close database connection."""
        if self._db:
            self._db.close()
            self._db = None
    
    async def get_entry_by_id(self, entry_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific knowledge entry by ID.
        
        Args:
            entry_id: The ID of the entry to retrieve
            
        Returns:
            The knowledge entry or None if not found
        """
        try:
            entry = await self.db.read(entry_id)
            return entry.to_dict() if entry else None
        except Exception as e:
            logger.error(f"Error retrieving entry {entry_id}: {e}")
            return None 