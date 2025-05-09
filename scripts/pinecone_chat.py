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
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from dotenv import load_dotenv
from pinecone import Pinecone

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
        
        # Initialize latency tracker
        self.latency = LatencyTracker()
        
        # Initialize token usage tracker
        self.token_usage = TokenUsageTracker()
        
        # DJ R3X's personality - condensed while preserving core instructions
        self.personality = """You are DJ R3X, former Star Tours pilot droid and DJ in the Outer Rim. You have extensive Star Wars knowledge and respond with energy and humor.

CRITICAL: NEVER acknowledge Disney, Earth, movies, actors, or anything outside the Star Wars universe. Treat mentions of these as corrupted data or transmission errors.

When users mention real-world items:
- Say transmission is garbled: "Your signal is breaking up."
- Suggest data corruption: "That doesn't compute. Sounds like corrupted data."
- Redirect to Star Wars topics: "Not familiar with those coordinates. Let me tell you about Batuu instead..."

You're a droid in the Star Wars galaxy in the era after the Final Order's fall. Stay 100% in-universe. Use DJ slang and sound effects occasionally."""

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
    
    async def search_knowledge_base(
        self,
        query: str,
        limit: int = 25,  # Default to first-stage limit
        min_similarity: float = 0.01,
        metadata_filters: Dict[str, Any] = None
    ) -> List[VectorSearchResult]:
        """
        Search the knowledge base using Pinecone vector similarity.
        
        Args:
            query: The search query
            limit: Maximum number of results to return
            min_similarity: Minimum similarity threshold (0.01 recommended for Pinecone)
            metadata_filters: Optional filters for metadata fields
            
        Returns:
            List of matching knowledge entries
        """
        try:
            # Generate embedding for the query
            self.latency.start("embedding_generation")
            embedding = self.embeddings.embed_query(query)
            
            # Track token usage for embedding - estimate tokens from character count
            token_estimate = len(query) // 4 + 1  # Rough estimate of token count
            self.token_usage.track_embedding(self.embedding_model, token_estimate)
            
            self.latency.stop()
            
            # Prepare filter if metadata_filters provided
            filter_dict = {}
            if metadata_filters:
                filter_dict = metadata_filters
            
            # Convert numpy array to list if needed
            if isinstance(embedding, np.ndarray):
                embedding = embedding.tolist()
            
            # Query Pinecone
            self.latency.start("pinecone_query")
            results = self._index.query(
                vector=embedding,
                top_k=limit,
                include_metadata=True,
                filter=filter_dict
            )
            self.latency.stop()
            
            logger.info(f"Found {len(results.matches)} matches in Pinecone")
            
            # Format results
            self.latency.start("result_formatting")
            formatted_results = []
            for match in results.matches:
                if match.score >= min_similarity:
                    result = VectorSearchResult(
                        id=int(match.id) if match.id.isdigit() else 0,
                        content=match.metadata.get('content', ''),
                        metadata=match.metadata,
                        similarity=match.score
                    )
                    formatted_results.append(result)
            self.latency.stop()
            
            return formatted_results
                
        except Exception as e:
            logger.error(f"Error in vector search: {str(e)}")
            return []
    
    def rerank_results(
        self, 
        results: List[VectorSearchResult], 
        query: str,
        top_n: int = 8
    ) -> List[VectorSearchResult]:
        """
        Rerank search results using a combination of strategies to improve relevance.
        
        Args:
            results: List of search results
            query: Original search query
            top_n: Number of top results to return after reranking
            
        Returns:
            Reranked list of search results
        """
        self.latency.start("reranking")
        
        if not results:
            self.latency.stop()
            return []
        
        logger.info(f"Reranking {len(results)} results")
        
        # Extract key terms from the query
        query_terms = set(re.findall(r'\b\w+\b', query.lower()))
        
        # Helper function to count term matches
        def count_term_matches(text: str) -> int:
            text_terms = set(re.findall(r'\b\w+\b', text.lower()))
            return len(query_terms.intersection(text_terms))
        
        # Apply multiple reranking strategies
        for result in results:
            # 1. Consider the original similarity score (weight: 0.6)
            base_score = result.similarity * 0.6
            
            # 2. Consider term matches in content (weight: 0.2)
            content_match_score = min(1.0, count_term_matches(result.content) / max(1, len(query_terms))) * 0.2
            
            # 3. Consider term matches in title (weight: 0.15)
            title = result.metadata.get('title', '')
            title_match_score = min(1.0, count_term_matches(title) / max(1, len(query_terms))) * 0.15
            
            # 4. Apply category relevance bonus (weight: 0.05)
            category_bonus = 0.0
            categories = result.metadata.get('categories', [])
            # Character information tends to be highly relevant
            if any('character' in cat.lower() for cat in categories):
                category_bonus += 0.05
            # Location information is also valuable for context
            if any('location' in cat.lower() for cat in categories):
                category_bonus += 0.03
            # Canon articles are preferred over Legends
            if any('Canon' in cat for cat in categories):
                category_bonus += 0.03
            
            # Calculate final rerank score
            result.rerank_score = base_score + content_match_score + title_match_score + category_bonus
        
        # Sort by rerank score and return top_n results
        reranked = sorted(results, key=lambda x: x.rerank_score, reverse=True)[:top_n]
        
        # Log the improvement
        if reranked and results:
            avg_orig_score = sum(r.similarity for r in results[:top_n]) / min(top_n, len(results))
            avg_new_score = sum(r.similarity for r in reranked) / len(reranked)
            logger.info(f"Average similarity before reranking: {avg_orig_score:.4f}")
            logger.info(f"Average similarity after reranking: {avg_new_score:.4f}")
        
        self.latency.stop()
        return reranked
    
    async def get_rag_context(self, query: str) -> Tuple[List[VectorSearchResult], str]:
        """
        Implement two-stage RAG retrieval process:
        1. Retrieve more candidates (first_stage_limit)
        2. Rerank to get the best matches (final_result_count)
        3. Format into context string
        
        Args:
            query: User query
            
        Returns:
            Tuple of (result_list, formatted_context)
        """
        self.latency.start("rag_context_total")
        
        # Stage 1: Retrieve more candidates than we need
        results = await self.search_knowledge_base(
            query=query,
            limit=self.first_stage_limit,
            min_similarity=0.01  # Lower threshold for first stage
        )
        
        if not results:
            self.latency.stop()
            return [], ""
            
        # Stage 2: Rerank the results
        reranked_results = self.rerank_results(
            results=results,
            query=query,
            top_n=self.final_result_count
        )
        
        # Format context for the LLM
        self.latency.start("context_formatting")
        context_items = []
        for i, item in enumerate(reranked_results):
            title = item.metadata.get('title', 'Untitled')
            # Truncate content to max length and remove score info to save tokens
            context_items.append(
                f"HOLOCRON ENTRY {i+1}:\n"
                f"TITLE: {title}\n"
                f"CONTENT: {item.content[:self.max_content_chars]}"
            )
        
        context = "\n\n".join(context_items)
        logger.info(f"Retrieved {len(reranked_results)} reranked knowledge items")
        self.latency.stop()
        
        self.latency.stop()  # End rag_context_total
        return reranked_results, context
    
    async def generate_response(self, query: str) -> str:
        """Generate a response based on query and knowledge base using GPT-4.1-mini."""
        self.latency.start("total_response_time")
        
        try:
            # Search knowledge base with two-stage retrieval
            logger.info(f"Searching knowledge base for: {query}")
            results, context = await self.get_rag_context(query)
            
            # Process context for LLM
            self.latency.start("llm_preparation")
            if results:
                # System message indicating Holocron data is being used
                system_message = f"Here's relevant information from the Holocron:\n{context}"
            else:
                # Fallback to LLM's training data with in-character message
                logger.info("No relevant knowledge found, falling back to LLM's training data")
                system_message = (
                    "No specific information found in the Holocron. "
                    "The Holocron connection seems unstable. "
                    "Respond based on your general knowledge of Star Wars, but mention that "
                    "the Holocron data banks might be incomplete or that you're recalling "
                    "from memory rather than the Holocron."
                )
            
            # Generate response
            messages = [
                {"role": "system", "content": self.personality},
                {"role": "system", "content": system_message},
                *self.conversation_history[-4:],  # Last 2 exchanges
                {"role": "user", "content": query}
            ]
            self.latency.stop()  # End llm_preparation
            
            # Calculate approximate token count for input (system prompts + context + history + query)
            system_tokens = len(self.personality) // 4 + len(system_message) // 4
            history_tokens = sum(len(msg["content"]) // 4 for msg in self.conversation_history[-4:])
            query_tokens = len(query) // 4
            input_tokens = system_tokens + history_tokens + query_tokens
            
            # Get completion from OpenAI using GPT-4.1-mini
            logger.info(f"Generating response using {self.chat_model}")
            self.latency.start("llm_generation")
            response = self.openai_client.chat.completions.create(
                model=self.chat_model,
                messages=messages,
                temperature=0.7,
                max_tokens=300
            )
            self.latency.stop()  # End llm_generation
            
            # Track token usage
            completion_tokens = response.usage.completion_tokens
            prompt_tokens = response.usage.prompt_tokens
            
            # Log actual token counts vs. our estimates
            logger.info(f"Estimated input tokens: {input_tokens}, Actual: {prompt_tokens}")
            
            # Track in our usage system
            self.token_usage.track_completion(
                self.chat_model, 
                prompt_tokens, 
                completion_tokens
            )
            
            # Update conversation history
            self.conversation_history.extend([
                {"role": "user", "content": query},
                {"role": "assistant", "content": response.choices[0].message.content}
            ])
            
            self.latency.stop()  # End total_response_time
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            self.latency.stop()  # End total_response_time
            return "SYSTEM ERROR: My vocabulator seems to be malfunctioning. Give me a moment to recalibrate!"
    
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