#!/usr/bin/env python3
"""
Setup script for the Holocron Knowledge Database in Supabase

This script creates:
1. The holocron_knowledge table with pgvector support
2. A match_holocron_documents function for similarity search
"""

import os
import sys
import asyncio
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

# Import app settings
from config.app_settings import (
    SUPABASE_URL,
    SUPABASE_KEY,
    HOLOCRON_TABLE_NAME
)

# SQL to create vector extension (if not exists)
CREATE_VECTOR_EXTENSION = """
-- Enable the pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
"""

# SQL to create the holocron_knowledge table
CREATE_HOLOCRON_TABLE = f"""
-- Create the holocron_knowledge table with vector support
CREATE TABLE IF NOT EXISTS {HOLOCRON_TABLE_NAME} (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    content_tokens INTEGER NOT NULL,
    metadata JSONB,
    embedding VECTOR(1536),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""

# SQL to create an index for faster vector similarity searches
CREATE_VECTOR_INDEX = f"""
-- Create a vector similarity search index
CREATE INDEX IF NOT EXISTS holocron_embedding_idx
ON {HOLOCRON_TABLE_NAME}
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
"""

# SQL to create a function for similarity search
CREATE_MATCH_FUNCTION = f"""
-- Create a function to match documents by similarity
CREATE OR REPLACE FUNCTION match_holocron_documents(
    query_embedding VECTOR(1536),
    match_threshold FLOAT,
    match_count INT
)
RETURNS TABLE(
    id BIGINT,
    content TEXT,
    metadata JSONB,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    {HOLOCRON_TABLE_NAME}.id,
    {HOLOCRON_TABLE_NAME}.content,
    {HOLOCRON_TABLE_NAME}.metadata,
    1 - ({HOLOCRON_TABLE_NAME}.embedding <=> query_embedding) AS similarity
  FROM {HOLOCRON_TABLE_NAME}
  WHERE 1 - ({HOLOCRON_TABLE_NAME}.embedding <=> query_embedding) > match_threshold
  ORDER BY {HOLOCRON_TABLE_NAME}.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
"""

async def setup_database():
    """Set up the Supabase database for the Holocron Knowledge System"""
    # Load environment variables
    load_dotenv()
    
    # Initialize Supabase client
    try:
        logger.info(f"Connecting to Supabase at {SUPABASE_URL}")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Execute each SQL statement directly
        sql_statements = [
            ("Enable pgvector extension", CREATE_VECTOR_EXTENSION),
            (f"Create table '{HOLOCRON_TABLE_NAME}'", CREATE_HOLOCRON_TABLE),
            ("Create vector similarity index", CREATE_VECTOR_INDEX),
            ("Create match_holocron_documents function", CREATE_MATCH_FUNCTION)
        ]
        
        for description, sql in sql_statements:
            logger.info(f"Executing: {description}...")
            try:
                # Execute SQL directly
                await asyncio.to_thread(
                    lambda: supabase.query(sql).execute()
                )
                logger.info(f"✅ Successfully executed: {description}")
            except Exception as sql_error:
                logger.error(f"Error executing {description}: {str(sql_error)}")
                if hasattr(sql_error, 'response'):
                    logger.error(f"Response status: {sql_error.response.status_code}")
                    logger.error(f"Response text: {sql_error.response.text}")
        
        logger.info("✅ Database setup complete!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Database setup failed: {str(e)}")
        if hasattr(e, 'response'):
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response text: {e.response.text}")
        return False

if __name__ == "__main__":
    asyncio.run(setup_database()) 