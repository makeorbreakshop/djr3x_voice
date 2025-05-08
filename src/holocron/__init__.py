"""
Holocron Knowledge System

A RAG-based system that provides canonical Star Wars knowledge to DJ R3X
through retrieval-augmented generation using a Supabase vector database.
"""

# Import the patches from the holocron package
import sys
import os
import logging

logger = logging.getLogger(__name__)

# Add the root directory to the path for importing the patches
try:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
    from holocron import patches
    logger.info("Successfully imported patches from holocron package")
except ImportError as e:
    logger.error(f"Failed to import patches: {e}")
    logger.warning("You might encounter issues with Supabase client initialization") 