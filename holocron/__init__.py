"""
Holocron Knowledge System

A RAG (Retrieval-Augmented Generation) system for Star Wars canonical knowledge.
"""

# Import patches first to ensure they're applied before any other imports
from . import patches

# Export key components
from .database import client_factory 