#!/usr/bin/env python3
"""
Phase 1 URL Collection Script for DJ R3X Holocron

This script collects URLs for Phase 1 of the Holocron knowledge system using the MediaWiki API.
- R3X/RX-24 specific information
- Oga's Cantina and direct workplace  
- DJ/entertainment roles in Star Wars

It uses the existing URL collection system with targeted parameters
to find the most relevant content for Phase 1.

Usage:
    python scripts/collect_phase1_urls.py           # Full collection with database storage
    python scripts/collect_phase1_urls.py --report-only  # Just generate a report
    python scripts/collect_phase1_urls.py --test    # Test mode without database access
"""

import os
import sys
import asyncio
import logging
import argparse
from typing import List, Dict, Set, Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
import aiohttp
from urllib.parse import quote, unquote

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the URL collection components
from holocron.url_collector.url_store import URLStore
from holocron.url_collector.content_filter import ContentFilter
from holocron.url_collector.reporting import CoverageReporter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/phase1_collection.log")
    ]
)
logger = logging.getLogger(__name__)
console = Console()

# Phase 1 search terms for sitemap crawler
RX24_SEARCH_TERMS = [
    "RX-24", "R3X", "Star Tours", "Captain Rex", "Oga's Cantina droid",
    "DJ R3X", "Rex (droid)"
]

OGAS_CANTINA_SEARCH_TERMS = [
    "Oga's Cantina", "Oga Garra", "Black Spire Outpost", "Batuu",
    "Galaxy's Edge cantina", "Star Wars cantina", "Black Spire"
]

DJ_ENTERTAINMENT_SEARCH_TERMS = [
    "Star Wars music", "cantina music", "Modal Nodes", "Figrin D'an",
    "Max Rebo", "music droid", "entertainment droid", "DJ droid",
    "protocol droid entertainment"
]

# Phase 1 categories for category crawler
RX24_CATEGORIES = [
    "Category:Droids", "Category:Star Tours characters",
    "Category:Pilot droids", "Category:Droid models"
]

OGAS_CANTINA_CATEGORIES = [
    "Category:Cantinas", "Category:Batuu", "Category:Galaxy's Edge",
    "Category:Restaurants", "Category:Black Spire Outpost"
]

DJ_ENTERTAINMENT_CATEGORIES = [
    "Category:Musicians", "Category:Musical groups", "Category:Songs",
    "Category:Entertainment", "Category:Performers"
]

class WikiApiCrawler:
    """MediaWiki API crawler for Wookieepedia."""
    
    def __init__(self):
        self.base_url = "https://starwars.fandom.com/api.php"
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def search_wiki(self, search_term: str, limit: int = 20) -> Set[str]:
        """Search Wookieepedia using the MediaWiki API."""
        urls = set()
        params = {
            'action': 'query',
            'list': 'search',
            'srsearch': search_term,
            'format': 'json',
            'srlimit': limit
        }
        
        try:
            async with self.session.get(self.base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'query' in data and 'search' in data['query']:
                        for result in data['query']['search']:
                            title = result['title']
                            # Use safe URL encoding that preserves certain characters
                            url_title = self._encode_wiki_title(title)
                            urls.add(f"https://starwars.fandom.com/wiki/{url_title}")
                else:
                    logger.warning(f"Search request failed with status {response.status}")
        except Exception as e:
            logger.error(f"Error during search: {e}")
        
        return urls
    
    async def crawl_category(self, category: str) -> Set[str]:
        """Get pages in a category using the MediaWiki API with continuation."""
        urls = set()
        if not category.startswith("Category:"):
            category = f"Category:{category}"
            
        params = {
            'action': 'query',
            'list': 'categorymembers',
            'cmtitle': category,
            'format': 'json',
            'cmlimit': 500
        }
        
        continue_params = {}
        
        try:
            while True:
                request_params = {**params, **continue_params}
                async with self.session.get(self.base_url, params=request_params) as response:
                    if response.status != 200:
                        break
                        
                    data = await response.json()
                    
                    if 'query' in data and 'categorymembers' in data['query']:
                        for member in data['query']['categorymembers']:
                            if member['ns'] == 0:  # Articles only
                                title = member['title']
                                url_title = self._encode_wiki_title(title)
                                urls.add(f"https://starwars.fandom.com/wiki/{url_title}")
                    
                    if 'continue' not in data:
                        break
                        
                    continue_params = data['continue']
        except Exception as e:
            logger.error(f"Error during category crawl: {e}")
        
        return urls
    
    @staticmethod
    def _encode_wiki_title(title: str) -> str:
        """
        Encode a wiki title for use in a URL.
        
        This handles special characters like &, ?, and others that are
        problematic in URL construction.
        """
        # First replace spaces with underscores per MediaWiki convention
        title = title.replace(' ', '_')
        
        # Properly encode the title while preserving certain characters
        # We'll manually handle some problematic characters first
        title = title.replace('&', '%26')
        title = title.replace('?', '%3F')
        title = title.replace("'", '%27')
        title = title.replace('"', '%22')
        
        # Then use urllib.parse.quote for general encoding, excluding already encoded parts
        # This approach ensures we don't double-encode characters
        segments = []
        for segment in title.split('%'):
            if segment:
                if len(segment) > 2 and all(c in '0123456789ABCDEFabcdef' for c in segment[:2]):
                    # This looks like it's already percent-encoded
                    segments.append(f'%{segment}')
                else:
                    # This needs encoding
                    segments.append(quote(segment, safe='/_-:'))
            elif segments:  # Empty segment after a % means we had a % at the end
                segments[-1] = segments[-1] + '%'
        
        return ''.join(segments)

class ModifiedContentFilter(ContentFilter):
    """
    Enhanced version of ContentFilter that handles special characters better.
    """
    
    async def classify_urls(self, urls: Set[str]) -> Dict[str, str]:
        """Classify URLs by content type (canonical or legends)."""
        content_types = {}
        
        # Create batches of URLs to process (10 at a time)
        url_list = list(urls)
        batch_size = 10
        batches = [url_list[i:i + batch_size] for i in range(0, len(url_list), batch_size)]
        
        logger.info(f"Classifying {len(url_list)} URLs in {len(batches)} batches")
        
        for batch in batches:
            batch_tasks = []
            
            for url in batch:
                # Decode the URL for better error messages
                decoded_url = unquote(url)
                task = asyncio.create_task(self._classify_single_url(url, decoded_url))
                batch_tasks.append(task)
            
            # Process batch concurrently
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            # Process results
            for url, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Error classifying URL {url}: {result}")
                    content_types[url] = 'unknown'
                else:
                    content_types[url] = result
            
            # Small delay to avoid overwhelming the server
            await asyncio.sleep(0.5)
        
        logger.info(f"Classification complete: {sum(1 for t in content_types.values() if t == 'canonical')} canonical, "
                    f"{sum(1 for t in content_types.values() if t == 'legends')} legends, "
                    f"{sum(1 for t in content_types.values() if t == 'unknown')} unknown")
        
        return content_types
    
    async def _classify_single_url(self, url: str, decoded_url: str) -> str:
        """
        Classify a single URL, handling special characters better.
        
        Args:
            url: The encoded URL to check
            decoded_url: Decoded version for better error messages
            
        Returns:
            'canonical', 'legends', or 'unknown' content type
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Add a longer timeout and proper headers
                headers = {
                    'User-Agent': 'Mozilla/5.0 DJ R3X Holocron Knowledge Base',
                    'Accept': 'text/html,application/xhtml+xml,application/xml',
                }
                
                async with session.get(url, headers=headers, timeout=20) as response:
                    if response.status != 200:
                        logger.warning(f"Failed to fetch {decoded_url} with status {response.status}")
                        return 'unknown'
                    
                    html = await response.text()
                    
                    # Check for legends banner/category
                    if 'Star Wars Legends' in html or 'Category:Legends articles' in html:
                        return 'legends'
                    
                    # Check for canonical banner/category (absence of legends is a good indicator)
                    return 'canonical'
                    
        except asyncio.TimeoutError:
            logger.warning(f"Timeout while fetching {decoded_url}")
            return 'unknown'
        except Exception as e:
            logger.error(f"Error fetching article {decoded_url}: {str(e)}")
            return 'unknown'

async def test_url_collection():
    """Run a test of URL collection without database interaction."""
    console.print("[bold green]Running URL collection test mode[/bold green]")
    console.print("This will test the MediaWiki API functionality without database access.")
    
    # Test collecting urls for one category
    console.print("\n[bold]Testing search term collection for R3X/RX-24:[/bold]")
    search_terms = RX24_SEARCH_TERMS[:2]  # Just use a couple of terms for testing
    
    console.print("[yellow]Note: The first connection might take a moment...[/yellow]")
    
    try:
        async with WikiApiCrawler() as crawler:
            for term in search_terms:
                console.print(f"Searching for term: [cyan]{term}[/cyan]")
                try:
                    console.print("  Sending request... (waiting max 10 seconds)")
                    urls = await asyncio.wait_for(crawler.search_wiki(term, limit=3), timeout=10)
                    if urls:
                        console.print(f"  [green]✓[/green] Found {len(urls)} URLs")
                        for url in list(urls)[:3]:  # Show only first 3
                            console.print(f"    - {url}")
                    else:
                        console.print("  [yellow]⚠[/yellow] No URLs found")
                except asyncio.TimeoutError:
                    console.print("  [red]✗[/red] Search timed out after 10 seconds")
                except Exception as e:
                    console.print(f"  [red]✗[/red] Error: {e}")
        
        # Test category crawling
        console.print("\n[bold]Testing category crawling for Oga's Cantina:[/bold]")
        categories = OGAS_CANTINA_CATEGORIES[:1]  # Just use one category for testing
        
        async with WikiApiCrawler() as crawler:
            for category in categories:
                console.print(f"Crawling category: [cyan]{category}[/cyan]")
                console.print("  This might take a bit longer...")
                try:
                    urls = await asyncio.wait_for(crawler.crawl_category(category), timeout=30)
                    if urls:
                        console.print(f"  [green]✓[/green] Found {len(urls)} URLs")
                        for url in list(urls)[:3]:  # Show only first 3
                            console.print(f"    - {url}")
                    else:
                        console.print("  [yellow]⚠[/yellow] No URLs found")
                except asyncio.TimeoutError:
                    console.print("  [red]✗[/red] Category crawl timed out after 30 seconds")
                except Exception as e:
                    console.print(f"  [red]✗[/red] Error: {e}")
                    
        # Test content filter with potentially problematic URLs
        console.print("\n[bold]Testing content filter with special URLs:[/bold]")
        test_urls = [
            "https://starwars.fandom.com/wiki/R-3X",  # Basic URL
            "https://starwars.fandom.com/wiki/Myths_%26_Fables",  # URL with &
            "https://starwars.fandom.com/wiki/%22Arc_of_Fire%22_Bar_%26_Grill"  # URL with quotes and &
        ]
        
        # Don't actually hit the server in test mode
        console.print("  Verifying URL encoding:")
        for url in test_urls:
            console.print(f"    Original: {url}")
            decoded = unquote(url)
            console.print(f"    Decoded: {decoded}")
            
            # Re-encode with our method
            title = url.split("/wiki/")[1]
            crawler = WikiApiCrawler()
            reencode = crawler._encode_wiki_title(unquote(title))
            console.print(f"    Re-encoded: {reencode}")
            console.print("")
            
        # Test database interface (simulated, no actual connection)
        console.print("\n[bold]Testing database interface:[/bold]")
        console.print("  [yellow]⚠[/yellow] In test mode, no actual database connections are made")
        console.print("  [green]✓[/green] URLStore class available for data storage")
        console.print("  [green]✓[/green] ContentFilter class enhanced for special character handling")
        
    except Exception as e:
        console.print(f"[bold red]Error during testing: {e}[/bold red]")
    
    console.print("\n[bold green]Test completed![/bold green]")
    console.print("You can now run the full collection with database access once Supabase credentials are set.")

async def collect_urls_by_search_terms(search_terms: List[str], priority: str, tag: str) -> Set[str]:
    """Collect URLs using MediaWiki API search with specific search terms."""
    console.print(f"🔍 Searching for terms related to {tag}...")
    all_urls = set()
    
    async with WikiApiCrawler() as crawler:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task(f"Searching for {tag} content...", total=len(search_terms))
            
            for term in search_terms:
                urls = await crawler.search_wiki(term, limit=20)
                all_urls.update(urls)
                progress.update(task, advance=1)
                
                # Add a small delay between requests
                await asyncio.sleep(1)
    
    console.print(f"📝 Found {len(all_urls)} URLs related to {tag}")
    return all_urls

async def collect_urls_by_categories(categories: List[str], priority: str, tag: str) -> Set[str]:
    """Collect URLs from specific categories using MediaWiki API."""
    console.print(f"📑 Collecting from categories related to {tag}...")
    all_urls = set()
    
    async with WikiApiCrawler() as crawler:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task(f"Crawling categories for {tag}...", total=len(categories))
            
            for category in categories:
                urls = await crawler.crawl_category(category)
                all_urls.update(urls)
                progress.update(task, advance=1)
                
                # Add a small delay between requests
                await asyncio.sleep(1)
    
    console.print(f"📝 Found {len(all_urls)} URLs from {tag} categories")
    return all_urls

async def filter_and_store_urls(urls: Set[str], priority: str, tag: str) -> Dict[str, int]:
    """Filter and store URLs with content type detection."""
    store = URLStore()
    content_filter = ModifiedContentFilter()  # Use the enhanced content filter
    
    # Filter URLs by content type
    console.print(f"🔍 Classifying {len(urls)} URLs for {tag}...")
    content_types = await content_filter.classify_urls(urls)
    
    # Group URLs by content type
    canonical_urls = {url for url, ctype in content_types.items() if ctype == 'canonical'}
    legends_urls = {url for url, ctype in content_types.items() if ctype == 'legends'}
    unknown_urls = {url for url, ctype in content_types.items() if ctype == 'unknown'}
    
    console.print(f"✅ Found {len(canonical_urls)} canonical articles for {tag}")
    console.print(f"📚 Found {len(legends_urls)} legends articles for {tag}")
    console.print(f"❓ Found {len(unknown_urls)} unknown articles for {tag}")
    
    results = {
        'canonical': 0,
        'legends': 0,
        'unknown': 0
    }
    
    # Store canonical URLs with specified priority
    if canonical_urls:
        try:
            result = await store.store_urls(
                urls=list(canonical_urls),
                priority=priority,
                category=tag
            )
            console.print(f"📥 Stored {result['inserted']} new canonical URLs")
            results['canonical'] = result['inserted']
        except Exception as e:
            logger.error(f"Error storing canonical URLs: {e}")
            console.print(f"[red]Error storing canonical URLs: {e}[/red]")
        
    # Store legends URLs with lower priority
    if legends_urls:
        try:
            result = await store.store_urls(
                urls=list(legends_urls),
                priority='low',
                category=f"{tag} (Legends)"
            )
            console.print(f"📥 Stored {result['inserted']} new legends URLs")
            results['legends'] = result['inserted']
        except Exception as e:
            logger.error(f"Error storing legends URLs: {e}")
            console.print(f"[red]Error storing legends URLs: {e}[/red]")
            
    # Store unknown URLs with lowest priority
    if unknown_urls:
        try:
            result = await store.store_urls(
                urls=list(unknown_urls),
                priority='low',
                category=f"{tag} (Unknown)"
            )
            console.print(f"📥 Stored {result['inserted']} new unknown URLs")
            results['unknown'] = result['inserted']
        except Exception as e:
            logger.error(f"Error storing unknown URLs: {e}")
            console.print(f"[red]Error storing unknown URLs: {e}[/red]")
    
    return results

async def collect_phase1_urls():
    """Collect URLs for all Phase 1 content categories."""
    # Initialize database tables if needed
    store = URLStore()
    await store.initialize_tables()
    
    all_results = {}
    
    # --- R3X/RX-24 Information ---
    rx24_search_urls = await collect_urls_by_search_terms(
        RX24_SEARCH_TERMS, "high", "R3X/RX-24"
    )
    rx24_category_urls = await collect_urls_by_categories(
        RX24_CATEGORIES, "high", "R3X/RX-24"
    )
    all_rx24_urls = rx24_search_urls.union(rx24_category_urls)
    rx_results = await filter_and_store_urls(all_rx24_urls, "high", "R3X/RX-24")
    all_results["R3X/RX-24"] = rx_results
    
    # --- Oga's Cantina Information ---
    cantina_search_urls = await collect_urls_by_search_terms(
        OGAS_CANTINA_SEARCH_TERMS, "high", "Oga's Cantina"
    )
    cantina_category_urls = await collect_urls_by_categories(
        OGAS_CANTINA_CATEGORIES, "high", "Oga's Cantina"
    )
    all_cantina_urls = cantina_search_urls.union(cantina_category_urls)
    cantina_results = await filter_and_store_urls(all_cantina_urls, "high", "Oga's Cantina")
    all_results["Oga's Cantina"] = cantina_results
    
    # --- DJ/Entertainment Information ---
    dj_search_urls = await collect_urls_by_search_terms(
        DJ_ENTERTAINMENT_SEARCH_TERMS, "medium", "DJ/Entertainment"
    )
    dj_category_urls = await collect_urls_by_categories(
        DJ_ENTERTAINMENT_CATEGORIES, "medium", "DJ/Entertainment"
    )
    all_dj_urls = dj_search_urls.union(dj_category_urls)
    dj_results = await filter_and_store_urls(all_dj_urls, "medium", "DJ/Entertainment")
    all_results["DJ/Entertainment"] = dj_results
    
    # Generate a report of all collected URLs
    reporter = CoverageReporter()
    report = await reporter.generate_report_by_category()
    console.print(report)
    
    # Summary
    console.print("\n✨ Phase 1 URL collection complete!")
    total_canonical = sum(results.get('canonical', 0) for results in all_results.values())
    total_legends = sum(results.get('legends', 0) for results in all_results.values())
    total_urls = total_canonical + total_legends
    
    console.print(f"[bold]Total URLs collected for Phase 1:[/bold] {total_urls}")
    console.print(f"- Canonical: {total_canonical}")
    console.print(f"- Legends: {total_legends}")
    console.print(f"Target: 50-100 articles, Current: {total_urls}")

async def run_database_tests():
    """
    Run comprehensive tests for database operations.
    """
    console.print("[bold]Running Database Connection Tests[/bold]")
    
    # Test URLStore initialization
    try:
        store = URLStore()
        await store.initialize_tables()
        console.print("  [green]✓[/green] Successfully connected to database")
        console.print("  [green]✓[/green] Tables initialized")
    except Exception as e:
        console.print(f"  [red]✗[/red] Database connection failed: {e}")
        return False
    
    # Test URL insertion
    try:
        test_urls = [
            {
                "url": "https://starwars.fandom.com/wiki/Test_Article_1",
                "title": "Test Article 1",
                "priority": "high",
                "content_type": "canonical",
                "category": "Test Category"
            },
            {
                "url": "https://starwars.fandom.com/wiki/Test_Article_2",
                "title": "Test Article 2",
                "priority": "medium",
                "content_type": "legends",
                "category": "Test Category"
            }
        ]
        
        # Convert to format for store_urls
        urls = [item["url"] for item in test_urls]
        
        # Insert test data
        result = await store.store_urls(
            urls=urls,
            priority="high",
            category="Test Category"
        )
        
        if result["inserted"] > 0:
            console.print(f"  [green]✓[/green] Successfully inserted {result['inserted']} test URLs")
        else:
            console.print(f"  [yellow]⚠[/yellow] No new URLs inserted (may already exist)")
            
        # Clean up test data
        await store._execute_query(
            "DELETE FROM holocron_urls WHERE url LIKE 'https://starwars.fandom.com/wiki/Test_Article_%'"
        )
        console.print("  [green]✓[/green] Test data cleaned up")
        
    except Exception as e:
        console.print(f"  [red]✗[/red] URL insertion test failed: {e}")
        return False
    
    # Test reporting
    try:
        reporter = CoverageReporter()
        summary = await reporter.generate_summary()
        console.print("  [green]✓[/green] Reporting functionality verified")
        console.print(f"    Current URL count: {summary['total_urls']}")
    except Exception as e:
        console.print(f"  [red]✗[/red] Reporting test failed: {e}")
        return False
    
    console.print("[green]All database tests completed successfully![/green]")
    return True

def main():
    """Parse arguments and run the URL collection."""
    parser = argparse.ArgumentParser(description="Collect URLs for Phase 1 of the Holocron")
    parser.add_argument('--report-only', action='store_true',
                        help="Only generate a report, don't collect new URLs")
    parser.add_argument('--test', action='store_true',
                        help="Run in test mode without database interaction")
    parser.add_argument('--db-test', action='store_true',
                        help="Run database connection and operation tests")
    
    args = parser.parse_args()
    
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    if args.test:
        # Run in test mode without database access
        try:
            asyncio.run(test_url_collection())
        except Exception as e:
            console.print(f"[bold red]Test mode error: {e}[/bold red]")
            sys.exit(1)
        return
        
    if args.db_test:
        # Run database tests
        try:
            result = asyncio.run(run_database_tests())
            if not result:
                console.print("[bold red]Database tests failed[/bold red]")
                sys.exit(1)
        except Exception as e:
            console.print(f"[bold red]Database test error: {e}[/bold red]")
            sys.exit(1)
        return
    
    # For regular mode
    try:
        if args.report_only:
            # Just generate a report
            async def run_report():
                reporter = CoverageReporter()
                report = await reporter.generate_report_by_category()
                console.print(report)
                
            asyncio.run(run_report())
        else:
            # Run the full URL collection process
            asyncio.run(collect_phase1_urls())
            
    except ValueError as e:
        if "Missing Supabase credentials" in str(e):
            console.print("[bold red]Error: Missing Supabase credentials[/bold red]")
            console.print("Please ensure the following environment variables are set:")
            console.print("- SUPABASE_URL: Your Supabase project URL")
            console.print("- SUPABASE_KEY: Your Supabase API key or SUPABASE_SERVICE_ROLE_KEY")
            console.print("\nYou can set these by running:")
            console.print("export SUPABASE_URL=your-project-url")
            console.print("export SUPABASE_KEY=your-api-key")
            console.print("\nAlternatively, you can run in test mode without database access:")
            console.print("python scripts/collect_phase1_urls.py --test")
        else:
            console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)

if __name__ == "__main__":
    main() 