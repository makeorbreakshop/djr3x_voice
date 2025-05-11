"""
Base adapter interface for vector search implementations.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union
import numpy as np

class BaseVectorSearch(ABC):
    """Abstract base class for vector search implementations."""
    
    @property
    @abstractmethod
    def embedding_dimension(self) -> int:
        """Get the dimension of embedding vectors."""
        pass
    
    @abstractmethod
    async def search(
        self,
        embedding: Union[List[float], np.ndarray],
        limit: int = 10,
        threshold: float = 0.5,
        metadata_filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors.
        
        Args:
            embedding: Query vector
            limit: Maximum number of results
            threshold: Minimum similarity threshold
            metadata_filters: Optional metadata filters
            
        Returns:
            List of dictionaries containing matches and their metadata
        """
        pass
    
    @abstractmethod
    async def add_vectors(
        self,
        vectors: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> None:
        """
        Add vectors to the index.
        
        Args:
            vectors: List of dictionaries containing vectors and metadata
            batch_size: Size of batches for upload
        """
        pass
    
    @abstractmethod
    async def delete_vectors(self, vector_ids: List[str]) -> None:
        """
        Delete vectors from the index.
        
        Args:
            vector_ids: List of vector IDs to delete
        """
        pass
    
    def close(self) -> None:
        """Close any open connections. Optional implementation."""
        pass 