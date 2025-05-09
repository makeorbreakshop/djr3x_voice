"""
Pinecone implementation of vector search for the Holocron Knowledge System.
"""

import os
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv
import logging
from typing import List, Dict, Any, Optional, Union
import numpy as np

from ..database.base_adapter import BaseVectorSearch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PineconeVectorSearch(BaseVectorSearch):
    """Pinecone implementation of vector search."""
    
    def __init__(self):
        """Initialize Pinecone connection."""
        load_dotenv()
        
        # Get environment variables
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")
        
        if not self.api_key:
            raise ValueError("PINECONE_API_KEY not found in environment variables")
            
        # Initialize Pinecone
        self.pc = Pinecone(api_key=self.api_key)
        
        # Create index if it doesn't exist
        existing_indexes = [index.name for index in self.pc.list_indexes()]
        if self.index_name not in existing_indexes:
            self.pc.create_index(
                name=self.index_name,
                dimension=1536,  # OpenAI embedding dimension
                metric="cosine",
                spec=ServerlessSpec(
                    cloud="aws",
                    region="us-east-1"
                )
            )
        
        # Connect to index
        self._index = self.pc.Index(name=self.index_name)
    
    @property
    def embedding_dimension(self) -> int:
        """Get the dimension of embedding vectors."""
        return 1536  # OpenAI embedding dimension
    
    async def search(
        self,
        embedding: Union[List[float], np.ndarray],
        limit: int = 10,
        threshold: float = 0.01,
        metadata_filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors in Pinecone.
        
        Args:
            embedding: Query vector
            limit: Maximum number of results
            threshold: Minimum similarity threshold (default: 0.01)
                       Lower threshold improves recall, especially for specific entity queries
            metadata_filters: Optional metadata filters
            
        Returns:
            List of dictionaries containing matches and their metadata
        """
        try:
            # Convert numpy array to list if needed
            if isinstance(embedding, np.ndarray):
                embedding = embedding.tolist()
            
            # Prepare filter if metadata_filters provided
            filter_dict = {}
            if metadata_filters:
                filter_dict = metadata_filters
            
            # Query Pinecone
            results = self._index.query(
                vector=embedding,
                top_k=limit,
                include_metadata=True,
                filter=filter_dict
            )
            
            # Format results to match BaseVectorSearch interface
            formatted_results = []
            for match in results.matches:
                if match.score >= threshold:
                    formatted_results.append({
                        'id': match.id,
                        'content': match.metadata.get('content', ''),
                        'metadata': match.metadata,
                        'similarity': match.score
                    })
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error searching vectors: {str(e)}")
            return []
    
    async def add_vectors(
        self,
        vectors: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> None:
        """
        Add vectors to Pinecone index.
        
        Args:
            vectors: List of dictionaries containing vectors and metadata
            batch_size: Size of batches for upload
        """
        try:
            # Process vectors in batches
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i + batch_size]
                
                # Format vectors for Pinecone
                pinecone_vectors = []
                for vector in batch:
                    pinecone_vectors.append({
                        'id': str(vector['id']),
                        'values': vector['values'],
                        'metadata': vector['metadata']
                    })
                
                # Upsert batch
                self._index.upsert(vectors=pinecone_vectors)
                
                logger.info(f"Added batch {i//batch_size + 1}")
                
        except Exception as e:
            logger.error(f"Error adding vectors: {str(e)}")
            raise
    
    async def delete_vectors(self, vector_ids: List[str]) -> None:
        """
        Delete vectors from Pinecone index.
        
        Args:
            vector_ids: List of vector IDs to delete
        """
        try:
            self._index.delete(ids=vector_ids)
            logger.info(f"Deleted {len(vector_ids)} vectors")
        except Exception as e:
            logger.error(f"Error deleting vectors: {str(e)}")
            raise
    
    def close(self) -> None:
        """Close Pinecone connection."""
        # No explicit cleanup needed for Pinecone
        pass 