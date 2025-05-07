"""
Category crawler for discovering Wookieepedia articles by category.
"""

import asyncio
import logging
from typing import List, Set, Dict, Optional
import urllib.parse
import aiohttp
from bs4 import BeautifulSoup
import time

logger = logging.getLogger(__name__)

class CategoryCrawler:
    """Crawls Wookieepedia categories to discover article URLs."""
    
    def __init__(self):
        self.base_url = "https://starwars.fandom.com"
        self.discovered_urls: Dict[str, Set[str]] = {}
        self.session: aiohttp.ClientSession = None
        
    async def __aenter__(self):
        """Set up async context."""
        logger.debug("Initializing CategoryCrawler session")
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up async context."""
        if self.session:
            logger.debug("Closing CategoryCrawler session")
            await self.session.close()
            
    async def fetch_category_page(self, url: str) -> str:
        """Fetch category page HTML content with exponential backoff and retries."""
        logger.debug(f"Fetching category page: {url}")
        max_retries = 5
        base_backoff = 1  # seconds
        max_backoff = 32 # seconds
        
        for attempt in range(max_retries):
            start_time = time.time()
            try:
                async with self.session.get(url, timeout=20) as response:
                    response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                    html = await response.text()
                    logger.debug(f"Fetched {len(html)} bytes in {time.time() - start_time:.2f}s from {url} (attempt {attempt + 1})")
                    return html
            except (aiohttp.ClientError, asyncio.TimeoutError) as e: # Catch relevant aiohttp errors and TimeoutError
                logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                if attempt == max_retries - 1:
                    logger.error(f"All {max_retries} retries failed for {url}. Last error: {e}")
                    # Depending on desired behavior, you might return "" or raise the exception
                    # For now, let's stick to returning "" as per original error handling, but after all retries.
                    if isinstance(e, asyncio.TimeoutError):
                         raise  # Re-raise timeout if it's the final error for this specific handling
                    return "" 
                
                # Exponential backoff calculation
                backoff_time = min(max_backoff, base_backoff * (2 ** attempt))
                # Add some jitter to avoid thundering herd problem
                jitter = backoff_time * 0.1 * (2 * time.time() % 1 - 0.5) # +/- 10% jitter
                actual_wait_time = backoff_time + jitter
                
                logger.info(f"Retrying {url} in {actual_wait_time:.2f} seconds...")
                await asyncio.sleep(actual_wait_time)
        
        logger.error(f"Exhausted all retries for {url}. Returning empty string.")
        return "" # Should ideally not be reached if errors are handled or re-raised properly above
            
    async def crawl_category(self, category_name: str, limit: int = 50) -> Set[str]:
        """
        Crawl a specific category to find article URLs.
        
        Args:
            category_name: Name of the category to crawl
            limit: Maximum number of articles to retrieve
            
        Returns:
            Set of article URLs in the category
        """
        logger.info(f"Crawling category: {category_name}")
        
        # Normalize category name
        if not category_name.startswith("Category:"):
            category_name = f"Category:{category_name}"
            
        # Construct the category URL
        # Replace spaces with underscores and then quote the category name for the URL path
        encoded_category_name = urllib.parse.quote(category_name.replace(" ", "_"))
        category_url = f"{self.base_url}/wiki/{encoded_category_name}"
        
        urls = set()
        next_page_url = category_url
        
        while next_page_url and len(urls) < limit:
            logger.debug(f"Processing category page: {next_page_url}")
            html = await self.fetch_category_page(next_page_url)
            if not html:
                break
                
            soup = BeautifulSoup(html, "html.parser")
            
            # Find all article links in the category
            article_links = soup.select("a.category-page__member-link")
            logger.debug(f"Found {len(article_links)} article links on page")
            
            for link in article_links:
                if len(urls) >= limit:
                    break
                    
                href = link.get("href")
                if href and href.startswith("/wiki/"):
                    article_url = self.base_url + href
                    
                    # Skip subcategories and templates
                    if "Category:" in article_url or "Template:" in article_url:
                        continue
                        
                    urls.add(article_url)
                    
            # Check if there's a next page
            next_button = soup.select_one("a.category-page__pagination-next")
            if next_button and next_button.get("href"): # Added check for href existence
                # Ensure the next page URL is also correctly formed
                next_page_path = next_button.get("href")
                if next_page_path.startswith(f"{self.base_url}/wiki/"):
                    next_page_url = next_page_path # Already a full URL
                elif next_page_path.startswith("/wiki/"):
                    next_page_url = self.base_url + next_page_path
                else: # Fallback for unexpected href, log and stop pagination
                    logger.warning(f"Unexpected next page href: {next_page_path} for category {category_name}")
                    next_page_url = None
            else:
                next_page_url = None
            
            # Rate limiting
            await asyncio.sleep(1)
            
        logger.info(f"Found {len(urls)} URLs in category {category_name}")
        return urls
        
    async def crawl(self, categories: Optional[List[str]] = None, limit_per_category: int = 50) -> Dict[str, Set[str]]:
        """
        Crawl multiple categories to discover article URLs.
        
        Args:
            categories: List of categories to crawl
            limit_per_category: Maximum articles per category
            
        Returns:
            Dictionary mapping category names to sets of article URLs
        """
        if categories is None:
            categories = [
                "Category:Characters",
                "Category:Locations",
                "Category:Droids",
                "Category:Vehicles",
                "Category:Weapons",
                "Category:Technology"
            ]
            
        logger.info(f"Crawling {len(categories)} categories")
        
        self.discovered_urls = {}
        
        for category in categories:
            try:
                logger.info(f"Starting crawl of category: {category}")
                category_urls = await asyncio.wait_for(
                    self.crawl_category(category, limit_per_category),
                    timeout=120  # 2 minute timeout per category
                )
                self.discovered_urls[category] = category_urls
                logger.info(f"Completed crawl of {category}: found {len(category_urls)} URLs")
            except asyncio.TimeoutError:
                logger.error(f"Timeout crawling category {category}")
                self.discovered_urls[category] = set()
            except Exception as e:
                logger.error(f"Error crawling category {category}: {e}")
                self.discovered_urls[category] = set()
                
        return self.discovered_urls 