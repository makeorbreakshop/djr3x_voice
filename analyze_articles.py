#!/usr/bin/env python3
import os
import json
import glob
import random
import re

def analyze_articles():
    """Analyze a few articles to better understand the patterns and detection."""
    batch_files = glob.glob('data/test_processing/batch_*.json')
    
    # Sample 5 random batch files
    sample_batch_files = random.sample(batch_files, min(5, len(batch_files)))
    
    # Patterns to check
    patterns = {
        "Canon template": r'\{\{Canon\}\}',
        "Canon article template": r'\{\{Canon article\}\}',
        "Canon category": r'\[\[Category:Canon articles\]\]',
        "Legends template": r'\{\{Legends\}\}',
        "Legends article template": r'\{\{Legends article\}\}',
        "Legends category": r'\[\[Category:Legends articles\]\]',
        "Top|leg": r'\{\{Top\|leg\}\}',
        "Top|can": r'\{\{Top\|can\}\}',
        "Top (no param)": r'\{\{Top\}\}'
    }
    
    # Count occurrences
    pattern_counts = {pattern: 0 for pattern in patterns}
    total_articles = 0
    canon_count = 0
    legends_count = 0
    
    # Analyze articles from the sample batch files
    analyzed_articles = []
    for batch_file in sample_batch_files:
        try:
            with open(batch_file, 'r') as f:
                batch = json.load(f)
                for article in batch:
                    total_articles += 1
                    if article.get('is_canonical') is True:
                        canon_count += 1
                    elif article.get('is_canonical') is False:
                        legends_count += 1
                    
                    content = article.get('content', '')
                    pattern_matches = {}
                    for pattern_name, pattern in patterns.items():
                        if re.search(pattern, content, re.IGNORECASE):
                            pattern_counts[pattern_name] += 1
                            pattern_matches[pattern_name] = True
                        else:
                            pattern_matches[pattern_name] = False
                    
                    # Add to analyzed articles for detailed inspection
                    if len(analyzed_articles) < 10:
                        analyzed_articles.append({
                            'title': article.get('title'),
                            'is_canonical': article.get('is_canonical'),
                            'pattern_matches': pattern_matches
                        })
        except Exception as e:
            print(f"Error processing {batch_file}: {e}")
    
    # Print summary
    print(f"Analyzed {total_articles} articles")
    print(f"Canon articles: {canon_count}")
    print(f"Legends articles: {legends_count}")
    print("\nPattern occurrences:")
    for pattern, count in pattern_counts.items():
        print(f"{pattern}: {count}")
    
    # Print detailed analysis of a few articles
    print("\nDetailed article analysis:")
    for i, article in enumerate(analyzed_articles, 1):
        print(f"\nArticle {i}: {article['title']}")
        print(f"is_canonical: {article['is_canonical']}")
        for pattern, matches in article['pattern_matches'].items():
            if matches:
                print(f"  - {pattern}: Yes")
    
    # Look at the process_wiki_dump.py file to analyze how Canon/Legends detection is implemented
    try:
        with open('src/holocron/wiki_processing/process_wiki_dump.py', 'r') as f:
            content = f.read()
            is_canonical_method = re.search(r'def _is_canonical_content.*?return False', content, re.DOTALL)
            if is_canonical_method:
                print("\nCanon detection implementation:")
                print(is_canonical_method.group(0))
    except Exception as e:
        print(f"Error reading process_wiki_dump.py: {e}")

if __name__ == "__main__":
    analyze_articles() 