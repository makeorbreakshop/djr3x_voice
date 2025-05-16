#!/usr/bin/env python3
"""
Improved RAG Example Script
Demonstrates the enhanced RAG capabilities with hybrid search, improved reranking, and query expansion.
"""

import os
import sys
import time
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Pre-download NLTK resources if not already downloaded
import nltk
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords', quiet=True)

# Import the enhanced RAG components
from scripts.pinecone_chat import PineconeChatInterface

# Load environment variables
load_dotenv()
console = Console()

async def run_improved_rag_demo():
    """Demonstrate the improved RAG capabilities with a series of queries."""
    
    # Initialize the chat interface
    console.print("[bold cyan]Initializing Pinecone Chat Interface...[/bold cyan]")
    chat = PineconeChatInterface()
    chat.initialize()
    
    # Example queries to demonstrate different improvements
    demos = [
        {
            "name": "Baseline Query",
            "query": "Who is Luke Skywalker?",
            "description": "Standard query to establish baseline performance."
        },
        {
            "name": "Automatic Metadata Filtering",
            "query": "Tell me about characters from the original trilogy.",
            "description": "Automatically detects era=original and content_type=character filters."
        },
        {
            "name": "Query Expansion",
            "query": "What weapons do the bad guys use?",
            "description": "Expands 'bad guys' to include 'sith' and 'weapon' to 'blaster'."
        },
        {
            "name": "Enhanced Reranking",
            "query": "What is the fastest ship in the galaxy?",
            "description": "Shows improved reranking that boosts results with matching terms."
        },
        {
            "name": "Follow-up Question Handling",
            "query": "Who piloted it?",
            "description": "Handles follow-up question by incorporating context from previous query."
        }
    ]
    
    # Run each demonstration
    for i, demo in enumerate(demos):
        console.print(f"\n[bold green]Demo {i+1}: {demo['name']}[/bold green]")
        console.print(f"Query: [italic cyan]{demo['query']}[/italic cyan]")
        console.print(f"[dim]{demo['description']}[/dim]\n")
        
        # Time the response
        start_time = time.time()
        response = await chat.generate_response(demo["query"])
        elapsed = time.time() - start_time
        
        # Display response
        markdown = Markdown(response)
        console.print(Panel(markdown, title=f"Response (in {elapsed:.2f}s)", border_style="green"))
        
        # Add a pause between requests
        if i < len(demos) - 1:
            console.print("[dim]Press Enter to continue to next demo...[/dim]")
            input()
    
    # Print performance report
    console.print("\n[bold]Performance Metrics:[/bold]")
    chat.print_latency_report()
    chat.print_token_usage_report()
    
    chat.close()
    console.print("\n[bold green]Demo completed![/bold green]")

def main():
    """Run the async demo."""
    try:
        asyncio.run(run_improved_rag_demo())
    except KeyboardInterrupt:
        console.print("\n[bold red]Demo interrupted by user.[/bold red]")
    except Exception as e:
        console.print(f"\n[bold red]Error: {str(e)}[/bold red]")

if __name__ == "__main__":
    main() 