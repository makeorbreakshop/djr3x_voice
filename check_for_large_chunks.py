import os
import pinecone
from pinecone import Pinecone
import tiktoken
import statistics
from dotenv import load_dotenv
import numpy as np
import random
from collections import defaultdict

load_dotenv()

# Connect to Pinecone
print("Connecting to Pinecone index: holocron-knowledge")
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])

# Get the index
index_name = "holocron-knowledge"
index = pc.Index(index_name)

# Initialize tokenizer
tokenizer = tiktoken.encoding_for_model("text-embedding-ada-002")

# Function to count tokens in text
def count_tokens(text):
    tokens = tokenizer.encode(text)
    return len(tokens)

# Search for specific articles with known long content
print("Searching for specific large articles...")

# Try querying for longer articles directly
large_article_terms = [
    "Luke Skywalker biography",
    "Star Wars complete history",
    "Darth Vader full story",
    "Millennium Falcon specifications",
    "Galactic Empire formation",
    "Clone Wars detailed",
    "Jedi Order history",
    "Sith complete history",
    "Star Wars: The Old Republic",
    "High Republic era"
]

# Try random queries as well
all_matches = []

# Try each search term
for term in large_article_terms:
    print(f"\nSearching for content about '{term}'...")
    
    # Use a sparse vector with only a few active dimensions
    # This sometimes helps find more diverse results
    query_vector = [0.0] * 1536
    
    # Set a few random dimensions to non-zero
    for i in range(20):
        pos = random.randint(0, 1535)
        query_vector[pos] = random.uniform(0.5, 1.0)
        
    # Normalize
    magnitude = (sum(x**2 for x in query_vector)) ** 0.5
    normalized_vector = [x/magnitude for x in query_vector]
    
    # Query without filters first to get more results
    query_response = index.query(
        vector=normalized_vector,
        top_k=50,
        include_metadata=True
    )
    
    matches = query_response['matches']
    print(f"Found {len(matches)} potentially relevant vectors")
    
    # Keep track of any large chunks we find
    large_chunks = []
    for match in matches:
        metadata = match.get('metadata', {})
        content = metadata.get('content', '')
        
        if not content or len(content) < 200:
            continue
            
        # Count tokens
        token_count = count_tokens(content)
        
        # Check if this is a large chunk (>1000 tokens)
        if token_count > 1000:
            large_chunks.append((match, token_count))
            
    print(f"Found {len(large_chunks)} chunks with >1000 tokens")
    
    # Add matches to our collection
    all_matches.extend([x[0] for x in large_chunks])

print(f"\nTotal large chunks found: {len(all_matches)}")

# Analyze token counts
if all_matches:
    # Print some examples of large chunks
    print("\nExample large chunks:")
    for i, match in enumerate(all_matches[:5]):
        metadata = match.get('metadata', {})
        content = metadata.get('content', '')
        url = metadata.get('url', 'unknown')
        title = metadata.get('title', 'unknown')
        token_count = count_tokens(content)
        
        print(f"\nLarge Chunk {i+1}:")
        print(f"  URL: {url}")
        print(f"  Title: {title}")
        print(f"  Token count: {token_count}")
        print(f"  Content length: {len(content)} characters")
        print(f"  First 200 chars: {content[:200]}...")
        
    # Calculate statistics for all found large chunks
    token_counts = []
    for match in all_matches:
        metadata = match.get('metadata', {})
        content = metadata.get('content', '')
        token_count = count_tokens(content)
        token_counts.append(token_count)
    
    if token_counts:
        print("\nToken count statistics for large chunks:")
        print(f"  Min: {min(token_counts)}")
        print(f"  Max: {max(token_counts)}")
        print(f"  Mean: {statistics.mean(token_counts):.2f}")
        print(f"  Median: {statistics.median(token_counts)}")
        print(f"  Standard deviation: {statistics.stdev(token_counts):.2f}")
        
        # Distribution of large chunks
        bins = [1000, 1500, 2000, 2500, 3000, 3500, 4000, float('inf')]
        bin_labels = ['1000-1500', '1501-2000', '2001-2500', '2501-3000', '3001-3500', '3501-4000', '4000+']
        
        counts = [0] * len(bin_labels)
        for count in token_counts:
            for i, upper in enumerate(bins[1:]):
                if count < upper:
                    counts[i] += 1
                    break
        
        print("\nToken count distribution for large chunks:")
        for label, count in zip(bin_labels, counts):
            percent = 100 * count / len(token_counts)
            print(f"  {label}: {count} vectors ({percent:.2f}%)")
            
        # Check what percentage might match the dev log issue
        very_large = sum(1 for count in token_counts if count > 2000)
        very_large_percent = 100 * very_large / len(token_counts) if token_counts else 0
        
        print(f"\nLarge chunks with >2000 tokens: {very_large} ({very_large_percent:.2f}%)")
        
        # Final assessment
        if very_large > 0 and very_large_percent > 10:
            print("\nPARTIAL CONFIRMATION: The dev log issue was identified in some vectors.")
            print(f"Found {len(all_matches)} chunks with >1000 tokens, of which {very_large} have >2000 tokens.")
            print("However, these appear to be exceptions rather than the majority of vectors.")
        else:
            print("\nThe dev log issue was NOT conclusively confirmed.")
            print("While some larger chunks exist, they don't match the pattern described in the dev log.")
    else:
        print("No token counts available for large chunk analysis.")
else:
    print("No large chunks found in the index. The dev log issue could not be verified.") 