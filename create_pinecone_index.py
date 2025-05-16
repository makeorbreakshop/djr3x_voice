#!/usr/bin/env python3
import os
import pinecone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get Pinecone API key
api_key = os.getenv("PINECONE_API_KEY")
if not api_key:
    raise ValueError("PINECONE_API_KEY not found in environment variables")

# Get Pinecone index name (default to holocron-knowledge)
index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")

print(f"Creating Pinecone index: {index_name}")
print(f"API Key (first 5 chars): {api_key[:5]}...")

# Initialize Pinecone
pinecone.init(api_key=api_key)

# Check if index already exists
existing_indexes = pinecone.list_indexes()
print(f"Existing indexes: {existing_indexes}")

if index_name in existing_indexes:
    print(f"Index '{index_name}' already exists!")
else:
    # Create the index - the dimension depends on your embedding model
    # Common dimensions: 
    # OpenAI ada-002: 1536
    # OpenAI text-embedding-3-small: 1536
    # OpenAI text-embedding-3-large: 3072 
    dimension = 1536  # Adjust based on your embedding model

    print(f"Creating new index '{index_name}' with dimension {dimension}...")
    try:
        pinecone.create_index(
            name=index_name,
            dimension=dimension,
            metric="cosine"
        )
        print(f"Successfully created index: {index_name}")
    except Exception as e:
        print(f"Error creating index: {e}")

# List all indexes to confirm
print("\nCurrent indexes:")
print(pinecone.list_indexes()) 