#!/usr/bin/env python3
import os
import json
import glob

def count_canon_legends():
    """Count Canon vs. Legends articles in the batch files."""
    total_articles = 0
    canon_count = 0
    legends_count = 0
    unknown_count = 0
    
    batch_files = glob.glob('data/test_processing/batch_*.json')
    
    for batch_file in batch_files:
        try:
            with open(batch_file, 'r') as f:
                batch = json.load(f)
                for article in batch:
                    total_articles += 1
                    if article.get('is_canonical') is True:
                        canon_count += 1
                    elif article.get('is_canonical') is False:
                        legends_count += 1
                    else:
                        unknown_count += 1
        except Exception as e:
            print(f"Error processing {batch_file}: {e}")
    
    print(f"Total articles: {total_articles}")
    print(f"Canon articles: {canon_count}")
    print(f"Legends articles: {legends_count}")
    print(f"Unknown articles: {unknown_count}")
    
    # Let's also check a few random articles for debugging
    if batch_files:
        with open(batch_files[0], 'r') as f:
            batch = json.load(f)
            if batch:
                article = batch[0]
                print("\nExample article:")
                print(f"Title: {article.get('title')}")
                print(f"is_canonical: {article.get('is_canonical')}")
                # Check for Canon/Legends markers in the content
                content = article.get('content', '')
                print(f"Has {{{{Canon}}}} marker: {'{{Canon}}' in content}")
                print(f"Has {{{{Legends}}}} marker: {'{{Legends}}' in content}")
                print(f"Has {{{{Top|leg}}}} marker: {'{{Top|leg}}' in content}")
                print(f"Has {{{{Top|can}}}} marker: {'{{Top|can}}' in content}")

if __name__ == "__main__":
    count_canon_legends() 