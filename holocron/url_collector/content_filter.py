"""
Content filter for classifying Wookieepedia articles.
"""

import logging
import re
from typing import Dict, List, Set, Tuple
import aiohttp
from bs4 import BeautifulSoup
import asyncio

logger = logging.getLogger(__name__)

class ContentFilter:
    """Filters and classifies Wookieepedia articles."""
    
    def __init__(self):
        self.session: aiohttp.ClientSession = None
        
        # Indicators of Legends/non-canonical content
        self.legends_indicators = [
            'This article is about a subject that was part of Star Wars Legends',
            'Star Wars Legends',
            'Legends article',
            'Category:Legends',
            'Legends continuity',
            'This article contains information from the Expanded Universe'
        ]
        
        # Indicators of canonical content
        self.canon_indicators = [
            'Canon article',
            'Category:Canon',
            'This article is about a canonical subject'
        ]
        
    async def __aenter__(self):
        """Set up async context."""
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up async context."""
        if self.session:
            await self.session.close()
            
    async def fetch_article(self, url: str) -> str:
        """Fetch article HTML content."""
        try:
            async with self.session.get(url) as response:
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            logger.error(f"Error fetching article {url}: {e}")
            return ""
            
    def analyze_content(self, content: str) -> Tuple[str, Dict[str, any]]:
        """Analyze article content to determine canonicity and extract metadata."""
        if not content:
            return 'unknown', {}
            
        soup = BeautifulSoup(content, 'lxml')
        
        # Initialize metadata
        metadata = {
            'title': '',
            'categories': [],
            'indicators_found': [],
            'confidence': 0.0
        }
        
        # Extract title
        title_elem = soup.select_one('h1.page-header__title')
        if title_elem:
            metadata['title'] = title_elem.text.strip()
            
        # Extract categories
        categories = soup.select('div.page-header__categories a')
        metadata['categories'] = [cat.text.strip() for cat in categories]
        
        # Look for canonicity indicators
        content_text = soup.get_text()
        legends_matches = [ind for ind in self.legends_indicators 
                         if ind.lower() in content_text.lower()]
        canon_matches = [ind for ind in self.canon_indicators 
                        if ind.lower() in content_text.lower()]
        
        metadata['indicators_found'] = {
            'legends': legends_matches,
            'canon': canon_matches
        }
        
        # Determine content type and confidence
        if legends_matches and not canon_matches:
            content_type = 'legends'
            metadata['confidence'] = 0.9 if len(legends_matches) > 1 else 0.7
        elif canon_matches and not legends_matches:
            content_type = 'canonical'
            metadata['confidence'] = 0.9 if len(canon_matches) > 1 else 0.7
        elif not legends_matches and not canon_matches:
            # Default newer articles to canonical unless proven otherwise
            content_type = 'canonical'
            metadata['confidence'] = 0.5
        else:
            # Mixed signals - need manual review
            content_type = 'unknown'
            metadata['confidence'] = 0.3
            
        return content_type, metadata
        
    async def classify_url(self, url: str) -> str:
        """
        Classify a single URL as canonical or legends.
        
        Args:
            url: URL to classify
            
        Returns:
            'canonical', 'legends', or 'unknown'
        """
        html = await self.fetch_article(url)
        if not html:
            return 'unknown'
            
        soup = BeautifulSoup(html, "html.parser")
        
        # Check for Legends banner
        legends_banner = soup.find("div", class_="legends-notice")
        if legends_banner:
            return 'legends'
            
        # Check for Canon/Legends tabs
        tabs = soup.find("div", class_="tabber wds-tabber")
        if tabs:
            # If there are tabs, check which one is active
            active_tab = tabs.find("div", class_="wds-tab__content wds-is-current")
            if active_tab:
                tab_name = active_tab.get("data-tab-name", "").lower()
                if "canon" in tab_name:
                    return 'canonical'
                elif "legends" in tab_name:
                    return 'legends'
                    
        # Check article categories
        categories = soup.find_all("div", class_="page-header__categories")
        for category in categories:
            if "Category:Canon articles" in category.text:
                return 'canonical'
            elif "Category:Legends articles" in category.text:
                return 'legends'
                
        # Default to unknown if we can't determine
        return 'unknown'
        
    async def classify_urls(self, urls: Set[str]) -> Dict[str, str]:
        """
        Classify multiple URLs as canonical or legends.
        
        Args:
            urls: Set of URLs to classify
            
        Returns:
            Dictionary mapping URLs to their content type
        """
        async with aiohttp.ClientSession() as self.session:
            results = {}
            for url in urls:
                content_type = await self.classify_url(url)
                results[url] = content_type
                
            return results
        
    def estimate_canonicity(self, url: str) -> str:
        """Estimate content type from URL structure without fetching."""
        url_lower = url.lower()
        
        # Quick checks based on URL patterns
        if '/legends/' in url_lower:
            return 'legends'
        elif '/canon/' in url_lower:
            return 'canonical'
        
        # Default to unknown for proper analysis
        return 'unknown'
        
    async def analyze_url_batch(self, urls: List[str]) -> Dict[str, Dict]:
        """Analyze a batch of URLs and return detailed metadata."""
        results = {}
        
        for url in urls:
            # Start with URL-based estimate
            initial_type = self.estimate_canonicity(url)
            
            if initial_type == 'unknown':
                # Need to fetch and analyze content
                content = await self.fetch_article(url)
                content_type, metadata = self.analyze_content(content)
            else:
                content_type = initial_type
                metadata = {'confidence': 0.8, 'source': 'url_pattern'}
                
            results[url] = {
                'content_type': content_type,
                'metadata': metadata
            }
            
        return results 