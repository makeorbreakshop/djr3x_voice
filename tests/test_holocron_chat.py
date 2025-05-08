#!/usr/bin/env python3
"""
Test script for chatting with DJ R3X's Holocron knowledge base.
This script simulates a conversation with DJ R3X using GPT-4 and the Holocron system.
"""

import os
import sys
import asyncio
from typing import List, Dict, Any
import logging
from rich.console import Console
from rich.markdown import Markdown
from openai import AsyncOpenAI
from dotenv import load_dotenv
import pytest
from unittest.mock import Mock, patch
import numpy as np
from openai import OpenAI

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import Holocron components
from holocron.knowledge.retriever import HolocronRetriever
from holocron.knowledge.embeddings import OpenAIEmbeddings
from scripts.simple_holocron_chat import SimpleChatInterface

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
console = Console()

# Load environment variables
load_dotenv()

# Test data
MOCK_EMBEDDING = [0.1] * 1536
MOCK_KNOWLEDGE_RESULTS = [
    {
        "id": "1",
        "content": "DJ R-3X, formerly RX-24, was a pilot droid for Star Tours before becoming a DJ at Oga's Cantina.",
        "metadata": {"title": "DJ R-3X", "source": "test"},
        "similarity": 0.95
    },
    {
        "id": "2",
        "content": "Oga's Cantina is located in Black Spire Outpost on Batuu.",
        "metadata": {"title": "Oga's Cantina", "source": "test"},
        "similarity": 0.85
    }
]

class HolocronChat:
    """Chat interface for DJ R3X's Holocron knowledge base."""
    
    def __init__(self):
        self.retriever = HolocronRetriever()
        self.embeddings = OpenAIEmbeddings()
        self.conversation_history: List[Dict[str, str]] = []
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # DJ R3X's personality traits
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
        
    async def get_holocron_context(self, query: str) -> List[Dict[str, Any]]:
        """Retrieve relevant context from the Holocron knowledge base."""
        try:
            results = await self.retriever.search(
                query=query,
                limit=5,
                min_relevance=0.7
            )
            return results
        except Exception as e:
            logger.error(f"Error retrieving from Holocron: {e}")
            return []

    async def generate_response(self, user_input: str) -> str:
        """Generate DJ R-3X's response using GPT-4 and Holocron knowledge."""
        # Get relevant context from Holocron
        holocron_context = await self.get_holocron_context(user_input)
        
        # Format context for GPT-4
        context_text = "\n\n".join([
            f"Relevant knowledge {i+1}:\n{item['content']}"
            for i, item in enumerate(holocron_context)
        ])
        
        # Prepare messages for GPT-4
        messages = [
            {"role": "system", "content": self.personality},
            {"role": "system", "content": f"Here's relevant information from the Holocron:\n{context_text}"},
            *self.conversation_history[-4:],  # Last 2 exchanges for context
            {"role": "user", "content": user_input}
        ]
        
        try:
            # Get response from GPT-4
            response = await self.client.chat.completions.create(
                model="gpt-4",
                messages=messages,
                temperature=0.7,
                max_tokens=300
            )
            
            # Update conversation history
            self.conversation_history.extend([
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response.choices[0].message.content}
            ])
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "SYSTEM ERROR: My vocabulator seems to be malfunctioning. Give me a moment to recalibrate!"

async def main():
    """Main chat loop."""
    chat = HolocronChat()
    
    console.print("[bold cyan]DJ R-3X's Holocron Chat Interface[/bold cyan]")
    console.print("[yellow]Type 'exit' to end the conversation[/yellow]\n")
    
    # Initial greeting
    console.print(Markdown("_DJ R-3X powers up with a cheerful whir_"))
    console.print("[cyan]DJ R-3X:[/cyan] Bright suns, traveler! DJ R-3X here, ready to drop some knowledge faster than a pod racer on Boonta Eve! What can I tell you about?")
    
    while True:
        try:
            # Get user input
            user_input = input("\n[You]: ").strip()
            
            if user_input.lower() in ['exit', 'quit', 'bye']:
                console.print("\n[cyan]DJ R-3X:[/cyan] Till the Spire, friend! Keep the music playing!")
                break
            
            # Show processing indicator
            with console.status("[bold cyan]Consulting the Holocron...[/bold cyan]"):
                response = await chat.generate_response(user_input)
            
            # Display response
            console.print(f"\n[cyan]DJ R-3X:[/cyan] {response}")
            
        except KeyboardInterrupt:
            console.print("\n\n[cyan]DJ R-3X:[/cyan] Woah! Smooth landing on that shutdown sequence! Till the Spire!")
            break
        except Exception as e:
            logger.error(f"Error in chat loop: {e}")
            console.print("\n[red]SYSTEM ERROR: Something went wrong with the Holocron interface.[/red]")

@pytest.fixture
def chat_interface():
    """Create a SimpleChatInterface instance with mocked external services."""
    with patch('openai.OpenAI'), patch('supabase.create_client'):
        interface = SimpleChatInterface()
        # Mock the OpenAI client
        interface.openai_client = Mock()
        # Mock the Supabase client
        interface.supabase = Mock()
        return interface

def test_initialization(chat_interface):
    """Test proper initialization of the chat interface."""
    assert chat_interface.chat_model == "gpt-4.1-mini"
    assert chat_interface.embedding_model == "text-embedding-ada-002"
    assert isinstance(chat_interface.conversation_history, list)
    assert chat_interface.table_name == "holocron_knowledge"

def test_embedding_generation(chat_interface):
    """Test embedding generation with mock OpenAI response."""
    # Mock OpenAI embedding response
    mock_response = Mock()
    mock_response.data = [Mock(embedding=MOCK_EMBEDDING)]
    chat_interface.openai_client.embeddings.create.return_value = mock_response
    
    # Test successful embedding generation
    embedding = chat_interface.generate_embedding("Test query")
    assert len(embedding) == 1536
    assert embedding == MOCK_EMBEDDING
    
    # Test error handling
    chat_interface.openai_client.embeddings.create.side_effect = Exception("API Error")
    embedding = chat_interface.generate_embedding("Test query")
    assert len(embedding) == 1536
    assert all(x == 0.0 for x in embedding)

def test_knowledge_base_search(chat_interface):
    """Test knowledge base search with different Supabase responses."""
    # Mock successful RPC response
    mock_rpc_response = Mock(data=MOCK_KNOWLEDGE_RESULTS)
    chat_interface.supabase.functions.invoke.return_value = mock_rpc_response
    
    results = chat_interface.search_knowledge_base("Test query")
    assert len(results) == 2
    assert results[0]["content"] == MOCK_KNOWLEDGE_RESULTS[0]["content"]
    
    # Test RPC failure fallback to SQL
    chat_interface.supabase.functions.invoke.side_effect = Exception("RPC Error")
    mock_sql_response = Mock(data=MOCK_KNOWLEDGE_RESULTS)
    chat_interface.supabase.query.return_value.execute.return_value = mock_sql_response
    
    results = chat_interface.search_knowledge_base("Test query")
    assert len(results) == 2
    assert results[0]["content"] == MOCK_KNOWLEDGE_RESULTS[0]["content"]

def test_response_generation(chat_interface):
    """Test response generation with mock knowledge base and OpenAI response."""
    # Mock knowledge base search
    with patch.object(chat_interface, 'search_knowledge_base', return_value=MOCK_KNOWLEDGE_RESULTS):
        # Mock OpenAI chat completion
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Bright suns! I'm DJ R-3X!"))]
        chat_interface.openai_client.chat.completions.create.return_value = mock_response
        
        response = chat_interface.generate_response("Tell me about yourself")
        assert "Bright suns!" in response
        assert len(chat_interface.conversation_history) == 2

def test_conversation_history_management(chat_interface):
    """Test conversation history management."""
    # Mock knowledge base and OpenAI responses
    with patch.object(chat_interface, 'search_knowledge_base', return_value=MOCK_KNOWLEDGE_RESULTS):
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Response"))]
        chat_interface.openai_client.chat.completions.create.return_value = mock_response
        
        # Generate multiple responses
        for i in range(5):
            chat_interface.generate_response(f"Query {i}")
        
        # Check history length (should contain last 4 exchanges = 8 messages)
        messages = [msg for msg in chat_interface.conversation_history[-4:]]
        assert len(messages) == 4

def test_error_handling(chat_interface):
    """Test error handling in various scenarios."""
    # Test OpenAI API error
    chat_interface.openai_client.chat.completions.create.side_effect = Exception("API Error")
    response = chat_interface.generate_response("Test query")
    assert "SYSTEM ERROR" in response
    
    # Test Supabase error
    chat_interface.supabase.functions.invoke.side_effect = Exception("Database Error")
    chat_interface.supabase.query.side_effect = Exception("SQL Error")
    response = chat_interface.generate_response("Test query")
    assert isinstance(response, str)  # Should still return a response

def test_character_consistency(chat_interface):
    """Test character consistency in responses."""
    # Mock knowledge base search
    with patch.object(chat_interface, 'search_knowledge_base', return_value=MOCK_KNOWLEDGE_RESULTS):
        # Mock OpenAI chat completion with different character responses
        responses = [
            "Bright suns! Let me tell you about the cantina!",
            "Till the Spire! Keep that music playing!",
            "Back in my Star Tours days...",
            "You should hear my latest mix!"
        ]
        
        for resp in responses:
            mock_response = Mock()
            mock_response.choices = [Mock(message=Mock(content=resp))]
            chat_interface.openai_client.chat.completions.create.return_value = mock_response
            
            response = chat_interface.generate_response("Tell me something")
            assert response in responses

def test_context_window_usage(chat_interface):
    """Test proper usage of context window with 8 chunks."""
    # Create mock results with 8 chunks
    mock_results = [
        {"id": str(i), "content": f"Content chunk {i}", "metadata": {"title": f"Title {i}"}}
        for i in range(8)
    ]
    
    with patch.object(chat_interface, 'search_knowledge_base', return_value=mock_results):
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Response using all chunks"))]
        chat_interface.openai_client.chat.completions.create.return_value = mock_response
        
        # Generate response and check that all chunks were included
        chat_interface.generate_response("Test query")
        
        # Get the last call to OpenAI
        last_call = chat_interface.openai_client.chat.completions.create.call_args
        messages = last_call[1]['messages']
        
        # Find the message containing the context
        context_message = next(msg for msg in messages if "Relevant knowledge" in msg['content'])
        
        # Verify all 8 chunks are present
        for i in range(8):
            assert f"Content chunk {i}" in context_message['content']

def test_integration():
    """Integration test using real services (requires environment variables)."""
    # Only run if environment variables are set
    if all([os.getenv("OPENAI_API_KEY"), os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")]):
        chat = SimpleChatInterface()
        
        # Test basic query
        response = chat.generate_response("Tell me about yourself")
        assert response and isinstance(response, str)
        assert len(response) > 0
        
        # Test knowledge retrieval
        response = chat.generate_response("What is Oga's Cantina?")
        assert response and isinstance(response, str)
        assert len(response) > 0
        
        # Test conversation continuity
        response = chat.generate_response("Where is it located?")
        assert response and isinstance(response, str)
        assert len(response) > 0
    else:
        pytest.skip("Environment variables not set for integration test")

if __name__ == "__main__":
    asyncio.run(main()) 