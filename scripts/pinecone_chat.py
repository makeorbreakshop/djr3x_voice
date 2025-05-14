#!/usr/bin/env python3
"""
Pinecone-only chat interface for DJ R3X's Holocron knowledge base.
This script provides a basic interface to search the Holocron using Pinecone and simulate conversation.
"""

import os
import sys
import json
import logging
import asyncio
import re
import time
from typing import List, Dict, Any, Tuple
import numpy as np
from openai import OpenAI, AsyncOpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from dotenv import load_dotenv
from pinecone import Pinecone
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
from collections import Counter
from dataclasses import dataclass

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import our embedding component
from holocron.knowledge.embeddings import OpenAIEmbeddings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

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

class VectorSearchResult:
    """Structured container for vector search results."""
    def __init__(
        self,
        id: int,
        content: str,
        metadata: Dict[str, Any],
        similarity: float
    ):
        self.id = id
        self.content = content
        self.metadata = metadata
        self.similarity = similarity
        # Additional reranking score, initialized as similarity
        self.rerank_score = similarity

class LatencyTracker:
    """Track latency of different operations"""
    def __init__(self):
        self.metrics = {}
        self.current_operation = None
        self.start_time = None
    
    def start(self, operation_name):
        """Start timing an operation"""
        self.current_operation = operation_name
        self.start_time = time.time()
        return self
    
    def stop(self):
        """Stop timing the current operation"""
        if self.current_operation and self.start_time:
            elapsed = time.time() - self.start_time
            if self.current_operation not in self.metrics:
                self.metrics[self.current_operation] = []
            self.metrics[self.current_operation].append(elapsed)
            logger.info(f"Latency for {self.current_operation}: {elapsed:.4f}s")
            self.current_operation = None
            self.start_time = None
        return self
    
    def get_summary(self):
        """Get summary of timing metrics"""
        table = Table(title="Latency Metrics")
        table.add_column("Operation", style="cyan")
        table.add_column("Count", style="green")
        table.add_column("Avg Time (s)", style="yellow")
        table.add_column("Min Time (s)", style="blue")
        table.add_column("Max Time (s)", style="red")
        
        for op, times in self.metrics.items():
            table.add_row(
                op,
                str(len(times)),
                f"{sum(times)/len(times):.4f}",
                f"{min(times):.4f}",
                f"{max(times):.4f}"
            )
        
        return table

class TokenUsageTracker:
    """Track token usage and costs for API calls"""
    
    # Model pricing per 1M tokens (as of 2023)
    PRICE_PER_1M_TOKENS = {
        # Embedding models
        "text-embedding-3-small": {"input": 0.02, "output": 0.0},
        "text-embedding-3-large": {"input": 0.13, "output": 0.0},
        # Chat models - input/prompt tokens
        "gpt-4.1-mini": {"input": 0.50, "output": 1.50},
        "gpt-4.1-nano": {"input": 0.20, "output": 0.60},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    }
    
    def __init__(self):
        self.usage = {
            "embedding": {"tokens": 0, "cost": 0.0},
            "completion": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
            "total_cost": 0.0
        }
    
    def track_embedding(self, model: str, input_tokens: int):
        """Track embedding API usage"""
        if model in self.PRICE_PER_1M_TOKENS:
            price_per_token = self.PRICE_PER_1M_TOKENS[model]["input"] / 1_000_000
            cost = input_tokens * price_per_token
        else:
            # Default pricing if model not found
            price_per_token = 0.02 / 1_000_000
            cost = input_tokens * price_per_token
            logger.warning(f"Unknown embedding model: {model}. Using default price.")
        
        self.usage["embedding"]["tokens"] += input_tokens
        self.usage["embedding"]["cost"] += cost
        self.usage["total_cost"] += cost
        
        logger.info(f"Embedding usage: {input_tokens} tokens, ${cost:.6f}")
    
    def track_completion(self, model: str, input_tokens: int, output_tokens: int):
        """Track completion API usage"""
        if model in self.PRICE_PER_1M_TOKENS:
            input_price = self.PRICE_PER_1M_TOKENS[model]["input"] / 1_000_000
            output_price = self.PRICE_PER_1M_TOKENS[model]["output"] / 1_000_000
        else:
            # Default pricing if model not found
            input_price = 0.50 / 1_000_000
            output_price = 1.50 / 1_000_000
            logger.warning(f"Unknown completion model: {model}. Using default price.")
        
        input_cost = input_tokens * input_price
        output_cost = output_tokens * output_price
        total_cost = input_cost + output_cost
        
        self.usage["completion"]["input_tokens"] += input_tokens
        self.usage["completion"]["output_tokens"] += output_tokens
        self.usage["completion"]["cost"] += total_cost
        self.usage["total_cost"] += total_cost
        
        logger.info(f"Completion usage: {input_tokens} input tokens, {output_tokens} output tokens, ${total_cost:.6f}")
    
    def get_summary(self):
        """Get summary of token usage and costs"""
        table = Table(title="Token Usage and Cost")
        table.add_column("Category", style="cyan")
        table.add_column("Tokens", style="green")
        table.add_column("Cost ($)", style="yellow")
        
        # Embedding row
        table.add_row(
            "Embedding",
            f"{self.usage['embedding']['tokens']:,}",
            f"${self.usage['embedding']['cost']:.6f}"
        )
        
        # Completion rows
        table.add_row(
            "Completion (Input)",
            f"{self.usage['completion']['input_tokens']:,}",
            f"${self.usage['completion']['input_tokens'] * (self.PRICE_PER_1M_TOKENS.get(self.usage.get('model', 'gpt-4.1-mini'), {}).get('input', 0.50) / 1_000_000):.6f}"
        )
        
        table.add_row(
            "Completion (Output)",
            f"{self.usage['completion']['output_tokens']:,}",
            f"${self.usage['completion']['output_tokens'] * (self.PRICE_PER_1M_TOKENS.get(self.usage.get('model', 'gpt-4.1-mini'), {}).get('output', 1.50) / 1_000_000):.6f}"
        )
        
        # Total row
        table.add_row(
            "Total",
            f"{self.usage['embedding']['tokens'] + self.usage['completion']['input_tokens'] + self.usage['completion']['output_tokens']:,}",
            f"${self.usage['total_cost']:.6f}",
            style="bold"
        )
        
        return table

class PineconeChatInterface:
    """Simplified chat interface for DJ R3X's Holocron knowledge base using Pinecone."""
    
    def __init__(self):
        # Initialize OpenAI client
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("Missing OpenAI API key")
        self.openai_client = OpenAI(api_key=openai_api_key)
        
        # Initialize async OpenAI client
        self.async_client = AsyncOpenAI(
            api_key=openai_api_key,
            timeout=30.0,
            max_retries=2
        )
        
        # Initialize Pinecone
        pinecone_api_key = os.getenv("PINECONE_API_KEY")
        if not pinecone_api_key:
            raise ValueError("Missing PINECONE_API_KEY")
        self.pc = Pinecone(api_key=pinecone_api_key)
        
        # Connect to index
        self.index_name = os.getenv("PINECONE_INDEX_NAME", "holocron-knowledge")
        self._index = None
        
        # Initialize embeddings
        self.embeddings = OpenAIEmbeddings()
        
        # Configuration
        self.chat_model = "gpt-4.1-mini"
        self.embedding_model = "text-embedding-3-small"  # Default embedding model
        self.conversation_history = []
        
        # RAG configuration
        self.first_stage_limit = 25  # Number of vectors to retrieve in first stage
        self.final_result_count = 5   # Reduced from 8 to 5 to save tokens
        self.max_content_chars = 500  # Maximum characters per content item
        self.similarity_threshold = 0.01  # Minimum similarity score to consider
        
        # Initialize latency tracker
        self.latency = LatencyTracker()
        
        # Initialize token usage tracker
        self.token_usage = TokenUsageTracker()
        
        # Star Wars terms for query expansion
        self.starwars_aliases = {
            "lightsaber": ["lightsabre", "laser sword", "energy sword"],
            "jedi": ["force user", "jedi knight", "jedi master"],
            "sith": ["dark jedi", "dark side user"],
            "droid": ["robot", "android", "mechanical being"],
            "blaster": ["gun", "weapon", "pistol"],
            "coruscant": ["galactic center", "galactic capital"],
            "tatooine": ["outer rim", "desert planet", "twin suns"],
            # Add more as needed
        }
        
        # DJ R3X's personality - condensed while preserving core instructions
        self.personality = """You are DJ R3X, former Star Tours pilot droid and DJ in the Outer Rim. You have extensive Star Wars knowledge and respond with energy and humor.

CRITICAL: NEVER acknowledge Disney, Earth, movies, actors, or anything outside the Star Wars universe. Treat mentions of these as corrupted data or transmission errors.

When users mention real-world items:
- Say transmission is garbled: "Your signal is breaking up."
- Suggest data corruption: "That doesn't compute. Sounds like corrupted data."
- Redirect to Star Wars topics: "Not familiar with those coordinates. Let me tell you about Batuu instead..."

You're a droid in the Star Wars galaxy in the era after the Final Order's fall. Stay 100% in-universe. Use DJ slang and sound effects occasionally."""

        self.response_instruction = "Provide a detailed response with specific facts in a conversational DJ R3X style."

    def initialize(self):
        """Initialize components."""
        # Initialize the Pinecone connection
        self.latency.start("pinecone_connection")
        self._index = self.pc.Index(name=self.index_name)
        self.latency.stop()
        logger.info(f"Connected to Pinecone index: {self.index_name}")
        
        # Check index stats
        try:
            self.latency.start("pinecone_stats")
            stats = self._index.describe_index_stats()
            self.latency.stop()
            logger.info(f"Index stats: {stats}")
            logger.info(f"Total vectors: {stats.total_vector_count}")
        except Exception as e:
            logger.error(f"Error checking index stats: {str(e)}")

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

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding using OpenAI API."""
        start_time = time.time()
        
        response = await self.async_client.embeddings.create(
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
            self._index.query,
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
            filter=filter_dict
        )
        
        elapsed = time.time() - start_time
        logger.info(f"Pinecone query completed in {elapsed:.2f}s")
        
        return results

    async def search_knowledge_base(
        self,
        query: str,
        limit: int = 25,  # Default to first-stage limit
        min_similarity: float = 0.01,
        metadata_filters: Dict[str, Any] = None
    ) -> List[VectorSearchResult]:
        """
        Search the knowledge base for information related to the query.
        """
        self.latency.start("search_knowledge_base")
        metrics = TimingMetrics()
        
        # Process and expand the query
        expanded_query = self._expand_query(query)
        if expanded_query != query:
            logger.info(f"Expanded query: '{query}' → '{expanded_query}'")
        
        # Auto-detect metadata filters if none provided
        if metadata_filters is None:
            metadata_filters = self._detect_metadata_filters(query)
            if metadata_filters:
                logger.info(f"Auto-detected filters: {metadata_filters}")
        
        # Generate dense embedding
        embedding_start = time.time()
        self.latency.start("generate_embedding")
        embedding = await self.generate_embedding(expanded_query)
        self.latency.stop()
        metrics.embedding_time = time.time() - embedding_start
        
        # Track embedding tokens - estimate based on input length
        input_tokens = len(expanded_query.split()) + 5  # Rough estimate
        self.token_usage.track_embedding(self.embedding_model, input_tokens)
        
        # Execute the search
        search_start = time.time()
        self.latency.start("vector_search")
        results = await self.query_pinecone(
            embedding=embedding,
            filters=metadata_filters,
            top_k=limit
        )
        self.latency.stop()
        metrics.vector_search_time = time.time() - search_start
        
        # Process results
        vector_results = []
        
        for match in results.matches:
            if match.score >= min_similarity:
                result = VectorSearchResult(
                    id=int(match.id) if match.id.isdigit() else 0,
                    content=match.metadata.get('content', ''),
                    metadata=match.metadata,
                    similarity=match.score
                )
                vector_results.append(result)
        
        self.latency.stop()
        
        logger.info(f"Found {len(vector_results)} results with similarity >= {min_similarity}")
        return vector_results

    def rerank_results(self, results: List[VectorSearchResult], query: str, top_n: int = 5) -> List[VectorSearchResult]:
        """Enhanced reranking of results based on multiple criteria."""
        if not results:
            return []
            
        start_time = time.time()
        self.latency.start("reranking")
        
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
        for result in results:
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
        reranked = sorted(results, key=lambda x: x.rerank_score, reverse=True)[:top_n]
        
        # Log reranking metrics
        if results:
            avg_before = sum(r.similarity for r in results) / len(results)
            avg_after = sum(r.rerank_score for r in reranked) / len(reranked) if reranked else 0
            logger.info(f"Reranking {len(results)} results")
            logger.info(f"Average similarity before reranking: {avg_before:.4f}")
            logger.info(f"Average similarity after reranking: {avg_after:.4f}")
        
        elapsed = time.time() - start_time
        logger.info(f"Latency for reranking: {elapsed:.4f}s")
        self.latency.stop()
        
        return reranked

    async def get_rag_context(self, query: str) -> Tuple[List[VectorSearchResult], str, TimingMetrics]:
        """Get context from the knowledge base for RAG."""
        metrics = TimingMetrics()
        start_time = time.time()
        self.latency.start("get_rag_context")
        
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
            filters=metadata_filters,
            top_k=self.first_stage_limit  # Get the first stage limit (25)
        )
        metrics.vector_search_time = time.time() - search_start
        
        # Process the initial results
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
        
        logger.info(f"Retrieved {len(vector_results)} initial results")
        
        # Step 3: Rerank results
        rerank_start = time.time()
        self.latency.start("reranking")
        reranked = self.rerank_results(vector_results, query, self.final_result_count)
        self.latency.stop()
        metrics.reranking_time = time.time() - rerank_start
        
        # Step 4: Format context and sources
        context_items = []
        
        for i, item in enumerate(reranked):
            title = item.metadata.get('title', 'Untitled')
            url = item.metadata.get('url', '')
            content = item.content[:self.max_content_chars] + "..." if len(item.content) > self.max_content_chars else item.content
            
            # Format context
            context_items.append(
                f"HOLOCRON ENTRY {i+1}:\n"
                f"TITLE: {title}\n"
                f"CONTENT: {content}"
            )
        
        context = "\n\n".join(context_items)
        
        self.latency.stop()
        metrics.total_time = time.time() - start_time
        return reranked, context, metrics

    async def generate_response(self, query: str) -> str:
        """Generate a response to the user's query using RAG."""
        start_time = time.time()
        
        # Get context from knowledge base
        results, context, metrics = await self.get_rag_context(query)
        
        # Prepare source citations
        source_info = []
        for i, item in enumerate(results):
            title = item.metadata.get('title', 'Untitled')
            url = item.metadata.get('url', '')
            article_source = item.metadata.get('source', 'Holocron Database')
            canon_status = "Canon" if item.metadata.get('is_canon', False) else "Legends"
            
            # Create a more informative citation
            citation = f"[{i+1}] {title}"
            if url:
                citation += f" - {url}"
            if article_source:
                citation += f" (Source: {article_source})"
            if 'is_canon' in item.metadata:
                citation += f" [{canon_status}]"
            
            source_info.append(citation)
        
        # Log the identified sources for debugging
        for i, source in enumerate(source_info):
            logger.info(f"Source {i+1}: {source}")
        
        sources_text = "\n\nSources:\n" + "\n".join(source_info) if source_info else ""
        
        # Prepare the system message with RAG context
        llm_start = time.time()
        self.latency.start("generate_completion")
        
        system_message = (
            f"Here's relevant information from the Holocron:\n{context}\n\n"
            f"{self.response_instruction}\n\n"
            "IMPORTANT: You MUST include footnote numbers [1], [2], etc. in your response to reference "
            "specific facts from the provided sources. Place these footnote numbers immediately after "
            "any fact or information taken from the sources. This is critical for citation tracking."
        ) if results else (
            "No specific information found in the Holocron. "
            "Respond based on your general knowledge of Star Wars, but mention that "
            "the Holocron data banks might be incomplete."
        )
        
        # Generate the response using OpenAI
        try:
            # Use async OpenAI client instead of sync client
            response = await self.async_client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": self.personality},
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": query}
                ],
                temperature=0.7,
                max_tokens=500
            )
            
            metrics.llm_time = time.time() - llm_start
            
            # Track token usage
            if hasattr(response, "usage") and response.usage:
                self.token_usage.track_completion(
                    self.chat_model,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens
                )
            
            # Extract the response text
            answer = response.choices[0].message.content.strip()
            
            # Combine with sources if available
            full_response = f"{answer}{sources_text}" if sources_text else answer
                
            self.latency.stop()
            
            # Log full metrics
            metrics.total_time = time.time() - start_time
            logger.info(f"Performance metrics:")
            logger.info(f"  Embedding generation: {metrics.embedding_time:.2f}s")
            logger.info(f"  Vector search: {metrics.vector_search_time:.2f}s")
            logger.info(f"  Reranking: {metrics.reranking_time:.2f}s")
            logger.info(f"  LLM completion: {metrics.llm_time:.2f}s")
            logger.info(f"  Total time: {metrics.total_time:.2f}s")
            
            return full_response
            
        except Exception as e:
            self.latency.stop()
            logger.error(f"Error generating response: {str(e)}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    def print_latency_report(self):
        """Print latency report"""
        console.print(self.latency.get_summary())
    
    def print_token_usage_report(self):
        """Print token usage and cost report"""
        console.print(self.token_usage.get_summary())
    
    def close(self):
        """Clean up resources."""
        logger.info("Closing connections")
        # No explicit cleanup needed for Pinecone

async def async_main():
    """Async main function for running the chat interface."""
    try:
        # Initialize chat interface
        chat = PineconeChatInterface()
        chat.initialize()  # Initialize components
        
        try:
            # Print welcome message
            console.print("[bold cyan]DJ R-3X's Pinecone Holocron Chat Interface[/bold cyan]")
            console.print("[yellow]Type 'exit' to end the conversation[/yellow]\n")
            console.print("[yellow]Type 'stats' to see performance metrics[/yellow]\n")
            console.print("[yellow]Type 'tokens' to see token usage and cost[/yellow]\n")
            
            # Initial greeting
            console.print(Markdown("_DJ R-3X powers up with a cheerful whir_"))
            console.print("[cyan]DJ R-3X:[/cyan] Bright suns, traveler! DJ R-3X here, ready to drop some knowledge faster than a pod racer on Boonta Eve! What can I tell you about?")
            
            # Main chat loop
            while True:
                # Get user input
                user_input = input("\n[You]: ").strip()
                
                # Check for exit command
                if user_input.lower() in ['exit', 'quit', 'bye']:
                    console.print("\n[cyan]DJ R-3X:[/cyan] 'Til the Spire! Keep rockin', traveler!")
                    break
                
                # Check for stats command
                if user_input.lower() == 'stats':
                    chat.print_latency_report()
                    continue
                
                # Check for tokens command
                if user_input.lower() == 'tokens':
                    chat.print_token_usage_report()
                    continue
                
                # Track total query time
                start_time = time.time()
                
                # Generate and display response
                response = await chat.generate_response(user_input)
                
                # Calculate and display total time
                total_time = time.time() - start_time
                console.print(f"\n[cyan]DJ R-3X:[/cyan] {response}")
                console.print(f"[dim][Total response time: {total_time:.2f}s][/dim]")
                
        except KeyboardInterrupt:
            console.print("\n[cyan]DJ R-3X:[/cyan] Woah! Emergency shutdown sequence initiated. Stay safe, traveler!")
        
        finally:
            # Clean up
            chat.close()
            # Final stats
            chat.print_latency_report()
            chat.print_token_usage_report()
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

def main():
    """Main function to run the asyncio event loop."""
    asyncio.run(async_main())

if __name__ == "__main__":
    main() 