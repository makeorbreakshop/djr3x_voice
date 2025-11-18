"""
Memory service package for CantinaOS.

Provides long-term memory storage for person profiles and event history.
Distinct from NervousSystemService which handles real-time operational state.
"""

from .memory_service import MemoryService

__all__ = ["MemoryService"]
