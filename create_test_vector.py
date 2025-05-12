#!/usr/bin/env python3
"""
Create a test vector file with new URLs for testing the upload script.
"""

import os
import json
import uuid
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# Create test vectors directory if it doesn't exist
TEST_DIR = "data/test_vectors"
os.makedirs(TEST_DIR, exist_ok=True)

# Generate a unique filename
timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
filename = f"test_vectors_{timestamp}.parquet"
filepath = os.path.join(TEST_DIR, filename)

# Generate random test vectors
NUM_VECTORS = 20
VECTOR_DIM = 1536  # Common dimension for embeddings

# Create test data
vectors_data = []
for i in range(NUM_VECTORS):
    # Generate a random vector
    vector = np.random.rand(VECTOR_DIM).tolist()
    
    # Create unique URL that won't be in the existing processing_status.csv
    test_url = f"https://starwars.fandom.com/wiki/Test_Vector_{timestamp}_{i}"
    
    # Create metadata with URL
    metadata = {
        "url": test_url,
        "title": f"Test Vector {i}",
        "content": f"# Test Vector {i}\n\nThis is a test vector for testing the upload script.\n\n[Source] {test_url}",
        "is_test": True
    }
    
    # Create vector record
    vector_record = {
        "id": str(uuid.uuid4()),
        "values": vector,
        "metadata": json.dumps(metadata)  # Match the format in the existing files
    }
    
    vectors_data.append(vector_record)

# Convert to DataFrame
df = pd.DataFrame(vectors_data)

# Save as Parquet file
df.to_parquet(filepath, index=False)

print(f"Created test vector file with {NUM_VECTORS} vectors at: {filepath}")
print(f"These vectors have URLs that are not yet in the processing_status.csv")
print(f"Run the upload script with: python scripts/upload_with_url_tracking.py --test --batch-size 5 --max-files 1") 