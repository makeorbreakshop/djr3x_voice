"""
Standardized vector search implementation for the Holocron Knowledge System.
This module provides a consistent interface for vector similarity search operations
using Supabase's pgvector functionality.
"""

import logging
import json
from typing import List, Dict, Any, Optional, Union
import numpy as np
from supabase import Client
from supabase.lib.client_options import ClientOptions
from postgrest import SyncRequestBuilder as RequestBuilder
import time

from ..database.client_factory import default_factory
from .base_adapter import BaseVectorSearch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VectorSearchResult:
    """
    Structured container for vector search results.
    Provides consistent access to result data regardless of search method used.
    """
    def __init__(
        self,
        id: int,
        content: str,
        metadata: Dict[str, Any],
        similarity: float
    ):
        self.id = id
        self.content = content
        self.metadata = metadata
        self.similarity = similarity

    def __repr__(self) -> str:
        return f"VectorSearchResult(id={self.id}, similarity={self.similarity:.4f}, metadata={self.metadata}, content={self.content[:30]}...)"

    @classmethod
    def from_rpc_result(cls, result: Dict[str, Any]) -> 'VectorSearchResult':
        """Create from RPC match_documents result."""
        return cls(
            id=result['id'],
            content=result['content'],
            metadata=result.get('metadata', {}),
            similarity=result.get('similarity', 0.0)
        )

    @classmethod
    def from_sql_result(cls, result: Dict[str, Any]) -> 'VectorSearchResult':
        """Create from direct SQL query result."""
        return cls(
            id=result['id'],
            content=result['content'],
            metadata=result.get('metadata', {}),
            similarity=result.get('similarity', 0.0)
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'id': self.id,
            'content': self.content,
            'metadata': self.metadata,
            'similarity': self.similarity
        }

class VectorSearch(BaseVectorSearch):
    """
    Provides standardized vector similarity search functionality.
    Implements multiple search strategies with automatic fallback.
    """
    def __init__(
        self,
        table_name: str = "holocron_knowledge",
        embedding_dimension: int = 1536,
        pool_key: str = "vector_search"
    ):
        self._table_name = table_name
        self._embedding_dimension = embedding_dimension
        self.pool_key = pool_key
        self._client = None

    @property
    def client(self) -> Client:
        """Get Supabase client from connection pool."""
        if not self._client:
            self._client = default_factory.get_client(self.pool_key)
        return self._client

    @property
    def embedding_dimension(self) -> int:
        """Get the dimension of embedding vectors."""
        return self._embedding_dimension

    def _validate_embedding(self, embedding: Union[List[float], np.ndarray]) -> np.ndarray:
        """
        Validate and normalize embedding vector.
        
        Args:
            embedding: Input vector as list or numpy array
            
        Returns:
            Normalized numpy array of correct dimension
            
        Raises:
            ValueError: If embedding has incorrect dimension
        """
        if isinstance(embedding, list):
            embedding = np.array(embedding)
        
        if embedding.shape != (self.embedding_dimension,):
            raise ValueError(
                f"Embedding must have dimension {self.embedding_dimension}, "
                f"got {embedding.shape[0]}"
            )
        
        # Normalize to unit vector for cosine similarity
        return embedding / np.linalg.norm(embedding)

    def _format_embedding(self, embedding: np.ndarray) -> str:
        """Format embedding as string for SQL queries."""
        # Format as a simple comma-separated list without brackets for pgvector
        return ','.join([f"{x}" for x in embedding])

    async def search(
        self,
        embedding: Union[List[float], np.ndarray],
        limit: int = 10,
        threshold: float = 0.3,
        metadata_filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors using pgvector RPC function.
        
        Args:
            embedding: Query vector
            limit: Maximum number of results to return
            threshold: Minimum similarity threshold (0-1)
            metadata_filters: Optional filters to apply to metadata fields
            
        Returns:
            List of VectorSearchResult objects
        """
        embedding = self._validate_embedding(embedding)
        
        logger.info(f"Starting vector search with threshold {threshold}")
        
        try:
            # Use RPC-based vector search with retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    results = await self._search_rpc(
                        embedding=embedding,
                        limit=limit,
                        threshold=threshold,
                        metadata_filters=metadata_filters
                    )
                    return [r.to_dict() for r in results]
                except Exception as e:
                    if "timeout" in str(e).lower() and attempt < max_retries - 1:
                        # Only retry on timeout errors
                        retry_delay = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
                        logger.warning(f"Vector search timeout (attempt {attempt+1}/{max_retries}). Retrying in {retry_delay}s: {str(e)}")
                        time.sleep(retry_delay)
                    else:
                        # Re-raise on non-timeout errors or final attempt
                        raise
        except Exception as e:
            logger.warning(f"Vector search failed after retries: {str(e)}")
            # Return empty list - application will fall back to LLM's training knowledge
            return []

    async def _search_rpc(
        self,
        embedding: np.ndarray,
        limit: int,
        threshold: float,
        metadata_filters: Optional[Dict[str, Any]] = None
    ) -> List[VectorSearchResult]:
        """Search using RPC match_documents function."""
        try:
            params = {
                'query_embedding': self._format_embedding(embedding),
                'match_threshold': threshold,
                'match_count': limit
            }
            
            # Add metadata filters if provided
            if metadata_filters:
                params['metadata_filters'] = json.dumps(metadata_filters)
                
            # Execute RPC with properly formatted parameters
            response = self.client.rpc('match_documents', params).execute()
            
            logger.info(f"RPC search returned {len(response.data) if response.data else 0} results")
            if response.data and len(response.data) > 0:
                logger.info(f"First result similarity: {response.data[0].get('similarity', 'N/A')}")
            
            if not response.data:
                return []
                
            return [VectorSearchResult.from_rpc_result(r) for r in response.data]
            
        except Exception as e:
            logger.error(f"RPC search failed: {str(e)}")
            raise

    async def add_vectors(
        self,
        vectors: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> None:
        """Add vectors to the database."""
        try:
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                response = self.client.table(self._table_name).insert(batch).execute()
                if not response.data:
                    raise Exception("Failed to insert vectors")
                logger.info(f"Added batch {i//batch_size + 1}")
        except Exception as e:
            logger.error(f"Error adding vectors: {str(e)}")
            raise

    async def delete_vectors(self, vector_ids: List[str]) -> None:
        """Delete vectors from the database."""
        try:
            response = self.client.table(self._table_name).delete().in_('id', vector_ids).execute()
            if not response.data:
                raise Exception("Failed to delete vectors")
            logger.info(f"Deleted {len(vector_ids)} vectors")
        except Exception as e:
            logger.error(f"Error deleting vectors: {str(e)}")
            raise

    def close(self) -> None:
        """Close database connections."""
        if self._client:
            default_factory.close_client(self.pool_key)
            self._client = None
            logger.info("Closed vector search connections")