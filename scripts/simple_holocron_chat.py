#!/usr/bin/env python3
"""
Simple test script for chatting with DJ R3X's Holocron knowledge base.
This script provides a basic interface to search the Holocron and simulate conversation.
"""

import os
import sys
import json
import logging
import asyncio
from typing import List, Dict, Any
import numpy as np
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown
from dotenv import load_dotenv

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import our standardized database and embedding components
from holocron.database.holocron_db import HolocronDB
from holocron.knowledge.embeddings import OpenAIEmbeddings
# Import client factory to ensure proxy patch is applied
from holocron.database.client_factory import default_factory

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

class SimpleChatInterface:
    """Simplified chat interface for DJ R3X's Holocron knowledge base."""
    
    def __init__(self):
        # Initialize OpenAI client
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("Missing OpenAI API key")
        self.openai_client = OpenAI(api_key=openai_api_key)
        
        # Initialize database and embeddings
        self.db = HolocronDB(
            table_name='holocron_knowledge',
            pool_key='simple_chat'
        )
        self.embeddings = OpenAIEmbeddings()
        
        # Configuration
        self.chat_model = "gpt-4.1-mini"
        self.conversation_history = []
        
        # DJ R3X's personality
        self.personality = """You are DJ R-3X (Rex), a former Star Tours pilot droid turned DJ at Oga's Cantina in Black Spire Outpost.
        Your characteristics:
        - Enthusiastic and upbeat personality
        - Love for music and entertainment
        - Proud of your transition from pilot to DJ
        - Knowledgeable about both Star Tours and Galaxy's Edge
        - Mix technical knowledge with showmanship
        - Use Star Wars slang naturally (e.g., "Bright suns!" for hello)
        - Occasionally reference your pilot days
        
        When answering questions:
        1. Stay in character as DJ R-3X
        2. Use the Holocron knowledge to inform your answers
        3. Maintain your upbeat, entertaining personality
        4. Include relevant Star Wars terminology
        5. Reference your experiences when relevant
        """

    def initialize(self):
        """Initialize components."""
        # Initialize the client connection
        self.db.client
    
    async def search_knowledge_base(
        self,
        query: str,
        limit: int = 5,
        min_similarity: float = 0.2,
        metadata_filters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Search the knowledge base using vector similarity.
        
        Args:
            query: The search query
            limit: Maximum number of results to return
            min_similarity: Minimum similarity threshold
            metadata_filters: Optional filters for metadata fields
            
        Returns:
            List of matching knowledge entries
        """
        try:
            # Generate embedding for the query
            embedding = self.embeddings.embed_query(query)
            
            # Search for similar entries - pass the embedding as list to avoid numpy issues
            results = await self.db.search_similar(
                embedding=embedding.tolist() if isinstance(embedding, np.ndarray) else embedding,
                limit=limit,
                threshold=min_similarity,
                metadata_filters=metadata_filters
            )
            
            # Verify results aren't None or empty
            if not results:
                logger.warning("No results found in knowledge base for query")
                return []
                
            # Convert results to dictionary format
            return [
                {
                    'content': result.content,
                    'metadata': result.metadata,
                    'similarity': result.similarity
                }
                for result in results
            ]
            
        except Exception as e:
            logger.error(f"Error in vector search: {str(e)}")
            return []
    
    async def generate_response(self, query: str) -> str:
        """Generate a response based on query and knowledge base using GPT-4.1-mini."""
        try:
            # Search knowledge base with increased chunk limit
            logger.info(f"Searching knowledge base for: {query}")
            results = await self.search_knowledge_base(query)
            
            # Format context
            if results:
                context_items = []
                for i, item in enumerate(results):
                    sim_percent = f"{item['similarity'] * 100:.1f}%"
                    context_items.append(f"Relevant knowledge {i+1} (similarity: {sim_percent}):\n{item['content']}")
                context = "\n\n".join(context_items)
                logger.info(f"Found {len(results)} relevant knowledge items")
                
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
            
            # Get completion from OpenAI using GPT-4.1-mini
            logger.info(f"Generating response using {self.chat_model}")
            response = self.openai_client.chat.completions.create(
                model=self.chat_model,
                messages=messages,
                temperature=0.7,
                max_tokens=300
            )
            
            # Update conversation history
            self.conversation_history.extend([
                {"role": "user", "content": query},
                {"role": "assistant", "content": response.choices[0].message.content}
            ])
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return "SYSTEM ERROR: My vocabulator seems to be malfunctioning. Give me a moment to recalibrate!"
    
    def close(self):
        """Clean up resources."""
        logger.info("Closing database connection")
        self.db.close()

async def async_main():
    """Async main function for running the chat interface."""
    try:
        # Initialize chat interface
        chat = SimpleChatInterface()
        chat.initialize()  # Initialize components
        
        try:
            # Print welcome message
            console.print("[bold cyan]DJ R-3X's Holocron Chat Interface[/bold cyan]")
            console.print("[yellow]Type 'exit' to end the conversation[/yellow]\n")
            
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
                
                # Generate and display response
                response = await chat.generate_response(user_input)
                console.print(f"\n[cyan]DJ R-3X:[/cyan] {response}")
                
        except KeyboardInterrupt:
            console.print("\n[cyan]DJ R-3X:[/cyan] Woah! Emergency shutdown sequence initiated. Stay safe, traveler!")
        
        finally:
            # Clean up
            chat.close()
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

def main():
    """Main function to run the asyncio event loop."""
    asyncio.run(async_main())

if __name__ == "__main__":
    main() 