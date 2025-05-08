"""
Content filter for classifying Wookieepedia articles.
"""

import logging
import re
from typing import Dict, List, Set, Tuple
import aiohttp
from bs4 import BeautifulSoup
import asyncio
from urllib.parse import unquote

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
            'This article contains information from the Expanded Universe',
            'Category:Legends articles'
        ]
        
        # Indicators of canonical content
        self.canon_indicators = [
            'Canon article',
            'Category:Canon',
            'Category:Canon articles',
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
            headers = {
                'User-Agent': 'Mozilla/5.0 DJ R3X Holocron Knowledge Base',
                'Accept': 'text/html,application/xhtml+xml,application/xml'
            }
            async with self.session.get(url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            logger.error(f"Error fetching article {url}: {e}")
            return ""
            
    def analyze_content(self, content: str) -> Tuple[str, Dict[str, any]]:
        """Analyze article content to determine canonicity and extract metadata."""
        if not content:
            return 'unknown', {'confidence': 0.0, 'reason': 'empty_content'}
            
        soup = BeautifulSoup(content, 'html.parser')
        
        # Check for Legends banner
        legends_banner = soup.find('div', class_='legends-notice')
        if legends_banner:
            return 'legends', {'confidence': 1.0, 'reason': 'legends_banner'}
            
        # Check article text for indicators
        article_text = soup.get_text().lower()
        
        # Count matches for each type
        legends_matches = sum(1 for indicator in self.legends_indicators 
                            if indicator.lower() in article_text)
        canon_matches = sum(1 for indicator in self.canon_indicators 
                          if indicator.lower() in article_text)
        
        # Check categories
        categories = [cat.get_text() for cat in soup.find_all('div', class_='page-header__categories')]
        category_text = ' '.join(categories).lower()
        
        if 'category:legends articles' in category_text:
            return 'legends', {'confidence': 1.0, 'reason': 'legends_category'}
        elif 'category:canon articles' in category_text:
            return 'canonical', {'confidence': 1.0, 'reason': 'canon_category'}
            
        # Make decision based on indicator matches
        if legends_matches > canon_matches:
            return 'legends', {
                'confidence': min(1.0, legends_matches / len(self.legends_indicators)),
                'reason': 'legends_indicators'
            }
        elif canon_matches > legends_matches:
            return 'canonical', {
                'confidence': min(1.0, canon_matches / len(self.canon_indicators)),
                'reason': 'canon_indicators'
            }
        elif legends_matches == 0 and canon_matches == 0:
            # If no clear indicators, check URL structure
            if '/legends/' in soup.find('link', rel='canonical').get('href', '').lower():
                return 'legends', {'confidence': 0.8, 'reason': 'url_structure'}
            elif '/canon/' in soup.find('link', rel='canonical').get('href', '').lower():
                return 'canonical', {'confidence': 0.8, 'reason': 'url_structure'}
                
        return 'unknown', {'confidence': 0.0, 'reason': 'no_clear_indicators'}
        
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
            
        content_type, metadata = self.analyze_content(html)
        logger.debug(f"Classified {url} as {content_type} ({metadata})")
        return content_type
        
    async def classify_urls(self, urls: Set[str]) -> Dict[str, str]:
        """
        Classify multiple URLs as canonical or legends.
        
        Args:
            urls: Set of URLs to classify
            
        Returns:
            Dictionary mapping URLs to their content type
        """
        async with aiohttp.ClientSession() as self.session:
            tasks = []
            for url in urls:
                task = asyncio.create_task(self.classify_url(url))
                tasks.append((url, task))
                
            results = {}
            for url, task in tasks:
                try:
                    content_type = await task
                    results[url] = content_type
                except Exception as e:
                    logger.error(f"Error classifying {url}: {e}")
                    results[url] = 'unknown'
                    
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
        
        async with aiohttp.ClientSession() as self.session:
            tasks = []
            for url in urls:
                # Start with URL-based estimate
                initial_type = self.estimate_canonicity(url)
                
                if initial_type == 'unknown':
                    # Need to fetch and analyze content
                    task = asyncio.create_task(self.fetch_article(url))
                    tasks.append((url, task))
                else:
                    results[url] = {
                        'content_type': initial_type,
                        'metadata': {'confidence': 0.8, 'source': 'url_pattern'}
                    }
                    
            # Wait for all fetch tasks to complete
            for url, task in tasks:
                try:
                    content = await task
                    content_type, metadata = self.analyze_content(content)
                    results[url] = {
                        'content_type': content_type,
                        'metadata': metadata
                    }
                except Exception as e:
                    logger.error(f"Error analyzing {url}: {e}")
                    results[url] = {
                        'content_type': 'unknown',
                        'metadata': {'confidence': 0.0, 'reason': str(e)}
                    }
                    
            return results 