#!/usr/bin/env python3
import os
import json
import glob
from collections import defaultdict

def check_duplicates():
    """Check for duplicate articles in the batch files."""
    article_by_title = defaultdict(list)
    article_by_revision_id = defaultdict(list)
    batch_files = glob.glob('data/test_processing/batch_*.json')
    
    total_articles = 0
    
    print(f"Scanning {len(batch_files)} batch files for duplicates...")
    
    for batch_file in batch_files:
        try:
            with open(batch_file, 'r') as f:
                batch = json.load(f)
                for article in batch:
                    total_articles += 1
                    title = article.get('title', '')
                    revision_id = article.get('revision_id', '')
                    
                    article_by_title[title].append(batch_file)
                    article_by_revision_id[revision_id].append(batch_file)
        except Exception as e:
            print(f"Error processing {batch_file}: {e}")
    
    print(f"Total articles processed: {total_articles}")
    print(f"Unique article titles: {len(article_by_title)}")
    print(f"Unique revision IDs: {len(article_by_revision_id)}")
    
    # Check for duplicates
    duplicate_titles = {title: files for title, files in article_by_title.items() if len(files) > 1}
    duplicate_revisions = {rev_id: files for rev_id, files in article_by_revision_id.items() if len(files) > 1}
    
    print(f"\nDuplicate titles: {len(duplicate_titles)}")
    print(f"Duplicate revision IDs: {len(duplicate_revisions)}")
    
    # Show some examples of duplicates
    if duplicate_revisions:
        print("\nExample duplicate articles by revision ID:")
        count = 0
        for rev_id, files in duplicate_revisions.items():
            if count >= 5:
                break
            count += 1
            print(f"Revision ID {rev_id} appears in {len(files)} files:")
            for batch_file in files[:3]:  # Show first 3 batch files only
                print(f"  - {batch_file}")
            if len(files) > 3:
                print(f"  - ... and {len(files) - 3} more")

if __name__ == "__main__":
    check_duplicates() 