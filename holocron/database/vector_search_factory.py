"""
Factory for creating vector search implementations based on configuration.
"""

import os
from typing import Optional
from dotenv import load_dotenv

from .vector_search import VectorSearch
from ..knowledge.pinecone_adapter import PineconeVectorSearch
from .base_adapter import BaseVectorSearch

class VectorSearchFactory:
    """Factory for creating vector search implementations."""
    
    @staticmethod
    def create_vector_search(
        table_name: str = "holocron_knowledge",
        embedding_dimension: int = 1536,
        pool_key: str = "vector_search"
    ) -> BaseVectorSearch:
        """
        Create appropriate vector search implementation based on configuration.
        
        Args:
            table_name: Name of the table/collection
            embedding_dimension: Dimension of embedding vectors
            pool_key: Connection pool key for Supabase
            
        Returns:
            Vector search implementation (either Supabase or Pinecone)
        """
        load_dotenv()
        use_pinecone = os.getenv("USE_PINECONE", "false").lower() == "true"
        
        if use_pinecone:
            return PineconeVectorSearch()
        else:
            return VectorSearch(
                table_name=table_name,
                embedding_dimension=embedding_dimension,
                pool_key=pool_key
            ) 