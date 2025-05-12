#!/usr/bin/env python3
"""
Create a small test batch for verifying Pinecone upload format.
Creates 3 vectors that should be similar in structure to existing vectors.
"""

import os
import json
import uuid
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# Create test vectors directory if it doesn't exist
TEST_DIR = "data/pinecone_test"
os.makedirs(TEST_DIR, exist_ok=True)

# Generate a unique filename with a unique timestamp to ensure no URL conflicts
timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
filename = f"pinecone_test_{timestamp}.parquet"
filepath = os.path.join(TEST_DIR, filename)

# Generate a small number of test vectors
NUM_VECTORS = 3
VECTOR_DIM = 1536  # Common dimension for OpenAI embeddings

# Create test data
vectors_data = []
for i in range(NUM_VECTORS):
    # Generate a random vector
    vector = np.random.rand(VECTOR_DIM).tolist()
    
    # Create unique URL with timestamp and a clear identifier 
    test_url = f"https://starwars.fandom.com/wiki/PineconeTest_{timestamp}_{i}"
    
    # Create metadata with format similar to existing Wookieepedia entries
    metadata = {
        "url": test_url,
        "title": f"Pinecone Test Article {i}",
        "content": f"# Pinecone Test Article {i}\n\nThis is a test article for verifying Pinecone upload format. This should appear in the Pinecone index and be retrievable.\n\n## Section 1\n\nHere is some test content.\n\n## Section 2\n\nMore test content.\n\n[Source] {test_url}",
        "category": "Test",
        "is_test": True,
        "timestamp": timestamp
    }
    
    # Create vector record
    vector_record = {
        "id": f"pinecone_test_{timestamp}_{i}",  # Use a clearly identifiable ID 
        "values": vector,
        "metadata": json.dumps(metadata)
    }
    
    vectors_data.append(vector_record)

# Convert to DataFrame
df = pd.DataFrame(vectors_data)

# Save as Parquet file
df.to_parquet(filepath, index=False)

print(f"Created Pinecone test batch with {NUM_VECTORS} vectors at: {filepath}")
print(f"Run the upload script with: python scripts/upload_with_url_tracking.py --batch-size 3 --vectors-dir data/pinecone_test")
print(f"Then check Pinecone to verify these vectors with IDs: {[record['id'] for record in vectors_data]}") 