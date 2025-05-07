#!/usr/bin/env python3
import aiohttp
import asyncio
import json
from typing import Dict, List, Set
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WOOKIEEPEDIA_API = "https://starwars.fandom.com/api.php"

async def get_category_members(session: aiohttp.ClientSession, category: str) -> int:
    """Get all members of a category using continuation."""
    total_count = 0
    continue_params = {}
    
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": "500",  # Max allowed
            "cmnamespace": "0",  # Only articles, not subcategories
            **continue_params
        }
        
        try:
            async with session.get(WOOKIEEPEDIA_API, params=params) as response:
                data = await response.json()
                
                if "query" in data and "categorymembers" in data["query"]:
                    members = data["query"]["categorymembers"]
                    total_count += len(members)
                    logger.info(f"Found {len(members)} articles in {category} (Total: {total_count})")
                
                if "continue" not in data:
                    break
                    
                continue_params = data["continue"]
                await asyncio.sleep(1)  # Rate limiting
        except Exception as e:
            logger.error(f"Error processing {category}: {e}")
            break
    
    return total_count

async def get_total_articles(session: aiohttp.ClientSession) -> int:
    """Get total number of articles in the wiki."""
    params = {
        "action": "query",
        "format": "json",
        "meta": "siteinfo",
        "siprop": "statistics"
    }
    
    try:
        async with session.get(WOOKIEEPEDIA_API, params=params) as response:
            data = await response.json()
            if "query" in data and "statistics" in data["query"]:
                return data["query"]["statistics"].get("articles", 0)
    except Exception as e:
        logger.error(f"Error getting total articles: {e}")
    
    return 0

async def get_legends_count(session: aiohttp.ClientSession) -> int:
    """Get count of Legends articles."""
    return await get_category_members(session, "Legends articles")

async def main():
    """Compare canon vs. total content on Wookieepedia."""
    async with aiohttp.ClientSession() as session:
        print("\nAnalyzing Wookieepedia Content Distribution...")
        print("This may take a few minutes due to rate limiting...")
        print("-" * 50)
        
        # Get total articles first
        total_articles = await get_total_articles(session)
        print(f"\nTotal Articles in Wookieepedia: {total_articles:,d}")
        
        # Get canon articles
        print("\nCounting Canon articles...")
        canon_count = await get_category_members(session, "Canon articles")
        
        # Get legends articles
        print("\nCounting Legends articles...")
        legends_count = await get_legends_count(session)
        
        # Calculate percentages
        canon_percent = (canon_count / total_articles) * 100 if total_articles > 0 else 0
        legends_percent = (legends_count / total_articles) * 100 if total_articles > 0 else 0
        other_count = total_articles - (canon_count + legends_count)
        other_percent = (other_count / total_articles) * 100 if total_articles > 0 else 0
        
        print("\nWookieepedia Content Distribution:")
        print("-" * 50)
        print(f"Total Articles: {total_articles:,d}")
        print(f"Canon Articles: {canon_count:,d} ({canon_percent:.1f}%)")
        print(f"Legends Articles: {legends_count:,d} ({legends_percent:.1f}%)")
        print(f"Other/Uncategorized: {other_count:,d} ({other_percent:.1f}%)")
        print("\nNote:")
        print("- Counts include only main namespace articles")
        print("- Some articles might be tagged as both Canon and Legends")
        print("- Other/Uncategorized includes meta articles, disambiguation pages, etc.")
        print("- Rate limiting was applied to respect API limits")

if __name__ == "__main__":
    asyncio.run(main()) 