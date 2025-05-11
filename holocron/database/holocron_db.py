"""
Database access layer for the Holocron Knowledge System.
Implements the Repository pattern for consistent data access with proper error handling,
transaction support, and integration with vector search capabilities.
"""

import logging
from typing import List, Dict, Any, Optional, Union, TypeVar, Generic, Tuple
from datetime import datetime
import json
from contextlib import contextmanager
import numpy as np

from supabase import Client
from postgrest import SyncRequestBuilder as RequestBuilder

from .client_factory import default_factory
from .vector_search import VectorSearchResult
from .vector_search_factory import VectorSearchFactory
from .base_adapter import BaseVectorSearch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Type variable for generic repository operations
T = TypeVar('T')

class RepositoryError(Exception):
    """Base exception for repository operations."""
    pass

class TransactionError(RepositoryError):
    """Exception for transaction-related errors."""
    pass

class ValidationError(RepositoryError):
    """Exception for data validation errors."""
    pass

class Repository(Generic[T]):
    """
    Generic repository interface defining standard CRUD operations.
    """
    def __init__(self, table_name: str, pool_key: str = "default"):
        self.table_name = table_name
        self.pool_key = pool_key
        self._client = None

    @property
    def client(self) -> Client:
        """Get Supabase client from connection pool."""
        if not self._client:
            self._client = default_factory.get_client(self.pool_key)
        return self._client

    def create(self, data: Dict[str, Any]) -> T:
        """Create a new record."""
        raise NotImplementedError

    def read(self, id: int) -> Optional[T]:
        """Read a record by ID."""
        raise NotImplementedError

    def update(self, id: int, data: Dict[str, Any]) -> T:
        """Update an existing record."""
        raise NotImplementedError

    def delete(self, id: int) -> bool:
        """Delete a record by ID."""
        raise NotImplementedError

    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[T]:
        """List records with optional filters."""
        raise NotImplementedError

    @contextmanager
    def transaction(self):
        """
        Context manager for transaction support.
        Note: This is a placeholder for future Supabase transaction support.
        Currently, we're simulating transactions at the application level.
        """
        try:
            yield self
        except Exception as e:
            logger.error(f"Transaction failed: {str(e)}")
            raise TransactionError(f"Transaction failed: {str(e)}")

class HolocronKnowledge:
    """Data class for Holocron knowledge entries."""
    def __init__(
        self,
        id: int,
        content: str,
        metadata: Dict[str, Any],
        embedding: Optional[List[float]] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None
    ):
        self.id = id
        self.content = content
        self.metadata = metadata
        self.embedding = embedding
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HolocronKnowledge':
        """Create instance from dictionary."""
        return cls(
            id=data['id'],
            content=data['content'],
            metadata=data.get('metadata', {}),
            embedding=data.get('embedding'),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at')
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'id': self.id,
            'content': self.content,
            'metadata': self.metadata,
            'embedding': self.embedding,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

class HolocronDB(Repository[HolocronKnowledge]):
    """
    Repository implementation for Holocron knowledge database.
    Provides CRUD operations, vector search, and transaction support.
    """
    def __init__(
        self,
        table_name: str = "holocron_knowledge",
        pool_key: str = "holocron_db",
        embedding_dimension: int = 1536
    ):
        super().__init__(table_name, pool_key)
        self.vector_search: BaseVectorSearch = VectorSearchFactory.create_vector_search(
            table_name=table_name,
            embedding_dimension=embedding_dimension,
            pool_key=pool_key
        )

    def _validate_knowledge(self, data: Dict[str, Any]) -> None:
        """Validate knowledge entry data."""
        required_fields = ['content', 'metadata']
        for field in required_fields:
            if field not in data:
                raise ValidationError(f"Missing required field: {field}")

        if 'embedding' in data:
            try:
                embedding = data['embedding']
                if len(embedding) != self.vector_search.embedding_dimension:
                    raise ValidationError(
                        f"Invalid embedding dimension. Expected "
                        f"{self.vector_search.embedding_dimension}, got {len(embedding)}"
                    )
            except (TypeError, ValueError) as e:
                raise ValidationError(f"Invalid embedding format: {str(e)}")

    def create(self, data: Dict[str, Any]) -> HolocronKnowledge:
        """
        Create a new knowledge entry.
        
        Args:
            data: Dictionary containing entry data
            
        Returns:
            Created HolocronKnowledge instance
            
        Raises:
            ValidationError: If data is invalid
            RepositoryError: If creation fails
        """
        try:
            self._validate_knowledge(data)
            response = self.client.table(self.table_name).insert(data).execute()
            if not response.data:
                raise RepositoryError("Failed to create knowledge entry")
            return HolocronKnowledge.from_dict(response.data[0])
        except Exception as e:
            logger.error(f"Error creating knowledge entry: {str(e)}")
            raise

    def read(self, id: int) -> Optional[HolocronKnowledge]:
        """
        Read a knowledge entry by ID.
        
        Args:
            id: Entry ID
            
        Returns:
            HolocronKnowledge instance if found, None otherwise
        """
        try:
            response = self.client.table(self.table_name).select("*").eq("id", id).execute()
            if not response.data:
                return None
            return HolocronKnowledge.from_dict(response.data[0])
        except Exception as e:
            logger.error(f"Error reading knowledge entry: {str(e)}")
            raise

    def update(self, id: int, data: Dict[str, Any]) -> HolocronKnowledge:
        """
        Update an existing knowledge entry.
        
        Args:
            id: Entry ID
            data: Dictionary containing updated data
            
        Returns:
            Updated HolocronKnowledge instance
            
        Raises:
            ValidationError: If data is invalid
            RepositoryError: If update fails
        """
        try:
            self._validate_knowledge(data)
            response = self.client.table(self.table_name).update(data).eq("id", id).execute()
            if not response.data:
                raise RepositoryError(f"Failed to update knowledge entry {id}")
            return HolocronKnowledge.from_dict(response.data[0])
        except Exception as e:
            logger.error(f"Error updating knowledge entry: {str(e)}")
            raise

    def delete(self, id: int) -> bool:
        """
        Delete a knowledge entry by ID.
        
        Args:
            id: Entry ID
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            response = self.client.table(self.table_name).delete().eq("id", id).execute()
            return bool(response.data)
        except Exception as e:
            logger.error(f"Error deleting knowledge entry: {str(e)}")
            raise

    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[HolocronKnowledge]:
        """
        List knowledge entries with optional filters.
        
        Args:
            filters: Dictionary of field-value pairs to filter by
            limit: Maximum number of entries to return
            offset: Number of entries to skip
            
        Returns:
            List of HolocronKnowledge instances
        """
        try:
            query = self.client.table(self.table_name).select("*")
            
            if filters:
                for key, value in filters.items():
                    if key in ['content', 'id']:
                        query = query.eq(key, value)
                    else:
                        query = query.eq(f"metadata->>'{key}'", value)
            
            response = query.range(offset, offset + limit - 1).execute()
            
            return [
                HolocronKnowledge.from_dict(entry)
                for entry in response.data
            ]
        except Exception as e:
            logger.error(f"Error listing knowledge entries: {str(e)}")
            raise

    def batch_create(self, items: List[Dict[str, Any]]) -> List[HolocronKnowledge]:
        """
        Create multiple knowledge entries in a batch.
        
        Args:
            items: List of dictionaries containing entry data
            
        Returns:
            List of created HolocronKnowledge instances
        """
        try:
            for item in items:
                self._validate_knowledge(item)
            
            response = self.client.table(self.table_name).insert(items).execute()
            
            if not response.data:
                raise RepositoryError("Failed to create knowledge entries")
                
            return [
                HolocronKnowledge.from_dict(entry)
                for entry in response.data
            ]
        except Exception as e:
            logger.error(f"Error batch creating knowledge entries: {str(e)}")
            raise

    def batch_update(
        self,
        items: List[Tuple[int, Dict[str, Any]]]
    ) -> List[HolocronKnowledge]:
        """
        Update multiple knowledge entries in a batch.
        
        Args:
            items: List of (id, data) tuples
            
        Returns:
            List of updated HolocronKnowledge instances
        """
        updated = []
        for id, data in items:
            try:
                updated.append(self.update(id, data))
            except Exception as e:
                logger.error(f"Error updating entry {id}: {str(e)}")
                continue
        return updated

    async def search_similar(
        self,
        embedding: Union[List[float], np.ndarray],
        limit: int = 10,
        threshold: float = 0.5,
        metadata_filters: Optional[Dict[str, Any]] = None
    ) -> List[VectorSearchResult]:
        """
        Search for similar knowledge entries using vector similarity.
        
        Args:
            embedding: Query vector
            limit: Maximum number of results
            threshold: Minimum similarity threshold
            metadata_filters: Optional metadata filters
            
        Returns:
            List of VectorSearchResult objects
        """
        try:
            results = await self.vector_search.search(
                embedding=embedding,
                limit=limit,
                threshold=threshold,
                metadata_filters=metadata_filters
            )
            return results
        except Exception as e:
            logger.error(f"Vector search failed: {str(e)}")
            return []

    def close(self):
        """Close all connections."""
        if hasattr(self.vector_search, 'close'):
            self.vector_search.close()
        super().close() 