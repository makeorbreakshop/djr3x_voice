#!/usr/bin/env python3
"""
Check Holocron Knowledge Base Status

This script checks the current status of the Holocron knowledge base in Supabase,
including URL collection stats and knowledge base content.
"""

import os
import sys
import asyncio
import logging
from dotenv import load_dotenv
from supabase import create_client, Client
from rich.console import Console
from rich.table import Table

# Add src directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rich console for pretty output
console = Console()

def check_status():
    """
    Check and report on the status of the Holocron knowledge system
    """
    console.print("[bold]🧪 Holocron Knowledge System Status[/bold]")
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
        
        # 1. Check URLs table
        console.print("\n[bold]📋 URL Collection Status[/bold]")
        
        # Get basic URL counts
        url_count_result = supabase.table('holocron_urls').select('*', count='exact').execute()
        total_urls = url_count_result.count if url_count_result.count is not None else 0
        
        # Get canonical URLs count
        canonical_result = supabase.table('holocron_urls').select('*', count='exact').eq('content_type', 'canonical').execute()
        canonical_urls = canonical_result.count if canonical_result.count is not None else 0
        
        # Get legends URLs count
        legends_result = supabase.table('holocron_urls').select('*', count='exact').eq('content_type', 'legends').execute()
        legends_urls = legends_result.count if legends_result.count is not None else 0
        
        # Get unknown URLs count (null or 'unknown')
        unknown_result = supabase.table('holocron_urls').select('*', count='exact').is_('content_type', 'null').execute()
        unknown_urls = unknown_result.count if unknown_result.count is not None else 0
        
        # Get processed URLs count
        processed_result = supabase.table('holocron_urls').select('*', count='exact').eq('is_processed', True).execute()
        processed_urls = processed_result.count if processed_result.count is not None else 0
        
        # Create URL status table
        url_table = Table(show_header=True)
        url_table.add_column("Total URLs", style="cyan")
        url_table.add_column("Canonical", style="green")
        url_table.add_column("Legends", style="yellow")
        url_table.add_column("Unknown", style="red")
        url_table.add_column("Processed", style="blue")
        
        url_table.add_row(
            str(total_urls), 
            str(canonical_urls),
            str(legends_urls),
            str(unknown_urls),
            str(processed_urls)
        )
        
        console.print(url_table)
        
        # Calculate percentage processed
        if total_urls > 0:
            percent_processed = (processed_urls / total_urls) * 100
            console.print(f"[blue]Percentage processed: {percent_processed:.2f}%[/blue]")
        
        # 2. Check knowledge base table
        console.print("\n[bold]🧠 Knowledge Base Status[/bold]")
        
        # Get knowledge base count
        kb_count_result = supabase.table('holocron_knowledge').select('*', count='exact').execute()
        kb_count = kb_count_result.count if kb_count_result.count is not None else 0
        
        # Get knowledge base samples
        kb_samples_result = supabase.table('holocron_knowledge').select('id,content,content_tokens').order('id').limit(5).execute()
        kb_samples = kb_samples_result.data if kb_samples_result.data else []
        
        # Create knowledge base table
        kb_table = Table(show_header=True)
        kb_table.add_column("Knowledge Chunks", style="cyan")
        kb_table.add_column("Avg. Tokens", style="blue")
        
        if kb_count > 0:
            # Calculate average tokens by getting token counts directly
            tokens_result = supabase.table('holocron_knowledge').select('content_tokens').execute()
            tokens_data = tokens_result.data
            
            if tokens_data:
                avg_tokens = sum(item['content_tokens'] for item in tokens_data) / len(tokens_data)
                kb_table.add_row(str(kb_count), f"{avg_tokens:.1f}")
            else:
                kb_table.add_row(str(kb_count), "N/A")
                
            console.print(kb_table)
            
            # Show samples
            console.print("\n[bold]📄 Sample Knowledge Chunks[/bold]")
            
            sample_table = Table(show_header=True)
            sample_table.add_column("ID", style="dim")
            sample_table.add_column("Content Sample", style="cyan")
            sample_table.add_column("Tokens", style="blue")
            
            for sample in kb_samples:
                # Truncate content for display
                content_sample = sample['content'][:100] + "..." if len(sample['content']) > 100 else sample['content']
                
                sample_table.add_row(
                    str(sample['id']),
                    content_sample,
                    str(sample['content_tokens'])
                )
            
            console.print(sample_table)
        else:
            kb_table.add_row("0", "N/A")
            console.print(kb_table)
            console.print("[yellow]No knowledge chunks found in the database[/yellow]")
        
        # 3. Check batch processing status
        try:
            console.print("\n[bold]🔄 Batch Processing Status[/bold]")
            
            # Look for checkpoint files
            checkpoint_dir = "data/checkpoints"
            if os.path.exists(checkpoint_dir) and os.path.isdir(checkpoint_dir):
                checkpoints = [f for f in os.listdir(checkpoint_dir) if f.startswith("batch_progress_") and f.endswith(".json")]
                
                if checkpoints:
                    # Sort by modification time to get the latest checkpoint
                    latest_checkpoint = max(
                        [os.path.join(checkpoint_dir, f) for f in checkpoints],
                        key=os.path.getmtime
                    )
                    
                    # Read the checkpoint file
                    import json
                    with open(latest_checkpoint, 'r') as f:
                        checkpoint_data = json.load(f)
                    
                    # Create checkpoint table
                    checkpoint_table = Table(show_header=True)
                    checkpoint_table.add_column("Checkpoint File", style="dim")
                    checkpoint_table.add_column("Total URLs", style="cyan")
                    checkpoint_table.add_column("Processed", style="green")
                    checkpoint_table.add_column("Failed", style="red")
                    checkpoint_table.add_column("Last Updated", style="blue")
                    
                    checkpoint_table.add_row(
                        os.path.basename(latest_checkpoint),
                        str(checkpoint_data.get("total_urls", 0)),
                        str(checkpoint_data.get("processed_urls", 0)),
                        str(len(checkpoint_data.get("failed_urls", []))),
                        checkpoint_data.get("last_checkpoint", "N/A")
                    )
                    
                    console.print(checkpoint_table)
                else:
                    console.print("[yellow]No checkpoint files found[/yellow]")
            else:
                console.print("[yellow]Checkpoint directory not found[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Error checking batch status: {e}[/yellow]")
        
    except Exception as e:
        console.print(f"[bold red]Error connecting to Supabase: {e}[/bold red]")

def main():
    """Main entry point"""
    check_status()

if __name__ == "__main__":
    main() 