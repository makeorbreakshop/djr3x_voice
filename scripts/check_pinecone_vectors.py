#!/usr/bin/env python3
"""
Script to examine vectors in a Pinecone index.
"""

import argparse
from pinecone import Pinecone

def main():
    parser = argparse.ArgumentParser(description='Examine vectors in a Pinecone index')
    parser.add_argument('--index', type=str, required=True, help='Index name')
    parser.add_argument('--namespace', type=str, default='', help='Namespace (default: empty string)')
    parser.add_argument('--count', type=int, default=10, help='Number of vectors to examine')
    args = parser.parse_args()
    
    # Connect to Pinecone
    pc = Pinecone()
    index = pc.Index(args.index)
    
    # Get index stats
    stats = index.describe_index_stats()
    print(f"Total vector count: {stats.total_vector_count}")
    print(f"Namespaces: {stats.namespaces}")
    
    # List some vectors
    vector_ids = []
    count = 0
    for batch in index.list(namespace=args.namespace):
        vector_ids.extend(batch)
        count += len(batch)
        if count >= args.count:
            break
    
    print(f"\nListed {len(vector_ids)} vector IDs:")
    for i, vid in enumerate(vector_ids[:args.count]):
        print(f"{i+1}: {vid}")
    
    # Fetch some vectors
    if vector_ids:
        sample_ids = vector_ids[:min(5, len(vector_ids))]
        print(f"\nFetching {len(sample_ids)} sample vectors...")
        response = index.fetch(ids=sample_ids, namespace=args.namespace)
        
        for i, (vid, vector) in enumerate(response.vectors.items()):
            print(f"\nVector {i+1} - ID: {vid}")
            if hasattr(vector, 'metadata') and vector.metadata:
                print(f"  Metadata keys: {list(vector.metadata.keys())}")
                if 'title' in vector.metadata:
                    print(f"  Title: {vector.metadata['title']}")
                if 'embedding_model' in vector.metadata:
                    print(f"  Model: {vector.metadata['embedding_model']}")
            else:
                print("  No metadata")
            
            if hasattr(vector, 'values'):
                values = vector.values
                print(f"  Values length: {len(values)}")
                print(f"  First 5 values: {values[:5]}")
            else:
                print("  No values")

if __name__ == '__main__':
    main() 