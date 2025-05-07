"""
Main entry point for the URL Collection System.
"""

import asyncio
import logging
import click
from typing import Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
import sys
import os

from .sitemap_crawler import SitemapCrawler
from .category_crawler import CategoryCrawler
from .url_store import URLStore
from .content_filter import ContentFilter
from .reporting import CoverageReporter

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
console = Console()

async def initialize_database():
    """Initialize the database tables."""
    store = URLStore()
    await store.initialize_tables()
    console.print("✅ Database tables initialized")
    
async def collect_urls(method: str = 'all', category: Optional[str] = None):
    """Collect URLs using specified method."""
    store = URLStore()
    urls = set()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        if method in ['all', 'sitemap']:
            task = progress.add_task("🔍 Crawling sitemaps...", total=None)
            async with SitemapCrawler() as crawler:
                sitemap_urls = await crawler.crawl()
                urls.update(sitemap_urls)
                progress.update(task, completed=True)
                
        if method in ['all', 'category']:
            task = progress.add_task("📑 Crawling categories...", total=None)
            async with CategoryCrawler() as crawler:
                if category:
                    category_urls = await crawler.crawl_category(category)
                else:
                    results = await crawler.crawl()
                    category_urls = set().union(*results.values())
                urls.update(category_urls)
                progress.update(task, completed=True)
                
    console.print(f"📝 Found {len(urls)} unique URLs")
    return urls
    
async def filter_content(urls):
    """Filter and categorize content."""
    console.print("🔍 Analyzing content types...")
    async with ContentFilter() as filter:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Analyzing URLs...", total=len(urls))
            results = await filter.filter_urls(list(urls))
            progress.update(task, completed=True)
            
    return results
    
async def store_urls(urls, content_type='unknown'):
    """Store URLs in the database."""
    store = URLStore()
    result = await store.store_urls(list(urls), priority='medium')
    console.print(f"💾 Stored {result['inserted']} new URLs")
    return result
    
async def generate_report():
    """Generate and display a coverage report."""
    reporter = CoverageReporter()
    report = await reporter.generate_full_report()
    console.print(report)
    
@click.group()
def cli():
    """DJ R3X Holocron URL Collection System"""
    pass
    
@cli.command()
@click.option('--method', type=click.Choice(['all', 'sitemap', 'category']), 
              default='all', help='URL collection method')
@click.option('--category', help='Specific category to crawl')
def collect(method, category):
    """Collect URLs from Wookieepedia."""
    async def run():
        try:
            # Initialize database
            await initialize_database()
            
            # Collect URLs
            urls = await collect_urls(method, category)
            
            # Filter content
            results = await filter_content(urls)
            
            # Store URLs by content type
            for content_type, type_urls in results.items():
                await store_urls(type_urls, content_type)
                
            # Generate report
            await generate_report()
            
        except Exception as e:
            logger.error(f"Error during collection: {e}")
            sys.exit(1)
            
    asyncio.run(run())
    
@cli.command()
def report():
    """Generate a coverage report."""
    async def run():
        try:
            await generate_report()
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            sys.exit(1)
            
    asyncio.run(run())
    
if __name__ == '__main__':
    cli() 