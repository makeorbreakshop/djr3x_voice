#!/usr/bin/env python3
"""
Test script to insert a single vector directly into the Supabase database using SQL.
This addresses the limitations of the Supabase Python client with vector operations.
"""

import os
import sys
import json
import asyncio
import logging
import openai
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

# Import the OpenAI API key reader
from src.holocron.data_processor import read_api_key_from_env_file

# Import app settings
from config.app_settings import (
    SUPABASE_URL,
    SUPABASE_KEY,
    HOLOCRON_TABLE_NAME,
    EMBEDDING_MODEL
)

async def generate_embedding(text):
    """Generate an embedding vector for the given text using OpenAI's API."""
    api_key = read_api_key_from_env_file()
    if not api_key:
        logger.error("Failed to read OpenAI API key")
        return None
        
    openai.api_key = api_key
    
    try:
        response = openai.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
            encoding_format="float"
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        return None

def get_db_connection():
    """Create a direct PostgreSQL connection using Supabase credentials."""
    # Extract connection details from Supabase URL
    # Format: https://<project_id>.supabase.co
    project_id = SUPABASE_URL.split('//')[1].split('.')[0]
    
    # Construct PostgreSQL connection string
    host = f"{project_id}.supabase.co"
    dbname = "postgres"
    user = "postgres"
    password = SUPABASE_KEY  # Service role key is used as password
    port = "5432"
    
    # Connect to the database
    try:
        conn = psycopg2.connect(
            host=host,
            dbname=dbname,
            user=user,
            password=password,
            port=port,
            sslmode='require'
        )
        logger.info(f"Connected to PostgreSQL database at {host}")
        return conn
    except Exception as e:
        logger.error(f"Error connecting to PostgreSQL: {e}")
        return None

async def insert_test_vector():
    """Generate and insert a test vector into the database."""
    # Generate a test embedding
    test_text = "The Millennium Falcon is a modified YT-1300 Corellian light freighter."
    logger.info(f"Generating embedding for: '{test_text}'")
    
    embedding = await generate_embedding(test_text)
    if not embedding:
        logger.error("Failed to generate embedding")
        return False
    
    logger.info(f"Successfully generated embedding with {len(embedding)} dimensions")
    
    # Connect to the database
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        # Create a cursor
        cur = conn.cursor()
        
        # Prepare the SQL statement
        sql = f"""
        INSERT INTO {HOLOCRON_TABLE_NAME} (
            content, 
            content_tokens, 
            metadata, 
            embedding
        ) VALUES (
            %s, 
            %s, 
            %s, 
            %s::vector
        ) RETURNING id;
        """
        
        # Create metadata JSON
        metadata = json.dumps({
            "title": "Test Vector",
            "source": "Insert Test Script",
            "section": "Test"
        })
        
        # Format the embedding as a proper PostgreSQL vector
        # PostgreSQL expects a string like '[0.1, 0.2, 0.3, ...]'
        vector_str = str(embedding).replace("'", "").replace(" ", "")
        
        # Execute the SQL statement
        logger.info("Inserting test vector into database...")
        cur.execute(sql, (
            test_text,                 # content
            len(test_text.split()),    # content_tokens (simplified)
            metadata,                  # metadata
            vector_str                 # embedding
        ))
        
        # Get the ID of the inserted row
        row_id = cur.fetchone()[0]
        
        # Commit the transaction
        conn.commit()
        
        logger.info(f"✅ Successfully inserted test vector with ID: {row_id}")
        
        # Test retrieving the vector to confirm it was inserted correctly
        cur.execute(f"SELECT id, content FROM {HOLOCRON_TABLE_NAME} WHERE id = %s", (row_id,))
        result = cur.fetchone()
        logger.info(f"Retrieved test vector: ID={result[0]}, Content='{result[1]}'")
        
        return True
    
    except Exception as e:
        logger.error(f"Error inserting test vector: {e}")
        return False
    
    finally:
        if conn:
            conn.close()

async def main():
    """Run the test vector insertion."""
    load_dotenv()
    
    success = await insert_test_vector()
    
    if success:
        logger.info("✅ Test vector insertion successful")
    else:
        logger.error("❌ Test vector insertion failed")

if __name__ == "__main__":
    asyncio.run(main()) 