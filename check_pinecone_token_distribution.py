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

# Get index stats
print("Fetching index statistics...")
try:
    stats = index.describe_index_stats()
    # Handle different response structures based on Pinecone SDK version
    if isinstance(stats, dict) and "totalRecordCount" in stats:
        total_vectors = stats["totalRecordCount"]
    elif isinstance(stats, dict) and "total_record_count" in stats:
        total_vectors = stats["total_record_count"]
    else:
        # Try to access as an object
        total_vectors = getattr(stats, "total_record_count", None)
        if total_vectors is None:
            total_vectors = "unknown"
    print(f"Total vectors in index: {total_vectors}")
except Exception as e:
    print(f"Error getting stats: {e}")
    print("Continuing with vector sampling...")
    total_vectors = "unknown"

# Sample more vectors randomly - try different approaches
print("Sampling vectors using multiple approaches...")

# Approach 1: Sample with random sparse vectors
sample_size = 200
all_matches = []

# Attempt different search strategies to get diverse samples
search_strategies = [
    {
        "name": "Random dense vector",
        "func": lambda: [random.uniform(-1, 1) for _ in range(1536)]
    },
    {
        "name": "Sparse but diverse queries",
        "func": lambda: [1.0 if random.random() < 0.05 else 0.0 for _ in range(1536)]
    },
    {
        "name": "Random vectors with filter",
        "func": lambda: [random.uniform(-1, 1) for _ in range(1536)],
        "filter": {"chunk_id": {"$exists": True}}
    }
]

# Try each strategy
for strategy in search_strategies:
    print(f"\nTrying strategy: {strategy['name']}")
    
    for i in range(5):  # Try 5 times with each strategy
        # Generate vector according to strategy
        vector = strategy["func"]()
        
        # Normalize
        magnitude = (sum(x**2 for x in vector)) ** 0.5
        if magnitude > 0:
            normalized_vector = [x/magnitude for x in vector]
        else:
            normalized_vector = [0] * 1536
            normalized_vector[0] = 1.0  # At least one non-zero element
            
        # Query with optional filter
        query_args = {
            "vector": normalized_vector,
            "top_k": 50,
            "include_metadata": True
        }
        
        if "filter" in strategy:
            query_args["filter"] = strategy["filter"]
            
        query_response = index.query(**query_args)
        
        # Process matches
        matches = query_response['matches']
        print(f"  Round {i+1}: Found {len(matches)} vectors")
        
        # Add non-test vectors with content to our sample
        for match in matches:
            metadata = match.get('metadata', {})
            if metadata.get('is_test', False) or 'content' not in metadata:
                continue
            
            # Skip if we already have this vector
            duplicate = False
            for existing in all_matches:
                if existing.get('id', '') == match.get('id', ''):
                    duplicate = True
                    break
                    
            if not duplicate:
                all_matches.append(match)
                
        # If we have enough, break
        if len(all_matches) >= sample_size:
            break
            
    # If we have enough, break
    if len(all_matches) >= sample_size:
        break

print(f"\nCollected {len(all_matches)} unique vectors for analysis")

# Group by article URL to see chunk distribution
article_chunks = defaultdict(list)
for match in all_matches:
    metadata = match.get('metadata', {})
    url = metadata.get('url', '')
    if url:
        article_chunks[url].append(match)

print(f"Found chunks from {len(article_chunks)} distinct articles")

# Check a few articles with multiple chunks to see if there's a pattern
print("\nAnalyzing articles with multiple chunks:")
multi_chunk_articles = [(url, chunks) for url, chunks in article_chunks.items() if len(chunks) > 1]
for i, (url, chunks) in enumerate(sorted(multi_chunk_articles, key=lambda x: len(x[1]), reverse=True)):
    if i >= 5:  # Only show top 5
        break
        
    print(f"\nArticle: {url} - {len(chunks)} chunks")
    chunks_sorted = sorted(chunks, key=lambda x: x.get('metadata', {}).get('chunk_id', ''))
    
    for chunk in chunks_sorted:
        metadata = chunk.get('metadata', {})
        chunk_id = metadata.get('chunk_id', 'unknown')
        content = metadata.get('content', '')
        tokens = count_tokens(content)
        
        print(f"  Chunk {chunk_id}: {tokens} tokens, {len(content)} chars")

# Analyze token counts
token_counts = []
content_tokens_values = []
chunk_ids = set()

print(f"\nAnalyzing token distribution for {len(all_matches)} vectors...")

for match in all_matches:
    metadata = match.get('metadata', {})
    content = metadata.get('content', '')
    chunk_id = metadata.get('chunk_id', '')
    
    # Track unique chunk IDs
    if chunk_id:
        chunk_ids.add(chunk_id)
    
    # Check if content_tokens is in metadata
    if 'content_tokens' in metadata:
        token_count = float(metadata['content_tokens'])
        content_tokens_values.append(token_count)
    else:
        # Count tokens in content
        token_count = count_tokens(content)
    
    token_counts.append(token_count)

# Calculate statistics
if token_counts:
    # Overall statistics
    print("\nToken count statistics:")
    print(f"  Min: {min(token_counts)}")
    print(f"  Max: {max(token_counts)}")
    print(f"  Mean: {statistics.mean(token_counts):.2f}")
    print(f"  Median: {statistics.median(token_counts)}")
    print(f"  Standard deviation: {statistics.stdev(token_counts):.2f}")
    
    # Check if content_tokens metadata is present
    if content_tokens_values:
        print(f"\nFound {len(content_tokens_values)} vectors with 'content_tokens' metadata")
        print(f"  Average content_tokens value: {statistics.mean(content_tokens_values):.2f}")

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

    # Check the dev log claim
    high_token_counts = sum(1 for count in token_counts if count > 2000)
    high_percent = 100 * high_token_counts / len(token_counts)
    
    print(f"\nVectors with >2000 tokens: {high_token_counts} ({high_percent:.2f}%)")
    
    if high_percent > 50:
        print("\nCONCLUSION: The dev log is CORRECT. Most vectors have significantly more tokens than intended.")
    else:
        print("\nCONCLUSION: The dev log may not be accurate. Most vectors do not have excessive token counts.")
        
    # Check if current chunking is word-based or token-based
    if content_tokens_values:
        # If content_tokens values are mostly round numbers, likely token-based
        rounded_values = [v for v in content_tokens_values if v == round(v)]
        if len(rounded_values) / len(content_tokens_values) > 0.9:
            print("\nChunking appears to be TOKEN-BASED (most content_tokens values are whole numbers)")
        else:
            print("\nChunking appears to be WORD-BASED (content_tokens values have decimals)")
    
    # Final assessment
    if high_percent < 10 and statistics.mean(token_counts) < 1000:
        print("\nBased on this analysis, the vectors in Pinecone do NOT appear to have the chunking issue described in the dev log.")
        print("The majority of vectors have reasonable token counts well below 2000 tokens.")
    elif high_percent > 50:
        print("\nBased on this analysis, the vectors in Pinecone DO have the chunking issue described in the dev log.")
        print("Most vectors have excessive token counts above 2000 tokens.")
    else:
        print("\nThe analysis is inconclusive. There may be a mix of vectors with different chunking strategies.")
        
else:
    print("No token counts available for analysis.") 