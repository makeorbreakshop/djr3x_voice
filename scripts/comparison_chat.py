#!/usr/bin/env python3
"""
Optimized comparison chat for evaluating Pinecone RAG vs. pure LLM responses.
Streamlined for maximum performance with direct API calls.
"""

import os
import sys
import time
import asyncio
import re
import nltk
from dataclasses import dataclass
from typing import Dict, List, Any, Tuple, Optional
from collections import Counter
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Add the project root to Python path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.pinecone_chat import VectorSearchResult, logger

# Load environment variables
load_dotenv()
console = Console()

# Initialize NLTK resources silently, downloading if needed
try:
    nltk.data.find('tokenizers/punkt')
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('punkt', quiet=True)
    nltk.download('stopwords', quiet=True)

@dataclass
class TimingMetrics:
    """Store timing information for performance analysis."""
    embedding_time: float = 0.0
    vector_search_time: float = 0.0
    reranking_time: float = 0.0
    llm_time: float = 0.0
    total_time: float = 0.0

class OptimizedComparisonChat:
    """Streamlined chat interface to compare RAG vs. pure LLM responses."""
    
    def __init__(self):
        # Initialize OpenAI client
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("Missing OpenAI API key")
        
        # Single async client with optimized settings
        self.client = AsyncOpenAI(
            api_key=openai_api_key,
            timeout=30.0,
            max_retries=2
        )
        
        # Initialize Pinecone connection
        self.initialize_pinecone()
        
        # Configuration
        self.embedding_model = "text-embedding-ada-002"  # Match existing Pinecone embeddings
        self.chat_model = "gpt-4.1-mini"
        self.max_tokens = 300
        self.similarity_threshold = 0.01
        
        # Performance tracking
        self.queries = []
        self.rag_metrics = []
        self.llm_metrics = []
        self.winner_tracking = []
        
        # Star Wars terms for query expansion
        self.starwars_aliases = {
            "lightsaber": ["lightsabre", "laser sword", "energy sword"],
            "jedi": ["force user", "jedi knight", "jedi master"],
            "sith": ["dark jedi", "dark side user"],
            "droid": ["robot", "android", "mechanical being"],
            "blaster": ["gun", "weapon", "pistol"],
            "coruscant": ["galactic center", "galactic capital"],
            "tatooine": ["outer rim", "desert planet", "twin suns"],
        }
        
        # Conversation history for follow-up questions
        self.conversation_history = []
        
        # DJ R3X personality
        self.personality = """You are DJ R3X, a droid DJ from Star Wars Galaxy's Edge. 
You're hip, excitable, and love to use DJ slang and Star Wars references in your responses.
Always stay in character as DJ R3X when responding."""
        
        self.response_instruction = "Provide a detailed response with specific facts in a conversational DJ R3X style."
        
        logger.info(f"Initialized with embedding model: {self.embedding_model}, chat model: {self.chat_model}")
    
    def initialize_pinecone(self):
        """Initialize Pinecone connection."""
        try:
            from pinecone import Pinecone, PodSpec
            
            pinecone_api_key = os.getenv("PINECONE_API_KEY")
            if not pinecone_api_key:
                raise ValueError("Missing Pinecone API key")
            
            self.pc = Pinecone(api_key=pinecone_api_key)
            
            # Connect to existing index
            self.index_name = "holocron-knowledge"
            self.index = self.pc.Index(self.index_name)
            
            # Get index stats 
            stats = self.index.describe_index_stats()
            logger.info(f"Connected to Pinecone index: {self.index_name}")
            logger.info(f"Index stats: {stats}")
            logger.info(f"Total vectors: {stats.total_vector_count}")
            
        except Exception as e:
            logger.error(f"Failed to initialize Pinecone: {e}")
            raise
    
    def _expand_query(self, query: str) -> str:
        """Expand query with Star Wars specific terms and aliases."""
        expanded_query = query
        
        # Look for known terms that have aliases
        for term, aliases in self.starwars_aliases.items():
            if term.lower() in query.lower():
                # No need to expand if already using the canonical term
                continue
                
            # Check if any aliases are in the query
            for alias in aliases:
                if alias.lower() in query.lower():
                    # Replace the alias with the canonical term in the query
                    # but keep the original query intact
                    expanded_query += f" {term}"
                    break
        
        # Check for follow-up questions without context
        if len(expanded_query.split()) < 7 and not any(x in expanded_query.lower() for x in ["who", "what", "where", "when", "how", "why"]):
            # This might be a follow-up question, add context from last query if available
            if self.conversation_history and len(self.conversation_history) > 0:
                last_query = self.conversation_history[-1]
                # Extract key entities from the last query
                expanded_query = f"{expanded_query} context: {last_query}"
        
        return expanded_query
    
    def _detect_metadata_filters(self, query: str) -> Dict[str, Any]:
        """Detect potential metadata filters based on the query content."""
        filters = {}
        
        # Check for era indicators
        if any(term in query.lower() for term in ["prequel", "clone wars", "republic", "before empire"]):
            filters["era"] = "prequel"
        elif any(term in query.lower() for term in ["original trilogy", "rebellion", "empire", "imperial"]):
            filters["era"] = "original"
        elif any(term in query.lower() for term in ["sequel", "first order", "resistance", "after empire"]):
            filters["era"] = "sequel"
        
        # Check for canon vs legends indicators
        if "legends" in query.lower() or "expanded universe" in query.lower():
            filters["is_canon"] = False
        elif "canon" in query.lower() or "official" in query.lower():
            filters["is_canon"] = True
            
        # Check for content type filtering
        if any(term in query.lower() for term in ["character", "person", "who is"]):
            filters["content_type"] = "character"
        elif any(term in query.lower() for term in ["planet", "moon", "world", "where is"]):
            filters["content_type"] = "location"
        elif any(term in query.lower() for term in ["ship", "vehicle", "transport"]):
            filters["content_type"] = "vehicle"
            
        return filters
    
    # Sparse vector generation functionality has been removed
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding using OpenAI API."""
        start_time = time.time()
        
        response = await self.client.embeddings.create(
            model=self.embedding_model,
            input=text
        )
        
        elapsed = time.time() - start_time
        logger.info(f"Embedding generated in {elapsed:.2f}s")
        
        return response.data[0].embedding
    
    async def query_pinecone(self, embedding: List[float], filters: Dict[str, Any] = None, top_k: int = 25) -> Dict[str, Any]:
        """Query Pinecone index with dense vector search."""
        start_time = time.time()
        
        # Prepare filter if any provided
        filter_dict = None
        if filters:
            filter_dict = {}
            for key, value in filters.items():
                filter_dict[key] = {"$eq": value}
        
        # Dense-only search
        results = await asyncio.to_thread(
            self.index.query,
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
            filter=filter_dict
        )
        
        elapsed = time.time() - start_time
        logger.info(f"Pinecone query completed in {elapsed:.2f}s")
        
        return results
    
    def rerank_results(self, results: List[Dict[str, Any]], query: str, top_n: int = 5) -> List[VectorSearchResult]:
        """Enhanced reranking of results based on multiple criteria."""
        start_time = time.time()
        
        # Convert to VectorSearchResult objects
        vector_results = []
        for match in results.matches:
            if match.score >= self.similarity_threshold:
                result = VectorSearchResult(
                    id=int(match.id) if match.id.isdigit() else 0,
                    content=match.metadata.get('content', ''),
                    metadata=match.metadata,
                    similarity=match.score
                )
                vector_results.append(result)
        
        # Process query for term matching
        query_lower = query.lower()
        query_terms = set(word_tokenize(query_lower))
        stop_words = set(stopwords.words('english'))
        query_terms = {term for term in query_terms if term not in stop_words and len(term) > 2}
        
        def count_term_matches(text: str) -> float:
            """Count how many query terms appear in the text."""
            if not text or not query_terms:
                return 0
                
            text_lower = text.lower()
            matched_terms = sum(1 for term in query_terms if term in text_lower)
            return matched_terms / len(query_terms) if query_terms else 0
        
        # Enhance scores based on multiple criteria
        for result in vector_results:
            content = result.content
            metadata = result.metadata
            
            # Base score is the similarity score
            score = result.similarity
            
            # 1. Boost based on exact term matches (lexical relevance)
            term_match_score = count_term_matches(content) * 0.3
            
            # 2. Boost titles containing query terms
            title = metadata.get('title', '')
            title_match = count_term_matches(title) * 0.2
            
            # 3. Boost canonical content over non-canonical (if known)
            is_canon = metadata.get('is_canon', None)
            canon_boost = 0.1 if is_canon is True else 0
            
            # 4. Small boost for larger chunks (may contain more context)
            size_boost = min(0.05, len(content) / 50000)
            
            # Calculate final rerank score
            final_score = score + term_match_score + title_match + canon_boost + size_boost
            
            # Cap at 1.0 and store
            result.rerank_score = min(1.0, final_score)
        
        # Sort by rerank score
        reranked = sorted(vector_results, key=lambda x: x.rerank_score, reverse=True)[:top_n]
        
        # Log reranking metrics
        if vector_results:
            avg_before = sum(r.similarity for r in vector_results) / len(vector_results)
            avg_after = sum(r.rerank_score for r in reranked) / len(reranked) if reranked else 0
            logger.info(f"Reranking {len(vector_results)} results")
            logger.info(f"Average similarity before reranking: {avg_before:.4f}")
            logger.info(f"Average similarity after reranking: {avg_after:.4f}")
        
        elapsed = time.time() - start_time
        logger.info(f"Latency for reranking: {elapsed:.4f}s")
        
        return reranked
    
    async def get_rag_response(self, query: str) -> Tuple[str, TimingMetrics]:
        """Get response using RAG with Pinecone."""
        metrics = TimingMetrics()
        start_time = time.time()
        
        # Save query to conversation history
        self.conversation_history.append(query)
        if len(self.conversation_history) > 5:
            self.conversation_history.pop(0)
        
        # Process and expand the query
        expanded_query = self._expand_query(query)
        if expanded_query != query:
            logger.info(f"Expanded query: '{query}' → '{expanded_query}'")
        
        # Auto-detect metadata filters
        metadata_filters = self._detect_metadata_filters(query)
        if metadata_filters:
            logger.info(f"Auto-detected filters: {metadata_filters}")
        
        # Step 1: Generate dense embedding
        embedding_start = time.time()
        embedding = await self.generate_embedding(expanded_query)
        metrics.embedding_time = time.time() - embedding_start
        
        # Step 2: Query Pinecone
        search_start = time.time()
        results = await self.query_pinecone(
            embedding=embedding,
            filters=metadata_filters
        )
        metrics.vector_search_time = time.time() - search_start
        
        # Step 3: Rerank results
        rerank_start = time.time()
        reranked = self.rerank_results(results, query)
        metrics.reranking_time = time.time() - rerank_start
        
        # Step 4: Format context and sources
        context_items = []
        source_info = []
        
        for i, item in enumerate(reranked):
            title = item.metadata.get('title', 'Untitled')
            url = item.metadata.get('url', '')
            
            # Format context
            context_items.append(
                f"HOLOCRON ENTRY {i+1}:\n"
                f"TITLE: {title}\n"
                f"CONTENT: {item.content[:500]}"
            )
            
            # Format source
            source_info.append(f"[{i+1}] {title}: {url}" if url else f"[{i+1}] {title}")
        
        context = "\n\n".join(context_items)
        sources_text = "\n\nSources:\n" + "\n".join(source_info) if source_info else ""
        
        # Step 5: Generate response using OpenAI
        llm_start = time.time()
        system_message = (
            f"Here's relevant information from the Holocron:\n{context}\n\n"
            f"{self.response_instruction}\n\n"
            "After your answer, include footnote numbers [1], [2], etc. to reference "
            "specific facts from these sources."
        ) if reranked else (
            "No specific information found in the Holocron. "
            "Respond based on your general knowledge of Star Wars, but mention that "
            "the Holocron data banks might be incomplete."
        )
        
        response = await self.client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {"role": "system", "content": self.personality},
                {"role": "system", "content": system_message},
                {"role": "user", "content": query}
            ],
            max_tokens=self.max_tokens,
            temperature=0.7
        )
        
        metrics.llm_time = time.time() - llm_start
        
        # Step 6: Format final response
        answer = response.choices[0].message.content
        full_response = f"{answer}{sources_text}" if sources_text else answer
        
        metrics.total_time = time.time() - start_time
        return full_response, metrics
    
    async def get_llm_response(self, query: str) -> Tuple[str, TimingMetrics]:
        """Get response from pure LLM without RAG."""
        metrics = TimingMetrics()
        start_time = time.time()
        
        response = await self.client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {"role": "system", "content": self.personality},
                {"role": "user", "content": query}
            ],
            max_tokens=self.max_tokens,
            temperature=0.7
        )
        
        metrics.llm_time = time.time() - start_time
        metrics.total_time = time.time() - start_time
        
        return response.choices[0].message.content, metrics
    
    def print_comparison_stats(self):
        """Print comparison statistics for RAG vs. LLM."""
        if not self.queries:
            console.print("[yellow]No queries recorded yet.[/yellow]")
            return
        
        # Performance table
        perf_table = Table(title="Performance Comparison")
        perf_table.add_column("Metric", style="cyan")
        perf_table.add_column("RAG (avg)", style="green")
        perf_table.add_column("LLM (avg)", style="blue")
        
        # Calculate average metrics
        avg_rag_metrics = TimingMetrics(
            embedding_time=sum(m.embedding_time for m in self.rag_metrics) / len(self.rag_metrics),
            vector_search_time=sum(m.vector_search_time for m in self.rag_metrics) / len(self.rag_metrics),
            reranking_time=sum(m.reranking_time for m in self.rag_metrics) / len(self.rag_metrics),
            llm_time=sum(m.llm_time for m in self.rag_metrics) / len(self.rag_metrics),
            total_time=sum(m.total_time for m in self.rag_metrics) / len(self.rag_metrics)
        )
        
        avg_llm_metrics = TimingMetrics(
            llm_time=sum(m.llm_time for m in self.llm_metrics) / len(self.llm_metrics),
            total_time=sum(m.total_time for m in self.llm_metrics) / len(self.llm_metrics)
        )
        
        # Add performance metrics rows
        perf_table.add_row("Embedding Generation", f"{avg_rag_metrics.embedding_time:.2f}s", "N/A")
        perf_table.add_row("Vector Search", f"{avg_rag_metrics.vector_search_time:.2f}s", "N/A")
        perf_table.add_row("Reranking", f"{avg_rag_metrics.reranking_time:.2f}s", "N/A")
        perf_table.add_row("LLM Completion", f"{avg_rag_metrics.llm_time:.2f}s", f"{avg_llm_metrics.llm_time:.2f}s")
        perf_table.add_row("Total Response Time", f"{avg_rag_metrics.total_time:.2f}s", f"{avg_llm_metrics.total_time:.2f}s")
        
        # Winner statistics table
        total_comparisons = len(self.winner_tracking)
        if total_comparisons == 0:
            console.print(perf_table)
            return
            
        rag_wins = self.winner_tracking.count("RAG")
        llm_wins = self.winner_tracking.count("LLM")
        ties = self.winner_tracking.count("TIE")
        
        winner_table = Table(title="Response Quality Comparison")
        winner_table.add_column("Approach", style="cyan")
        winner_table.add_column("Wins", style="green")
        winner_table.add_column("Win Rate", style="blue")
        
        winner_table.add_row("RAG", str(rag_wins), f"{(rag_wins/total_comparisons)*100:.1f}%")
        winner_table.add_row("LLM", str(llm_wins), f"{(llm_wins/total_comparisons)*100:.1f}%")
        winner_table.add_row("Tie", str(ties), f"{(ties/total_comparisons)*100:.1f}%")
        
        console.print(perf_table)
        console.print("\n")
        console.print(winner_table)
    
    async def run(self):
        """Run the interactive comparison chat."""
        console.print("[bold green]DJ R3X Holocron Knowledge System - Performance Comparison[/bold green]")
        console.print("[yellow]Enter your questions to compare RAG vs. pure LLM responses.[/yellow]")
        console.print("[yellow]Type 'exit' to quit, 'stats' to see performance metrics.[/yellow]\n")
        
        while True:
            try:
                query = input("\n[bold cyan]Ask DJ R3X a question:[/bold cyan] ")
                
                if query.lower() == 'exit':
                    break
                elif query.lower() == 'stats':
                    self.print_comparison_stats()
                    continue
                
                self.queries.append(query)
                
                # Get both responses in parallel
                console.print("[dim]Processing...[/dim]")
                overall_start = time.time()
                
                # Use gather to run both responses in parallel
                rag_future = self.get_rag_response(query)
                llm_future = self.get_llm_response(query)
                results = await asyncio.gather(
                    rag_future, llm_future
                )
                
                # Unpack results from gather
                (rag_response, rag_metrics), (llm_response, llm_metrics) = results
                
                # Calculate and display timing
                overall_time = time.time() - overall_start
                console.print(f"[dim]Total time: {overall_time:.2f}s (Parallel execution saved "
                              f"{rag_metrics.total_time + llm_metrics.total_time - overall_time:.2f}s)[/dim]")
                
                # Store metrics
                self.rag_metrics.append(rag_metrics)
                self.llm_metrics.append(llm_metrics)
                
                # Display responses
                console.print("\n[bold green]RAG Response:[/bold green]")
                console.print(Panel(Markdown(rag_response)))
                console.print(f"[dim]Time: {rag_metrics.total_time:.2f}s[/dim]")
                
                console.print("\n[bold blue]Pure LLM Response:[/bold blue]")
                console.print(Panel(Markdown(llm_response)))
                console.print(f"[dim]Time: {llm_metrics.total_time:.2f}s[/dim]")
                
                # Ask for winner
                while True:
                    winner = input("\nWhich response was better? (rag/llm/tie): ").lower()
                    if winner in ['rag', 'llm', 'tie']:
                        self.winner_tracking.append(winner.upper())
                        break
                    console.print("[red]Please enter 'rag', 'llm', or 'tie'[/red]")
                
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted by user. Type 'exit' to quit.[/yellow]")
                continue
            except Exception as e:
                console.print(f"[red]Error: {str(e)}[/red]")
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                continue

async def main():
    """Main entry point."""
    chat = OptimizedComparisonChat()
    await chat.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Goodbye![/yellow]")
    except Exception as e:
        console.print(f"[red]Fatal error: {str(e)}[/red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1) 