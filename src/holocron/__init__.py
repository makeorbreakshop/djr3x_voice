"""
Holocron Knowledge System

A RAG-based system that provides canonical Star Wars knowledge to DJ R3X
through retrieval-augmented generation using a Supabase vector database.
"""

import sys
import os
import logging

logger = logging.getLogger(__name__) 