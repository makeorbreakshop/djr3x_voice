"""
Sitemap crawler for discovering Wookieepedia articles.
"""

import asyncio
import logging
from typing import List, Set, Dict, Optional
import aiohttp
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import re
import urllib.parse
import time

logger = logging.getLogger(__name__)

class SitemapCrawler:
    """Crawls Wookieepedia sitemaps to discover article URLs."""
    
    def __init__(self):
        self.base_url = "https://starwars.fandom.com"
        self.discovered_urls: Set[str] = set()
        self.session: aiohttp.ClientSession = None
        
    async def __aenter__(self):
        """Set up async context."""
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up async context."""
        if self.session:
            await self.session.close()
            
    async def search_wiki(self, search_term: str, limit: int = 50) -> Set[str]:
        """
        Search Wookieepedia for articles matching the search term.
        Includes exponential backoff and retries for network requests.
        
        Args:
            search_term: Term to search for
            limit: Maximum number of results to return
            
        Returns:
            Set of article URLs matching the search term
        """
        max_retries = 3 # Slightly fewer retries for search as it can be heavier
        base_backoff = 2  # Start with a slightly higher backoff for search
        max_backoff = 32 # seconds

        urls = set()

        # 1. Try direct article URL
        # Spaces are replaced by underscores for wiki paths
        encoded_direct_term = urllib.parse.quote(search_term.replace(" ", "_"))
        direct_url = f"{self.base_url}/wiki/{encoded_direct_term}"
        
        logger.debug(f"Attempting direct URL lookup for '{search_term}': {direct_url}")
        for attempt in range(max_retries):
            try:
                async with self.session.head(direct_url, timeout=10, allow_redirects=True) as response:
                    if response.status == 200:
                        urls.add(str(response.url)) # Use the final URL after redirects
                        logger.info(f"Direct URL hit for '{search_term}': {response.url}")
                        break # Success, no need to retry this part
                    elif response.status == 404:
                        logger.debug(f"Direct URL for '{search_term}' not found (404): {direct_url}")
                        break # Not found, no need to retry 404s for HEAD
                    else:
                        response.raise_for_status() # Raise for other client/server errors
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Direct URL attempt {attempt + 1} for {direct_url} failed: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"All {max_retries} retries for direct URL {direct_url} failed.")
                    break # Move on to search after final retry fails
                
                backoff_time = min(max_backoff, base_backoff * (2 ** attempt))
                jitter = backoff_time * 0.1 * (2 * time.time() % 1 - 0.5)
                actual_wait_time = backoff_time + jitter
                logger.info(f"Retrying direct URL {direct_url} in {actual_wait_time:.2f}s...")
                await asyncio.sleep(actual_wait_time)
            else: # if try block succeeded without http error status needing retry
                if response.status == 200 or response.status == 404:
                    break

        # 2. If direct URL not found or didn't resolve, try search
        # For query parameters, spaces are typically %20 (done by urllib.parse.quote)
        encoded_search_query_term = urllib.parse.quote(search_term)
        search_url = f"{self.base_url}/wiki/Special:Search?query={encoded_search_query_term}&scope=internal&limit={limit*2}" # Request more to account for non-article links
        
        logger.debug(f"Attempting search for '{search_term}': {search_url}")
        if len(urls) == 0: # Only search if direct hit didn't yield a result
            for attempt in range(max_retries):
                try:
                    async with self.session.get(search_url, timeout=20) as response:
                        response.raise_for_status()
                        text = await response.text()
                        
                    soup = BeautifulSoup(text, "html.parser")
                    # Prioritize .unified-search__result__title, fallback to .mw-search-result-heading for broader compatibility
                    results = soup.select(".unified-search__result__title a, .mw-search-result-heading a")
                    
                    found_count = 0
                    for result_link in results:
                        href = result_link.get("href")
                        if href:
                            # Ensure it's a Wookieepedia article URL and not a sub-category or special page
                            if href.startswith("/wiki/") and not any(kw in href for kw in [":Category:", ":File:", ":Template:", ":Special:", ":Help:"]):
                                full_url = href if href.startswith(self.base_url) else self.base_url + href
                                urls.add(full_url.split("#")[0]) # Add URL without fragment
                                found_count +=1
                                if len(urls) >= limit: # Check against overall desired limit of unique URLs
                                    break
                    
                    logger.info(f"Found {found_count} potential articles from search for '{search_term}'. Total unique URLs for term: {len(urls)}")
                    return urls # Return after successful search processing
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"Search attempt {attempt + 1} for {search_url} failed: {e}")
                    if attempt == max_retries - 1:
                        logger.error(f"All {max_retries} retries for search URL {search_url} failed. Returning current URLs.")
                        return urls # Return whatever was found, even if empty
                    
                    backoff_time = min(max_backoff, base_backoff * (2 ** attempt))
                    jitter = backoff_time * 0.1 * (2 * time.time() % 1 - 0.5)
                    actual_wait_time = backoff_time + jitter
                    logger.info(f"Retrying search {search_url} in {actual_wait_time:.2f}s...")
                    await asyncio.sleep(actual_wait_time)
        
        # This return is if direct hit found something and search was skipped, or if search exhausted retries.
        logger.info(f"Final URL count for search term '{search_term}': {len(urls)}")
        return urls
            
    async def crawl(self, search_term: Optional[str] = None, limit: int = 0) -> Set[str]:
        """
        Search for articles matching the search term.
        
        Args:
            search_term: Optional search term to filter URLs
            limit: Maximum number of URLs to return (0 for no limit)
            
        Returns:
            Set of discovered article URLs
        """
        if search_term:
            urls = await self.search_wiki(search_term, limit=limit if limit > 0 else 50)
            self.discovered_urls.update(urls)
            
            # Apply limit if specified
            if limit > 0 and len(self.discovered_urls) >= limit:
                self.discovered_urls = set(list(self.discovered_urls)[:limit])
                
        # Log the results
        message = f"Found {len(self.discovered_urls)} URLs"
        if search_term:
            message += f" matching '{search_term}'"
        logger.info(message)
        
        return self.discovered_urls
        
    def get_metadata(self) -> Dict[str, int]:
        """Get metadata about the crawl results."""
        return {
            "total_urls": len(self.discovered_urls),
            "wiki_urls": len([url for url in self.discovered_urls if "/wiki/" in url])
        } 