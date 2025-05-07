#!/usr/bin/env python3
"""
Fix Processed URLs in Holocron Database

This script updates the is_processed flag for URLs in the holocron_urls table
based on the checkpoint file and knowledge base entries.
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rich console for pretty output
console = Console()

async def get_latest_checkpoint():
    """Get the latest checkpoint file"""
    checkpoint_dir = "data/checkpoints"
    if not os.path.exists(checkpoint_dir):
        return None
        
    checkpoint_files = [f for f in os.listdir(checkpoint_dir) if f.startswith("batch_progress_") and f.endswith(".json")]
    if not checkpoint_files:
        return None
        
    # Sort by modification time to get the latest
    latest_file = max([os.path.join(checkpoint_dir, f) for f in checkpoint_files], key=os.path.getmtime)
    
    # Read the checkpoint file
    with open(latest_file, 'r') as f:
        checkpoint_data = json.load(f)
    
    return checkpoint_data, latest_file

async def mark_urls_as_processed(urls, supabase):
    """Mark URLs as processed in the database"""
    processed_count = 0
    for url in urls:
        try:
            result = supabase.table('holocron_urls').update({
                "is_processed": True,
                "last_checked": datetime.utcnow().isoformat()
            }).eq("url", url).execute()
            
            if result.data:
                processed_count += 1
                logger.info(f"Marked as processed: {url}")
            else:
                logger.warning(f"URL not found in database: {url}")
        except Exception as e:
            logger.error(f"Error updating URL {url}: {e}")
    
    return processed_count

async def main():
    """Main function"""
    console.print("[bold]🧪 Fixing Processed URLs in Holocron Database[/bold]")
    console.print("=" * 80)
    
    # Load environment variables
    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not supabase_key:
        console.print("[bold red]Error: Missing Supabase credentials[/bold red]")
        return
    
    try:
        # Initialize Supabase client
        supabase = create_client(supabase_url, supabase_key)
        
        # Get checkpoint data
        checkpoint_data, checkpoint_file = await get_latest_checkpoint()
        if not checkpoint_data:
            console.print("[bold yellow]No checkpoint files found[/bold yellow]")
            return
            
        console.print(f"[green]Found checkpoint {os.path.basename(checkpoint_file)} with {checkpoint_data['processed_urls']} processed URLs[/green]")
        
        # Get the list of URLs that have knowledge chunks in the database
        console.print("[bold]Checking knowledge chunks in database...[/bold]")
        knowledge_result = supabase.table('holocron_knowledge').select('metadata').execute()
        
        url_set = set()
        if knowledge_result.data:
            for item in knowledge_result.data:
                if item.get('metadata') and 'url' in item.get('metadata', {}):
                    url_set.add(item['metadata']['url'])
        
        console.print(f"[green]Found {len(url_set)} unique URLs with knowledge chunks[/green]")
        
        # Mark URLs as processed
        if url_set:
            processed_count = await mark_urls_as_processed(url_set, supabase)
            console.print(f"[bold green]✅ Successfully marked {processed_count} URLs as processed[/bold green]")
        else:
            console.print("[bold yellow]No URLs found with knowledge chunks[/bold yellow]")
            
            # Alternate approach: Try to get URLs from the database
            console.print("[bold]Using alternate approach: Processing first URLs from database...[/bold]")
            url_result = supabase.table('holocron_urls').select('url').limit(checkpoint_data["processed_urls"]).execute()
            
            if url_result.data:
                urls_to_process = [item['url'] for item in url_result.data]
                console.print(f"[green]Found {len(urls_to_process)} URLs to mark as processed[/green]")
                
                # Mark URLs as processed
                processed_count = await mark_urls_as_processed(urls_to_process, supabase)
                console.print(f"[bold green]✅ Successfully marked {processed_count} URLs as processed[/bold green]")
            else:
                console.print("[bold red]Error: No URLs found in database[/bold red]")
            
    except Exception as e:
        console.print(f"[bold red]Error: {e}[/bold red]")

if __name__ == "__main__":
    asyncio.run(main()) 