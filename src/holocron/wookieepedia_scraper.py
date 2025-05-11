"""
Wookieepedia Scraper for the Holocron Knowledge System

This module provides functionality to scrape canonical Star Wars content 
from Wookieepedia for use in the Holocron knowledge base.
"""

import os
import re
import json
import time
import logging
import asyncio
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, quote, unquote

import aiohttp
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
WOOKIEEPEDIA_URL = "https://starwars.fandom.com"
CANONICAL_CONTENT_CATEGORIES = [
    "Category:Canon_articles",
    "Category:Films",
    "Category:Television_shows",
    "Category:Characters",
    "Category:Locations",
    "Category:Technology",
    "Category:Vehicles",
    "Category:Weapons",
    "Category:Organizations"
]
USER_AGENT = "Mozilla/5.0 (compatible; DJ-R3X-Holocron/1.0; +https://github.com/yourusername/dj-r3x-voice)"
RATE_LIMIT = 1.0  # Seconds between requests to respect the site's servers

class WookieepediaScraper:
    """
    Scraper for extracting canonical Star Wars content from Wookieepedia.
    
    This class provides functionality to scrape articles from Wookieepedia's
    canonical content pages, extracting text content while filtering out
    non-canonical (Legends) material.
    """
    
    def __init__(self, rate_limit: float = RATE_LIMIT):
        """
        Initialize the Wookieepedia scraper.
        
        Args:
            rate_limit: Seconds to wait between requests (default: 1.0)
        """
        self.rate_limit = rate_limit
        self.session = None
        self.visited_urls: Set[str] = set()
        
    async def __aenter__(self):
        """Set up async context manager."""
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up async context manager."""
        if self.session:
            await self.session.close()
            
    def _encode_wiki_url(self, url: str) -> str:
        """
        Properly encode a Wookieepedia URL, handling special characters.
        
        Args:
            url: The URL to encode
            
        Returns:
            Properly encoded URL
        """
        # Parse the URL into components
        parsed = urlparse(url)
        
        # Split the path to isolate the wiki article name
        path_parts = parsed.path.split('/wiki/', 1)
        if len(path_parts) != 2:
            return url
            
        # The article name is after /wiki/
        article_name = path_parts[1]
        
        # SPECIAL CASE 1: Fix corrupted characters after % symbol using regex pattern
        # This handles Unicode replacement character (U+FFFD) that appears after the % was corrupted
        if '\ufffd' in article_name:
            logger.warning(f"Detected URL with Unicode replacement character: {url}")
            # Pattern: the replacement character followed by any character is actually "%+that character"
            article_name = re.sub(r'\ufffd([a-zA-Z0-9_])', r'%\1', article_name)
            logger.info(f"Attempting to fix corrupted URL using regex pattern: {article_name}")
        
        # SPECIAL CASE 2: Fix hex-encoded Unicode replacement character pattern
        if 'EF%BF%BD' in article_name:
            logger.warning(f"Detected URL with hex-encoded replacement character: {url}")
            # Match the full hex-encoded replacement character pattern followed by any letter/number
            article_name = re.sub(r'%EF%BF%BD([a-zA-Z0-9_])', r'%\1', article_name)
            logger.info(f"Attempting to fix corrupted URL using hex-encoded pattern: {article_name}")
        
        # First decode any existing encoding to prevent double-encoding
        article_name = unquote(article_name)
        
        # Replace % with %25 to prevent it from being treated as an encoding marker
        article_name = re.sub(r'%', '%25', article_name)
        
        # Re-encode the article name properly, preserving certain characters
        encoded_name = quote(article_name, safe='')
        
        # Reconstruct the URL
        new_path = f"/wiki/{encoded_name}"
        encoded_url = parsed._replace(path=new_path).geturl()
        
        return encoded_url

    async def _make_request(self, url: str) -> str:
        """
        Make an HTTP request with rate limiting.
        
        Args:
            url: The URL to request
            
        Returns:
            The HTML content of the response
        """
        if not self.session:
            self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
            
        # Rate limiting
        await asyncio.sleep(self.rate_limit)
        
        try:
            # Properly encode the URL before making the request
            encoded_url = self._encode_wiki_url(url)
            async with self.session.get(encoded_url, timeout=30) as response:
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return ""
            
    def _is_canonical_article(self, soup: BeautifulSoup) -> bool:
        """
        Check if an article contains canonical content.
        
        Args:
            soup: BeautifulSoup object of the article page
            
        Returns:
            True if the article is canonical, False otherwise
        """
        # Check for "Legends" banner which indicates non-canonical content
        legends_banner = soup.select(".banner-notification--legends")
        if legends_banner:
            return False
            
        # Check if it explicitly mentions being part of Star Wars Canon
        canon_mentions = soup.find_all(string=re.compile(r'Star Wars Canon|Canon Timeline|Disney Canon|Official Canon', re.IGNORECASE))
        if canon_mentions:
            return True
            
        # Check article categories for Canon tag
        categories = soup.select('div.page-header__categories a')
        for category in categories:
            if 'Canon' in category.text and 'Legends' not in category.text:
                return True
                
        # Default to true for now, we'll filter more strictly if needed
        return True
    
    def _extract_content(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Extract clean content from a Wookieepedia article.
        
        Args:
            soup: BeautifulSoup object of the article page
            url: The URL of the article
            
        Returns:
            Dictionary with article title, content and metadata
        """
        # Get the article title
        title_element = soup.select_one("h1.page-header__title")
        title = title_element.text.strip() if title_element else "Unknown Article"
        
        # Get the article content
        content_element = soup.select_one("div.mw-parser-output")
        if not content_element:
            return {
                "title": title,
                "url": url,
                "content": "",
                "sections": [],
                "is_canonical": False
            }
            
        # Remove non-content elements
        for element in content_element.select(".toc, .wikia-gallery, .thumb, .notice, .mw-empty-elt, .navbox"):
            element.decompose()
            
        # Remove citation references
        for citation in content_element.select("sup.reference"):
            citation.decompose()
            
        # Extract sections with headings
        sections = []
        current_section = {"heading": "Introduction", "content": ""}
        
        # Process each child element in the content
        for element in content_element.children:
            # Check if it's a heading
            if element.name in ["h2", "h3", "h4"]:
                # Save the previous section if it has content
                if current_section["content"].strip():
                    sections.append(current_section.copy())
                
                # Start a new section
                heading_text = element.get_text().strip()
                if "References" in heading_text or "Sources" in heading_text or "Notes" in heading_text:
                    break  # Stop processing at references section
                
                current_section = {"heading": heading_text, "content": ""}
            elif element.name == "p" or element.name == "ul" or element.name == "ol":
                # Add paragraph or list content to current section
                text = element.get_text().strip()
                if text:
                    current_section["content"] += text + "\n\n"
                    
        # Add the last section if it has content
        if current_section["content"].strip():
            sections.append(current_section)
            
        # Extract categories for metadata
        categories = []
        for category_link in soup.select("div.page-header__categories a"):
            category = category_link.text.strip()
            if category and category != "Categories":
                categories.append(category)
                
        # Combine all content for full-text
        full_content = "\n\n".join([f"## {s['heading']}\n{s['content']}" for s in sections])
        
        return {
            "title": title,
            "url": url,
            "content": full_content,
            "sections": sections,
            "categories": categories,
            "is_canonical": True  # We've already filtered non-canonical content
        }
        
    async def scrape_article(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Scrape a single Wookieepedia article.
        
        Args:
            url: The URL of the article to scrape
            
        Returns:
            Dictionary containing the article data, or None if not canonical
        """
        # Normalize URL and check if already visited
        url = url.split("#")[0]  # Remove anchor
        if url in self.visited_urls:
            return None
            
        self.visited_urls.add(url)
        
        # Fetch the article
        html = await self._make_request(url)
        if not html:
            return None
            
        soup = BeautifulSoup(html, "html.parser")
        
        # Check if this is a canonical article
        if not self._is_canonical_article(soup):
            logger.debug(f"Skipping non-canonical article: {url}")
            return None
            
        # Extract the content
        article_data = self._extract_content(soup, url)
        if not article_data["content"]:
            return None
            
        logger.info(f"Scraped article: {article_data['title']}")
        return article_data
        
    async def get_category_articles(self, category_name: str, limit: int = 100) -> List[str]:
        """
        Get article URLs from a Wookieepedia category.
        
        Args:
            category_name: The name of the category to scrape
            limit: Maximum number of articles to retrieve
            
        Returns:
            List of article URLs
        """
        if not category_name.startswith("Category:"):
            category_name = f"Category:{category_name}"
            
        url = f"{WOOKIEEPEDIA_URL}/wiki/{category_name}"
        urls = []
        
        try:
            html = await self._make_request(url)
            if not html:
                return urls
                
            soup = BeautifulSoup(html, "html.parser")
            
            # Get all article links in the category
            for link in soup.select("div.category-page__members a"):
                article_url = urljoin(WOOKIEEPEDIA_URL, link.get("href", ""))
                if article_url and "/wiki/" in article_url and ":" not in article_url.split("/")[-1]:
                    urls.append(article_url)
                    if len(urls) >= limit:
                        break
                        
            logger.info(f"Found {len(urls)} articles in category {category_name}")
            return urls
        except Exception as e:
            logger.error(f"Error getting articles from category {category_name}: {e}")
            return urls

    async def get_article_urls(self, categories: List[str] = CANONICAL_CONTENT_CATEGORIES, 
                             limit_per_category: int = 20) -> List[str]:
        """
        Get article URLs from multiple categories.
        
        Args:
            categories: List of category names to scrape
            limit_per_category: Maximum number of articles per category
            
        Returns:
            List of article URLs
        """
        all_urls = set()  # Use set to avoid duplicates
        
        for category in categories:
            try:
                urls = await self.get_category_articles(category, limit_per_category)
                all_urls.update(urls)
                logger.info(f"Added {len(urls)} URLs from category {category}")
            except Exception as e:
                logger.error(f"Error processing category {category}: {e}")
                continue
                
        # Convert set to list for JSON serialization
        return list(all_urls)

    async def scrape_multiple_categories(self, 
                                       categories: List[str] = CANONICAL_CONTENT_CATEGORIES, 
                                       limit_per_category: int = 20) -> List[Dict[str, Any]]:
        """
        Scrape articles from multiple categories.
        
        Args:
            categories: List of category names to scrape
            limit_per_category: Maximum number of articles per category
            
        Returns:
            List of dictionaries containing article data
        """
        # Get URLs first
        urls = await self.get_article_urls(categories, limit_per_category)
        
        # Scrape articles
        articles = []
        for url in urls:
            try:
                article = await self.scrape_article(url)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.error(f"Error scraping article {url}: {e}")
                continue
                
        return articles
        
    def save_to_json(self, articles: List[Dict[str, Any]], output_file: str):
        """
        Save scraped articles to a JSON file.
        
        Args:
            articles: List of article dictionaries
            output_file: Path to save the JSON file
        """
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Saved {len(articles)} articles to {output_file}")

async def main():
    """Run the scraper as a standalone script."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Scrape canonical content from Wookieepedia")
    parser.add_argument('--output', type=str, default="data/wookieepedia_articles.json",
                      help="Path to save the output JSON file")
    parser.add_argument('--limit', type=int, default=20,
                      help="Maximum articles per category to scrape")
    args = parser.parse_args()
    
    async with WookieepediaScraper() as scraper:
        articles = await scraper.scrape_multiple_categories(limit_per_category=args.limit)
        scraper.save_to_json(articles, args.output)
        
if __name__ == "__main__":
    asyncio.run(main()) 