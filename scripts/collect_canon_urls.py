#!/usr/bin/env python3
"""
Canon URL Collection Script for DJ R3X Holocron

This script collects all canon URLs from Wookieepedia and categorizes them by priority:
1. Galaxy's Edge related content
2. Droid-specific articles
3. Entertainment/Music content
4. General canon content

Features:
- Collects all canon articles (~49K)
- Assigns priority levels based on content relevance
- Stores URLs in Supabase with metadata
- Provides detailed progress reporting
- Handles rate limiting and pagination
- Implements batch processing and parallel execution

Usage:
    python scripts/collect_canon_urls.py           # Full collection with database storage
    python scripts/collect_canon_urls.py --dry-run # Test run without database writes
    python scripts/collect_canon_urls.py --report  # Generate coverage report
"""

import os
import sys
import asyncio
import logging
import argparse
from typing import List, Dict, Set, Any, Tuple
from datetime import datetime
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.table import Table
import aiohttp
from urllib.parse import quote, unquote
from concurrent.futures import ThreadPoolExecutor
from functools import partial

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
        logging.FileHandler(f"logs/canon_collection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)
console = Console()

# Processing configuration
BATCH_SIZE = 1000
MAX_WORKERS = 5
MAX_CONCURRENT_REQUESTS = 10

# Priority Categories with expanded terms
GALAXYS_EDGE_TERMS = [
    # Locations
    "Galaxy's Edge", "Black Spire Outpost", "Batuu", "Oga's Cantina",
    "Droid Depot", "Star Wars: Galaxy's Edge", "Savi's Workshop",
    "Dok-Ondar's Den of Antiquities", "Millennium Falcon: Smugglers Run",
    "Rise of the Resistance", "Docking Bay 7", "Ronto Roasters",
    "Kat Saka's Kettle", "Creature Stall", "Toydarian Toymaker",
    # Characters
    "Oga Garra", "Dok-Ondar", "Mubo", "Hondo Ohnaka", "Vi Moradi",
    "Lieutenant Bek", "Nien Nunb", "DJ R-3X", "RX-24", "Captain Rex",
    # Culture & Language
    "Batuuan", "Bright Suns", "Rising Moons", "Till the Spire",
    "Black Spire", "Spira", "Batuu Credits",
    # Related Terms
    "Outpost", "Resistance Supply", "First Order Cargo",
    "Gatherers", "Smugglers", "Travelers", "BSO"
]

DROID_TERMS = [
    # Droid Types
    "Astromech", "Protocol droid", "Battle droid", "Probe droid",
    "Medical droid", "Maintenance droid", "Security droid",
    "Repair droid", "Entertainment droid", "Service droid",
    "DJ droid", "Bartender droid", "Pilot droid",
    # Manufacturing & Technical
    "Droid manufacturer", "Droid model", "Droid series",
    "Droid programming", "Droid memory", "Droid personality",
    "Droid components", "Droid parts", "Droid repair",
    # Specific Series
    "R-series", "BB-series", "C-series", "IG-series",
    "RX-series", "DJ-series", "EV-series", "GE-series",
    # Characteristics
    "Droid intelligence", "Droid rights", "Droid rebellion",
    "Droid behavior", "Droid customization", "Droid voices",
    # Locations
    "Droid factory", "Droid shop", "Droid maintenance",
    "Droid charging station", "Droid bay"
]

ENTERTAINMENT_TERMS = [
    # Music Genres
    "Jizz", "Jizz-wailer", "Bith music", "Coruscant bar music",
    "Outer Rim music", "Core Worlds music", "Traditional music",
    # Bands & Musicians
    "Modal Nodes", "Max Rebo Band", "Cantina band",
    "Figrin D'an", "Max Rebo", "Sy Snootles",
    # Instruments
    "Kloo horn", "Fanfar", "Chidinkalu", "Dorenian Beshniquel",
    "Ommni box", "Slitherhorn", "Bandfill",
    # Venues
    "Cantina", "Club", "Theater", "Concert hall",
    "Entertainment district", "Music hall", "Performance venue",
    # Performance Types
    "Live performance", "Holographic performance", "Street performance",
    "Imperial ball", "Galactic opera", "Dance performance",
    # Culture
    "Galactic entertainment", "Cultural performance",
    "Musical tradition", "Art form", "Artistic expression",
    "Festival music", "Celebration music", "Ceremonial music",
    # Technology
    "Holovid", "Hologram entertainment", "Music holorecording",
    "Entertainment system", "Sound system", "Amplification"
]

# Priority Categories for API queries with expanded coverage
PRIORITY_CATEGORIES = {
    "high": [
        "Category:Star Wars: Galaxy's Edge",
        "Category:Batuu",
        "Category:Black Spire Outpost",
        "Category:Locations in Star Wars: Galaxy's Edge",
        "Category:Characters in Star Wars: Galaxy's Edge"
    ],
    "medium-high": [
        "Category:Droid models",
        "Category:Entertainment droids",
        "Category:Pilot droids",
        "Category:DJ droids"
    ],
    "medium": [
        "Category:Droids",
        "Category:Entertainment",
        "Category:Musicians",
        "Category:Musical instruments",
        "Category:Droid manufacturers",
        "Category:Music",
        "Category:Cantinas",
        "Category:Entertainment districts"
    ],
    "medium-low": [
        "Category:Technology",
        "Category:Culture",
        "Category:Celebrations",
        "Category:Customs",
        "Category:Languages"
    ]
}

# Priority scoring weights
PRIORITY_WEIGHTS = {
    "term_match": {
        "galaxys_edge": 5.0,
        "droids": 4.0,
        "entertainment": 4.0,
        "related": 2.0
    },
    "category_match": {
        "high": 5.0,
        "medium-high": 4.0,
        "medium": 3.5,
        "medium-low": 2.5,
        "low": 1.5
    }
}

# Priority thresholds for final categorization
PRIORITY_THRESHOLDS = {
    "high": 4.5,
    "medium": 3.0,
    "low": 0.0
}

class CanonURLCollector:
    """Collects and categorizes canon URLs from Wookieepedia."""
    
    def __init__(self, session: aiohttp.ClientSession, batch_size: int):
        self.session = session
        self.base_url = "https://starwars.fandom.com/api.php"
        self.collected_urls: Set[str] = set()
        self.url_priorities: Dict[str, Dict[str, Any]] = {}
        self.url_categories: Dict[str, List[str]] = {}
        self.subcategories: Dict[str, Set[str]] = {
            "high": set(),
            "medium": set(),
            "low": set()
        }
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self.batch_size = batch_size
    
    async def collect_all_canon_urls(self) -> Set[str]:
        """Collect all canon article URLs using continuation."""
        params = {
            'action': 'query',
            'list': 'categorymembers',
            'cmtitle': 'Category:Canon articles',
            'format': 'json',
            'cmlimit': 500  # Maximum allowed by API
        }
        
        continue_params = {}
        total_collected = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            # Start with an estimate of 50,000 URLs
            task = progress.add_task("Collecting canon URLs...", total=50000)
            
            while True:
                try:
                    async with self.session.get(
                        self.base_url,
                        params={**params, **continue_params}
                    ) as response:
                        if response.status != 200:
                            logger.error(f"API request failed with status {response.status}")
                            break
                        
                        data = await response.json()
                        
                        if 'query' in data and 'categorymembers' in data['query']:
                            new_urls = {
                                f"https://starwars.fandom.com/wiki/{self._encode_wiki_title(member['title'])}"
                                for member in data['query']['categorymembers']
                                if member['ns'] == 0  # Articles only
                            }
                            self.collected_urls.update(new_urls)
                            total_collected = len(self.collected_urls)
                            progress.update(
                                task,
                                completed=total_collected,
                                description=f"[cyan]Collected {total_collected:,d} canon URLs[/cyan]"
                            )
                            
                            # Update the total if we exceed our estimate
                            if total_collected > progress.tasks[task].total:
                                progress.update(task, total=total_collected + 1000)
                        
                        if 'continue' not in data:
                            break
                            
                        continue_params = data['continue']
                        
                except Exception as e:
                    logger.error(f"Error during URL collection: {e}")
                    break
        
        logger.info(f"Total canon URLs collected: {total_collected:,d}")
        return self.collected_urls

    async def get_article_categories(self, title: str) -> List[str]:
        """Get categories for an article using the API."""
        params = {
            'action': 'query',
            'prop': 'categories',
            'titles': title,
            'format': 'json',
            'cllimit': 500
        }
        
        categories = []
        try:
            async with self.session.get(self.base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'query' in data and 'pages' in data['query']:
                        page = next(iter(data['query']['pages'].values()))
                        if 'categories' in page:
                            categories = [cat['title'] for cat in page['categories']]
        except Exception as e:
            logger.error(f"Error getting categories for {title}: {e}")
        
        return categories

    async def determine_priority(self, url: str, categories: List[str]) -> Tuple[str, str]:
        """
        Determine priority based on weighted scoring of content relevance and categories.
        Returns a tuple of (priority_level, main_category).
        """
        decoded_url = unquote(url)
        title = decoded_url.split('/')[-1].replace('_', ' ')
        
        # Initialize scores for each category
        scores = {
            "galaxys_edge": 0.0,
            "droids": 0.0,
            "entertainment": 0.0,
            "related": 0.0
        }
        
        # Score based on term matches in URL and title
        content = f"{decoded_url.lower()} {title.lower()}"
        
        # Check Galaxy's Edge terms
        galaxys_edge_matches = sum(1 for term in GALAXYS_EDGE_TERMS 
                                 if term.lower() in content)
        scores["galaxys_edge"] += galaxys_edge_matches * PRIORITY_WEIGHTS["term_match"]["galaxys_edge"]
        
        # Check Droid terms
        droid_matches = sum(1 for term in DROID_TERMS 
                          if term.lower() in content)
        scores["droids"] += droid_matches * PRIORITY_WEIGHTS["term_match"]["droids"]
        
        # Check Entertainment terms
        entertainment_matches = sum(1 for term in ENTERTAINMENT_TERMS 
                                  if term.lower() in content)
        scores["entertainment"] += entertainment_matches * PRIORITY_WEIGHTS["term_match"]["entertainment"]
        
        # Score based on categories
        for category in categories:
            category_lower = category.lower()
            
            # Check high priority categories
            if any(high_cat.lower() in category_lower 
                  for high_cat in PRIORITY_CATEGORIES["high"]):
                scores["galaxys_edge"] += PRIORITY_WEIGHTS["category_match"]["high"]
            
            # Check medium-high priority categories
            elif any(med_high_cat.lower() in category_lower 
                    for med_high_cat in PRIORITY_CATEGORIES["medium-high"]):
                if "droid" in category_lower:
                    scores["droids"] += PRIORITY_WEIGHTS["category_match"]["medium-high"]
                else:
                    scores["entertainment"] += PRIORITY_WEIGHTS["category_match"]["medium-high"]
            
            # Check medium priority categories
            elif any(med_cat.lower() in category_lower 
                    for med_cat in PRIORITY_CATEGORIES["medium"]):
                if "droid" in category_lower:
                    scores["droids"] += PRIORITY_WEIGHTS["category_match"]["medium"]
                else:
                    scores["entertainment"] += PRIORITY_WEIGHTS["category_match"]["medium"]
            
            # Check medium-low priority categories
            elif any(med_low_cat.lower() in category_lower 
                    for med_low_cat in PRIORITY_CATEGORIES["medium-low"]):
                scores["related"] += PRIORITY_WEIGHTS["category_match"]["medium-low"]
        
        # Determine main category based on highest score
        main_category = max(scores.items(), key=lambda x: x[1])[0]
        max_score = scores[main_category]
        
        # Determine priority level based on score thresholds, mapping to valid DB enum values
        if max_score >= PRIORITY_THRESHOLDS["high"]:
            return "high", main_category
        elif max_score >= PRIORITY_THRESHOLDS["medium"]:
            return "medium", main_category
        else:
            return "low", main_category
    
    async def process_url_batch(
        self,
        urls: List[str],
        progress: Progress,
        task_id: int,
        batch_num: int,
        total_batches: int
    ) -> List[Dict[str, Any]]:
        """Process a batch of URLs with categorization."""
        results = []
        batch_size = len(urls)
        
        for i, url in enumerate(urls):
            async with self.semaphore:
                try:
                    title = unquote(url.split('/')[-1])
                    categories = await self.get_article_categories(title)
                    priority, category = await self.determine_priority(url, categories)
                    
                    results.append({
                        "url": url,
                        "title": title,
                        "priority": priority,
                        "categories": categories,
                        "discovered_at": datetime.now().isoformat(),
                        "is_processed": False
                    })
                    
                    # Store in collector's data structures for later use if needed
                    self.url_priorities[url] = {
                        "priority": priority,
                        "category": category
                    }
                    self.url_categories[url] = categories
                    self.subcategories[priority].add(category)
                    
                    # Update progress
                    progress.update(
                        task_id, 
                        advance=1,
                        description=f"[cyan]Processing batch {batch_num}/{total_batches} - URL {i+1}/{batch_size}[/cyan]"
                    )
                except Exception as e:
                    logger.error(f"Error processing URL {url}: {e}")
                    # Continue with next URL
                    progress.update(task_id, advance=1)
        
        # Log summary for this batch
        priority_counts = {}
        for result in results:
            priority = result.get("priority", "low")
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
            
        logger.info(f"Batch {batch_num}/{total_batches} priorities: {priority_counts}")
        
        return results

    async def process_all_urls(self) -> List[Dict[str, Any]]:
        """Process all collected URLs in parallel batches."""
        all_urls = list(self.collected_urls)
        total_urls = len(all_urls)
        batches = [all_urls[i:i + self.batch_size] for i in range(0, total_urls, self.batch_size)]
        total_batches = len(batches)
        
        console.print(f"\n[yellow]Starting URL processing in {total_batches} batches of {self.batch_size} URLs each[/yellow]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task_id = progress.add_task(
                "[cyan]Processing URLs...[/cyan]",
                total=total_urls
            )
            
            tasks = [
                self.process_url_batch(
                    batch,
                    progress,
                    task_id,
                    batch_num=i+1,
                    total_batches=total_batches
                )
                for i, batch in enumerate(batches)
            ]
            
            results = await asyncio.gather(*tasks)
            
        # Flatten results
        return [item for batch in results for item in batch]

    @staticmethod
    def _encode_wiki_title(title: str) -> str:
        """
        Encode a wiki title for use in a URL.
        Properly handles titles that start with % or contain literal % symbols.
        """
        # First handle basic character replacements
        title = title.replace(' ', '_')
        title = title.replace('&', '%26')
        title = title.replace('?', '%3F')
        title = title.replace("'", '%27')
        title = title.replace('"', '%22')
        
        # Special handling for % symbols
        segments = []
        parts = title.split('%')
        
        # Handle the first part specially
        if parts[0] == '':  # Title starts with %
            segments.append('%25')  # Encode the leading % as %25
            parts = parts[1:]  # Remove the empty first segment
        else:
            segments.append(quote(parts[0], safe='/_-:'))
            parts = parts[1:]
            
        # Handle remaining parts
        for part in parts:
            if part:
                if len(part) >= 2 and all(c in '0123456789ABCDEFabcdef' for c in part[:2]):
                    # This looks like it's already percent-encoded
                    segments.append(f'%{part}')
                else:
                    # This is a literal % followed by non-hex, encode the % and the text
                    segments.append('%25')
                    segments.append(quote(part, safe='/_-:'))
            else:
                # Empty part means we had consecutive % symbols
                segments.append('%25')
        
        return ''.join(segments)

async def store_urls(collector: CanonURLCollector, dry_run: bool = False) -> None:
    """Store processed URLs in the database with batch processing."""
    if dry_run:
        logger.info("Dry run - skipping database storage")
        return
        
    url_store = URLStore()
    
    # CHECKPOINT 1: Verify collected URLs
    logger.info(f"CHECKPOINT 1: Total URLs collected before processing: {len(collector.collected_urls)}")
    console.print(f"[yellow]CHECKPOINT 1: {len(collector.collected_urls)} URLs collected before processing[/yellow]")
    
    # Process the URLs
    processed_urls = await collector.process_all_urls()
    
    # CHECKPOINT 2: Verify processed URLs
    logger.info(f"CHECKPOINT 2: URLs after processing: {len(processed_urls)}")
    console.print(f"[yellow]CHECKPOINT 2: {len(processed_urls)} URLs after processing[/yellow]")
    
    # Log the number of URLs that will be processed
    logger.info(f"Processing {len(processed_urls):,d} URLs for database storage")
    
    if not processed_urls:
        logger.warning("No URLs to store in database - URL processing returned empty result")
        console.print("[yellow]Warning: No URLs to store - processing returned empty result[/yellow]")
        return
    
    # CHECKPOINT 3: Show sample of processed URLs
    if processed_urls:
        sample = processed_urls[0] if len(processed_urls) == 1 else [processed_urls[0], processed_urls[-1]]
        logger.info(f"CHECKPOINT 3: Sample processed URL data: {sample}")
    
    # Group URLs by priority for reporting
    priority_counts = {
        "high": 0,
        "medium": 0,
        "low": 0
    }
    
    # Store URLs in batches
    total_batches = len(processed_urls) // BATCH_SIZE + (1 if len(processed_urls) % BATCH_SIZE else 0)
    
    console.print(f"\n[yellow]Storing {len(processed_urls):,d} URLs in {total_batches} batches[/yellow]")
    
    total_inserted = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        store_task = progress.add_task(
            "[cyan]Storing URLs in database...[/cyan]",
            total=len(processed_urls)
        )
        
        for i in range(0, len(processed_urls), BATCH_SIZE):
            batch = processed_urls[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            
            # CHECKPOINT 4: Verify batch data before storage
            logger.info(f"CHECKPOINT 4: Preparing batch {batch_num}/{total_batches} with {len(batch)} URLs")
            if batch:
                # Log the first and last URL in the batch
                logger.info(f"Batch {batch_num} first URL: {batch[0]['url']}")
                logger.info(f"Batch {batch_num} last URL: {batch[-1]['url']}")
            
            try:
                # Update priority counts
                for url_data in batch:
                    priority = url_data.get("priority", "low")
                    priority_counts[priority] += 1
                
                # Store batch and await the result
                logger.info(f"Sending batch {batch_num}/{total_batches} with {len(batch)} URLs to database")
                result = await url_store.store_batch(batch)
                inserted_count = result.get('inserted', 0)
                total_inserted += inserted_count
                
                # CHECKPOINT 5: Verify storage result
                logger.info(f"CHECKPOINT 5: Batch {batch_num} storage result: {result}")
                logger.info(f"Stored batch {batch_num}/{total_batches} with {inserted_count} URLs (Total: {total_inserted})")
                
                progress.update(
                    store_task,
                    advance=len(batch),
                    description=f"[cyan]Storing Batch {batch_num}/{total_batches} ({inserted_count} inserted)[/cyan]"
                )
                
            except Exception as e:
                error_msg = f"Error storing batch {batch_num}/{total_batches}: {e}"
                logger.error(error_msg)
                console.print(f"[bold red]{error_msg}[/bold red]")
                # Continue with next batch rather than stopping completely
                continue
    
    # CHECKPOINT 6: Final verification
    logger.info(f"CHECKPOINT 6: Total URLs inserted according to counts: {total_inserted}")
    
    # Display results
    console.print(f"\n[bold green]Collection Complete! Stored {total_inserted} URLs in total.[/bold green]")
    
    table = Table(title="URL Collection Results")
    table.add_column("Priority", style="cyan")
    table.add_column("Count", style="magenta")
    table.add_column("Percentage", style="green")
    
    total_urls = sum(priority_counts.values())
    for priority, count in priority_counts.items():
        percentage = (count / total_urls) * 100 if total_urls > 0 else 0
        table.add_row(
            priority,
            str(count),
            f"{percentage:.1f}%"
        )
    
    console.print(table)

async def main():
    parser = argparse.ArgumentParser(description="Collect canon URLs from Wookieepedia")
    parser.add_argument("--dry-run", action="store_true", help="Run without storing to database")
    parser.add_argument("--report", action="store_true", help="Generate coverage report only")
    parser.add_argument("--limit", type=int, help="Limit the number of URLs to process (for testing)")
    parser.add_argument("--debug", action="store_true", help="Enable extra debug logging")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for URL processing")
    args = parser.parse_args()
    
    # Set debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger('holocron').setLevel(logging.DEBUG)
        logger.info("Debug logging enabled")
    
    if args.report:
        reporter = CoverageReporter()
        await reporter.generate_report()
        return
    
    console.print("[bold cyan]Starting Canon URL Collection Process...[/bold cyan]")
    logger.info("Starting Canon URL collection process")
    
    # Verify environment setup
    try:
        # Check Supabase connection before proceeding
        url_store = URLStore()
        logger.info("Successfully connected to Supabase")
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")
        console.print(f"[bold red]Error: Failed to connect to Supabase: {e}[/bold red]")
        return
    
    async with aiohttp.ClientSession() as session:
        collector = CanonURLCollector(session, batch_size=args.batch_size)
        
        # MAIN CHECKPOINT 1: Start URL collection
        logger.info("MAIN CHECKPOINT 1: Starting URL collection")
        console.print("[bold cyan]Collecting Canon URLs...[/bold cyan]")
        
        urls = await collector.collect_all_canon_urls()
        
        # MAIN CHECKPOINT 2: URL collection complete
        logger.info(f"MAIN CHECKPOINT 2: URL collection complete, {len(urls)} URLs collected")
        
        # Apply URL limit if specified
        if args.limit and args.limit > 0:
            url_list = list(collector.collected_urls)[:args.limit]
            console.print(f"[yellow]Limiting to {args.limit} URLs for testing[/yellow]")
            logger.info(f"Limiting to {args.limit} URLs for testing")
            # Update collector's URLs
            collector.collected_urls = set(url_list)
            logger.info(f"Collector now has {len(collector.collected_urls)} URLs after limiting")
        
        # MAIN CHECKPOINT 3: Starting batch processing
        logger.info(f"MAIN CHECKPOINT 3: Starting batch processing for {len(collector.collected_urls)} URLs")
        console.print("[bold cyan]Processing and storing URLs in batches...[/bold cyan]")
        
        # Use a completely batch-oriented approach
        url_list = list(collector.collected_urls)
        batch_size = args.batch_size
        total_batches = (len(url_list) + batch_size - 1) // batch_size
        
        # Prepare variables for tracking results
        total_processed = 0
        total_stored = 0
        priority_counts = {"high": 0, "medium": 0, "low": 0}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            overall_task = progress.add_task(
                "[bold cyan]Overall Progress[/bold cyan]", 
                total=len(url_list)
            )
            
            for batch_index in range(0, total_batches):
                start_idx = batch_index * batch_size
                end_idx = min(start_idx + batch_size, len(url_list))
                current_batch = url_list[start_idx:end_idx]
                
                batch_task = progress.add_task(
                    f"[cyan]Processing batch {batch_index + 1}/{total_batches}[/cyan]",
                    total=len(current_batch)
                )
                
                try:
                    logger.info(f"Starting batch {batch_index + 1}/{total_batches} with {len(current_batch)} URLs")
                    
                    # Only categorize the URLs in dry run mode, otherwise let the process_url_batch handle it
                    if args.dry_run:
                        # Skip API calls in dry run mode
                        logger.info(f"Dry run mode: Using URL-based categorization for batch {batch_index + 1}")
                        for url in current_batch:
                            # Use URL-based categorization only
                            priority, category = await collector.determine_priority(url, [])
                            collector.url_priorities[url] = {
                                "priority": priority,
                                "category": category
                            }
                            collector.url_categories[url] = [category]
                            collector.subcategories[priority].add(category)
                            progress.update(batch_task, advance=1)
                            progress.update(overall_task, advance=1)
                            total_processed += 1
                    else:
                        # Process this batch of URLs and immediately store them
                        processed_batch = await collector.process_url_batch(
                            current_batch, 
                            progress, 
                            batch_task, 
                            batch_index + 1, 
                            total_batches
                        )
                        
                        logger.info(f"Processed {len(processed_batch)} URLs in batch {batch_index + 1}")
                        total_processed += len(processed_batch)
                        
                        # Update priority counts
                        for url_data in processed_batch:
                            priority = url_data.get("priority", "low")
                            priority_counts[priority] += 1
                        
                        # Store this batch immediately if not in dry run mode
                        logger.info(f"Storing batch {batch_index + 1}/{total_batches} with {len(processed_batch)} URLs")
                        result = await url_store.store_batch(processed_batch)
                        inserted_count = result.get('inserted', 0)
                        total_stored += inserted_count
                        logger.info(f"Stored {inserted_count} URLs from batch {batch_index + 1}")
                        
                        # Update the overall progress
                        progress.update(overall_task, advance=len(current_batch))
                        
                except Exception as e:
                    error_msg = f"Error processing batch {batch_index + 1}/{total_batches}: {e}"
                    logger.error(error_msg)
                    console.print(f"[bold red]ERROR: {error_msg}[/bold red]")
                    # Continue with next batch
                    progress.update(overall_task, advance=len(current_batch))
                    continue
                finally:
                    # Remove the batch task
                    progress.remove_task(batch_task)
                
                # Checkpoint after each batch
                logger.info(f"Completed batch {batch_index + 1}/{total_batches}. Total processed: {total_processed}, Total stored: {total_stored}")
        
        # MAIN CHECKPOINT 4: Batch processing complete
        logger.info(f"MAIN CHECKPOINT 4: Batch processing complete. Processed {total_processed} URLs, stored {total_stored} URLs")
        console.print(f"\n[bold green]Collection Complete! Processed {total_processed} URLs, stored {total_stored} in total.[/bold green]")
        
        # Display results
        table = Table(title="URL Collection Results")
        table.add_column("Priority", style="cyan")
        table.add_column("Count", style="magenta")
        table.add_column("Percentage", style="green")
        
        total_urls = sum(priority_counts.values())
        for priority, count in priority_counts.items():
            percentage = (count / total_urls) * 100 if total_urls > 0 else 0
            table.add_row(
                priority,
                str(count),
                f"{percentage:.1f}%"
            )
        
        console.print(table)
        
        if not args.dry_run:
            # Try to verify database counts
            try:
                total_count = await url_store.get_total_count()
                logger.info(f"Final database count: {total_count} URLs")
                console.print(f"[green]Final database count: {total_count} URLs[/green]")
            except Exception as e:
                logger.error(f"Error getting final database count: {e}")
                console.print(f"[yellow]Could not verify final database count: {e}[/yellow]")
                
        logger.info("Canon URL collection process finished")

if __name__ == "__main__":
    asyncio.run(main()) 