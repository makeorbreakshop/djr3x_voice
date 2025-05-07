"""
RAG Provider for the Holocron Knowledge System

This module handles the retrieval of Star Wars knowledge from the Supabase 
vector database using semantic similarity search.
"""

import os
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple
import psycopg2
from psycopg2.extras import Json, DictCursor
from psycopg2.pool import SimpleConnectionPool

import openai
from dotenv import load_dotenv

# Import from data_processor to reuse the function to read the API key
from src.holocron.data_processor import read_api_key_from_env_file

from config.app_settings import (
    SUPABASE_URL,
    HOLOCRON_TABLE_NAME,
    HOLOCRON_MAX_RESULTS,
    HOLOCRON_SIMILARITY_THRESHOLD,
    HOLOCRON_ENABLED
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RAGProvider:
    """
    Retrieval-Augmented Generation provider for the Star Wars Holocron knowledge base.
    
    This class handles the retrieval of relevant Star Wars canonical knowledge from
    a vector database stored in Supabase, using semantic similarity search with
    OpenAI's text-embedding-3-small model.
    """
    
    def __init__(self, max_results: int = None, similarity_threshold: float = None):
        """Initialize the RAG Provider with PostgreSQL connection pool."""
        # Load environment variables
        load_dotenv()
        
        # Read OpenAI API key directly from .env file
        api_key = read_api_key_from_env_file()
        if not api_key:
            logger.error("Failed to load OpenAI API key from .env file")
            raise ValueError("OpenAI API key not found")
            
        # Initialize OpenAI with the key from .env file
        openai.api_key = api_key
        
        # Initialize PostgreSQL connection pool
        db_password = os.getenv("SUPABASE_DB_PASSWORD")
        if not db_password:
            raise ValueError("Database password not found in environment variables")
            
        # Parse Supabase URL components for PostgreSQL connection
        project_id = SUPABASE_URL.split('//')[1].split('.')[0]
        dsn = f"postgresql://postgres:{db_password}@db.{project_id}.supabase.co:5432/postgres"
        
        # Create connection pool
        self.pool = SimpleConnectionPool(1, 20, dsn)
        
        # Set up configuration
        self.table_name = HOLOCRON_TABLE_NAME
        self.max_results = max_results or HOLOCRON_MAX_RESULTS
        self.similarity_threshold = similarity_threshold or HOLOCRON_SIMILARITY_THRESHOLD
        
        logger.info("RAG Provider initialized with PostgreSQL connection pool")
    
    def __del__(self):
        """Clean up database connections when the object is destroyed."""
        if hasattr(self, 'pool'):
            self.pool.closeall()
    
    async def _generate_embedding(self, text: str) -> List[float]:
        """
        Generate an embedding vector for the given text using OpenAI's API.
        
        Args:
            text: The text to generate an embedding for
            
        Returns:
            A list of floats representing the embedding vector
        """
        try:
            response = openai.embeddings.create(
                model="text-embedding-3-small",
                input=text,
                encoding_format="float"
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise
    
    async def _search_vector_db(self, 
                               embedding: List[float], 
                               top_k: int = None) -> List[Dict[str, Any]]:
        """
        Search the vector database for similar content using direct PostgreSQL connection.
        
        Args:
            embedding: The query embedding vector
            top_k: Maximum number of results to return
            
        Returns:
            A list of dictionaries containing the matching documents and their similarity scores
        """
        if top_k is None:
            top_k = self.max_results
            
        try:
            # Get connection from pool
            conn = self.pool.getconn()
            
            # Create cursor that returns results as dictionaries
            with conn.cursor(cursor_factory=DictCursor) as cur:
                # Format vector string properly for PostgreSQL array
                vector_str = f"[{','.join(str(x) for x in embedding)}]"
                
                # Execute vector similarity search
                query = """
                    SELECT 
                        content,
                        metadata,
                        1 - (embedding <=> %s::vector) as similarity
                    FROM holocron_knowledge
                    WHERE 1 - (embedding <=> %s::vector) > %s
                    ORDER BY similarity DESC
                    LIMIT %s
                """
                
                cur.execute(query, (vector_str, vector_str, self.similarity_threshold, top_k))
                results = cur.fetchall()
                
                # Convert results to list of dictionaries
                return [dict(row) for row in results]
                
        except Exception as e:
            logger.error(f"Error searching vector database: {e}")
            return []
            
        finally:
            if 'conn' in locals():
                self.pool.putconn(conn)
    
    def _format_context(self, results: List[Dict[str, Any]]) -> str:
        """
        Format the retrieved documents for inclusion in the prompt.
        
        Args:
            results: List of retrieved documents with similarity scores
            
        Returns:
            A formatted string containing the relevant context
        """
        if not results:
            return ""
        
        context_parts = []
        
        for item in results:
            # Extract metadata information if available
            metadata = {}
            if 'metadata' in item and item['metadata']:
                if isinstance(item['metadata'], str):
                    try:
                        metadata = json.loads(item['metadata'])
                    except json.JSONDecodeError:
                        metadata = {"source": item['metadata']}
                else:
                    metadata = item['metadata']
            
            # Format the context with source information if available
            source_info = ""
            if metadata and 'source' in metadata:
                source_info = f" [Source: {metadata['source']}]"
            
            # Add the content with optional source information
            context_parts.append(f"{item['content']}{source_info}")
        
        # Join all context parts with separators
        return "\n---\n".join(context_parts)
    
    async def get_relevant_context(self, 
                                  query: str, 
                                  top_k: int = None, 
                                  threshold: float = None) -> Tuple[str, bool]:
        """
        Retrieve relevant Star Wars knowledge context based on the query.
        
        Args:
            query: The user's question or query
            top_k: Maximum number of results to return (defaults to app_settings.HOLOCRON_MAX_RESULTS)
            threshold: Similarity threshold (defaults to app_settings.HOLOCRON_SIMILARITY_THRESHOLD)
            
        Returns:
            A tuple containing:
            - formatted context string for prompt injection
            - boolean indicating if relevant information was found
        """
        if not HOLOCRON_ENABLED:
            return "", False
            
        try:
            # Use provided parameters or defaults
            if top_k is None:
                top_k = self.max_results
            if threshold is None:
                threshold = self.similarity_threshold
            
            # Generate embedding for the query
            embedding = await self._generate_embedding(query)
            
            # Search for relevant documents using direct PostgreSQL connection
            results = await self._search_vector_db(embedding, top_k)
            
            # Format context for prompt inclusion
            context = self._format_context(results)
            
            # Return context and a flag indicating if relevant information was found
            return context, bool(results)
            
        except Exception as e:
            logger.error(f"Error getting relevant context: {e}")
            return "", False 