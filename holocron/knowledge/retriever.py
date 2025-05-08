"""
Holocron Knowledge Retriever

This component handles retrieval of knowledge from the Holocron database using
semantic search with pgvector.
"""

import os
from typing import List, Dict, Any, Optional
import logging
from dotenv import load_dotenv
import asyncio

from ..database.holocron_db import HolocronDB, HolocronKnowledge
from .embeddings import OpenAIEmbeddings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class HolocronRetriever:
    """Retrieves knowledge from the Holocron database using semantic search."""
    
    def __init__(
        self,
        table_name: str = 'holocron_knowledge',
        embedding_dimension: int = 1536,
        pool_key: str = 'holocron_retriever'
    ):
        """
        Initialize the retriever with database access and embedding generation.
        
        Args:
            table_name: Name of the knowledge table
            embedding_dimension: Dimension of the embeddings
            pool_key: Key for the connection pool
        """
        self.db = HolocronDB(
            table_name=table_name,
            pool_key=pool_key,
            embedding_dimension=embedding_dimension
        )
        self.embeddings = OpenAIEmbeddings()
    
    async def search(
        self,
        query: str,
        limit: int = 5,
        min_relevance: float = 0.3,
        metadata_filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search the Holocron knowledge base using semantic similarity.
        
        Args:
            query: The search query
            limit: Maximum number of results to return
            min_relevance: Minimum similarity score (0-1) for results
            metadata_filters: Optional filters to apply to metadata fields
            
        Returns:
            List of matching knowledge entries with their content and metadata
        """
        try:
            # Get query embedding
            query_embedding = await self.embeddings.embed_query(query)
            
            # Perform vector search using HolocronDB
            results = await self.db.search_similar(
                embedding=query_embedding,
                limit=limit,
                threshold=min_relevance,
                metadata_filters=metadata_filters
            )
            
            # Convert results to dictionary format for backward compatibility
            return [result.to_dict() for result in results]
            
        except Exception as e:
            logger.error(f"Error in search method: {e}")
            return []
    
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
    
    async def close(self) -> None:
        """Close database connections."""
        await self.db.close() 