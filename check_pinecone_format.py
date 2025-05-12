#!/usr/bin/env python3
"""
Check the format of existing vectors in Pinecone
to ensure our test vectors match the correct format.
"""

import os
import pinecone
from pinecone import Pinecone, ServerlessSpec
import tiktoken
import statistics
from dotenv import load_dotenv
import numpy as np
import random

# Load environment variables
load_dotenv()

# Get Pinecone API key and index name
api_key = os.getenv("PINECONE_API_KEY")
index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")

if not api_key:
    raise ValueError("PINECONE_API_KEY not found in environment variables")

# Initialize Pinecone client
pc = Pinecone(api_key=api_key)

# Connect to the index
print(f"Connecting to Pinecone index: {index_name}")
index = pc.Index(index_name)

# Initialize tokenizer
tokenizer = tiktoken.encoding_for_model("text-embedding-ada-002")

# Function to count tokens in text
def count_tokens(text):
    tokens = tokenizer.encode(text)
    return len(tokens)

# Try different search terms to find real content
search_terms = ["Luke Skywalker", "Millennium Falcon", "Darth Vader", "Coruscant", "Death Star"]

# Try each search term to find non-test vectors
all_matches = []
for term in search_terms:
    print(f"Searching for content about '{term}'...")
    # Generate a random embedding vector as a query
    random_vector = [random.uniform(-1, 1) for _ in range(1536)]
    
    # Normalize the vector
    magnitude = (sum(x**2 for x in random_vector)) ** 0.5
    normalized_vector = [x/magnitude for x in random_vector]
    
    query_response = index.query(
        vector=normalized_vector,
        top_k=50,
        include_metadata=True,
        filter={"is_test": {"$exists": False}}  # Exclude test vectors
    )
    
    matches = query_response['matches']
    print(f"Found {len(matches)} potentially relevant vectors")
    
    if matches:
        # Check if these look like real content
        real_content = []
        for match in matches:
            metadata = match.get('metadata', {})
            content = metadata.get('content', '')
            title = metadata.get('title', '')
            url = metadata.get('url', '')
            
            # Look for starwars.fandom.com URLs that aren't test articles
            if 'starwars.fandom.com' in url and 'pineconetest' not in url.lower() and len(content) > 100:
                if term.lower() in content.lower() or term.lower() in title.lower():
                    real_content.append(match)
        
        if real_content:
            print(f"Found {len(real_content)} real articles about '{term}'")
            all_matches.extend(real_content)
            if len(all_matches) >= 100:
                break

# Analyze token counts
token_counts = []
content_lengths = []
has_token_count = 0

print(f"\nAnalyzing {len(all_matches)} real vectors from Pinecone index...")

for i, match in enumerate(all_matches[:100]):  # Limit to 100 for analysis
    if i < 3:  # Print sample metadata for first few records
        print(f"\nSample {i+1} metadata:")
        for key, value in match['metadata'].items():
            if key == 'content':
                print(f"  content (truncated): {value[:100]}...")
            else:
                print(f"  {key}: {value}")
    
    # Check if token_count is already in metadata
    if 'token_count' in match['metadata']:
        has_token_count += 1
        token_count = int(match['metadata']['token_count'])
    else:
        # Count tokens in content
        content = match['metadata'].get('content', '')
        token_count = count_tokens(content)
    
    token_counts.append(token_count)
    content_lengths.append(len(match['metadata'].get('content', '')))

# Calculate statistics
if token_counts:
    print("\nToken count statistics:")
    print(f"  Min: {min(token_counts)}")
    print(f"  Max: {max(token_counts)}")
    print(f"  Mean: {statistics.mean(token_counts):.2f}")
    print(f"  Median: {statistics.median(token_counts)}")
    print(f"  Standard deviation: {statistics.stdev(token_counts):.2f}")
    print(f"\nPercent of vectors with token_count metadata: {100 * has_token_count / len(all_matches[:100]):.2f}%")

    # Histogram-like distribution
    bins = [0, 250, 500, 750, 1000, 1500, 2000, 2500, 3000, 3500, 4000, float('inf')]
    bin_labels = ['0-250', '251-500', '501-750', '751-1000', '1001-1500', '1501-2000', 
                 '2001-2500', '2501-3000', '3001-3500', '3501-4000', '4000+']
    
    counts = [0] * len(bin_labels)
    for count in token_counts:
        for i, upper in enumerate(bins[1:]):
            if count < upper:
                counts[i] += 1
                break
    
    print("\nToken count distribution:")
    for label, count in zip(bin_labels, counts):
        percent = 100 * count / len(token_counts)
        print(f"  {label}: {count} vectors ({percent:.2f}%)")

    # If most vectors fall into the 2000+ range, the dev log is likely correct
    high_token_counts = sum(1 for count in token_counts if count > 2000)
    high_percent = 100 * high_token_counts / len(token_counts)
    
    print(f"\nVectors with >2000 tokens: {high_token_counts} ({high_percent:.2f}%)")
    
    if high_percent > 50:
        print("\nCONCLUSION: The dev log is CORRECT. Most vectors have significantly more tokens than intended.")
    else:
        print("\nCONCLUSION: The dev log may not be accurate. Most vectors do not have excessive token counts.")
else:
    print("No token counts available for analysis.")

print("\nThis information will help ensure our test vectors match the existing format.") 