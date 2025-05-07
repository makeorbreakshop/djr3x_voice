"""
Holocron Manager for DJ R3X.

This module provides the HolocronManager class which integrates the RAG knowledge system
with the voice interaction pipeline, enhancing DJ R3X's Star Wars knowledge.
"""

import re
import asyncio
import logging
from typing import Dict, Any, Tuple, Optional

from src.bus import EventBus, EventTypes, SystemMode
from src.holocron.rag_provider import RAGProvider
from config import app_settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HolocronManager:
    """
    Manages the Holocron Knowledge System which provides canonical Star Wars knowledge to DJ R3X.
    
    This class handles the integration between the RAG system and the Voice Manager,
    detecting knowledge-seeking queries and injecting relevant context into responses.
    """
    
    def __init__(self, event_bus: EventBus):
        """
        Initialize the Holocron Manager.
        
        Args:
            event_bus: The application event bus
        """
        self.event_bus = event_bus
        self.rag_provider = RAGProvider()
        self.enabled = app_settings.HOLOCRON_ENABLED
        
        # Knowledge seeking patterns
        self.knowledge_patterns = [
            r"(?i)tell me about",
            r"(?i)what (is|are|was|were)",
            r"(?i)who (is|are|was|were)",
            r"(?i)where (is|are|was|were)",
            r"(?i)when (is|are|was|were)",
            r"(?i)why (is|are|was|were)",
            r"(?i)how (does|do|did)",
            r"(?i)can you explain",
            r"(?i)information (about|on)",
        ]
        
        # Register for events
        self.event_bus.on(EventTypes.SYSTEM_MODE_CHANGED, self._handle_mode_change)
        
        logger.info("Holocron Manager initialized")
    
    async def _handle_mode_change(self, data: Dict[str, Any]) -> None:
        """
        Handle system mode changes.
        
        Args:
            data: Event data containing old_mode and new_mode
        """
        if "new_mode" not in data:
            return
            
        new_mode = data["new_mode"]
        
        # Holocron should be available in INTERACTIVE mode only
        if new_mode == SystemMode.INTERACTIVE.value:
            logger.info("Holocron knowledge system activated")
            await self.event_bus.emit(EventTypes.SYSTEM_STATUS, {
                "component": "holocron",
                "status": "active"
            })
        else:
            logger.info("Holocron knowledge system deactivated")
            await self.event_bus.emit(EventTypes.SYSTEM_STATUS, {
                "component": "holocron",
                "status": "inactive"
            })
    
    def is_knowledge_seeking_query(self, query: str) -> bool:
        """
        Determine if a query is seeking Star Wars knowledge.
        
        Args:
            query: The user's query text
            
        Returns:
            True if the query appears to be seeking knowledge
        """
        if not self.enabled:
            return False
            
        # Check if the query matches any of our knowledge-seeking patterns
        for pattern in self.knowledge_patterns:
            if re.search(pattern, query):
                return True
                
        # Star Wars keywords that might indicate knowledge-seeking
        star_wars_keywords = [
            r"(?i)jedi", r"(?i)sith", r"(?i)lightsaber", r"(?i)force", 
            r"(?i)empire", r"(?i)rebel", r"(?i)droid", r"(?i)clone",
            r"(?i)mandalorian", r"(?i)republic", r"(?i)galaxy",
            r"(?i)luke", r"(?i)vader", r"(?i)leia", r"(?i)han solo",
            r"(?i)millennium falcon", r"(?i)death star", r"(?i)star destroyer",
            r"(?i)tatooine", r"(?i)coruscant", r"(?i)naboo", r"(?i)hoth",
            r"(?i)yoda", r"(?i)obi-wan", r"(?i)kylo", r"(?i)rey", r"(?i)finn",
            r"(?i)palpatine", r"(?i)emperor", r"(?i)darth"
        ]
        
        # If query contains Star Wars terminology with a question structure
        for keyword in star_wars_keywords:
            if re.search(keyword, query):
                # Look for question-like structure
                if (re.search(r"\?$", query) or 
                    re.search(r"(?i)^(what|who|where|when|why|how)", query)):
                    return True
                
        return False
        
    async def enhance_prompt(self, query: str, system_prompt: str) -> Tuple[str, bool]:
        """
        Enhance the system prompt with relevant Star Wars knowledge.
        
        Args:
            query: The user's question or query
            system_prompt: The original system prompt
            
        Returns:
            Tuple containing:
            - enhanced system prompt with injected knowledge
            - boolean indicating if the prompt was enhanced
        """
        if not self.enabled:
            return system_prompt, False
            
        is_knowledge_query = self.is_knowledge_seeking_query(query)
        
        # Only attempt to retrieve knowledge if the query seems to be asking for it
        if is_knowledge_query:
            context, found = await self.rag_provider.get_relevant_context(query)
            
            if found:
                logger.info("Found relevant Star Wars knowledge for query")
                
                # Add the context to the system prompt
                enhanced_prompt = f"{system_prompt}\n\n" \
                                 f"Star Wars knowledge from the holocron:\n" \
                                 f"{context}\n\n" \
                                 f"When using this Star Wars knowledge in your response, mention " \
                                 f"that you're 'consulting the holocron' to maintain character voice. " \
                                 f"If the user is asking a direct Star Wars question, make sure to use " \
                                 f"this knowledge to give an accurate and complete answer while staying in character."
                
                return enhanced_prompt, True
                
        return system_prompt, False
        
    async def process_response(self, query: str, response: str, 
                             context_used: bool) -> str:
        """
        Process the AI response to ensure it properly integrates holocron knowledge.
        
        Args:
            query: The original user query
            response: The raw AI response
            context_used: Whether holocron context was used
            
        Returns:
            The processed response
        """
        if not self.enabled or not context_used:
            return response
            
        # Check if the response already mentions consulting the holocron
        if "consult" in response.lower() and "holocron" in response.lower():
            # Already in proper format
            return response
            
        # If it's a direct knowledge question and doesn't mention the holocron,
        # add a prefix about consulting the holocron
        if self.is_knowledge_seeking_query(query):
            if not re.search(r"(?i)holocron", response):
                # Add a prefix about consulting the holocron
                prefix_options = [
                    "Let me consult the holocron on that... ",
                    "According to my holocron database, ",
                    "Ah, the holocron has some information about this! ",
                    "My holocron shows that ",
                    "*checks holocron quickly* "
                ]
                import random
                prefix = random.choice(prefix_options)
                
                return f"{prefix}{response}"
                
        return response

    async def enhance_voice_processing(self, query: str, system_prompt: str) -> Tuple[str, bool, str]:
        """
        Enhance the voice processing with Star Wars knowledge.
        
        This is the main method called by VoiceManager to integrate holocron knowledge.
        
        Args:
            query: The user's transcribed query
            system_prompt: The original system prompt
            
        Returns:
            Tuple containing:
            - enhanced system prompt
            - whether knowledge was used
            - prompt modification description for logging
        """
        # Skip if holocron is disabled
        if not self.enabled:
            return system_prompt, False, "Holocron disabled"
            
        # Try to enhance prompt with Star Wars knowledge
        enhanced_prompt, knowledge_used = await self.enhance_prompt(query, system_prompt)
        
        if knowledge_used:
            return enhanced_prompt, True, "Added Star Wars knowledge context"
        else:
            return system_prompt, False, "No relevant knowledge found" 