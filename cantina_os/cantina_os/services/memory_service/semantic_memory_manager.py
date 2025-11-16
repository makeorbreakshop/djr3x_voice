"""
Semantic Memory Manager - First Principles Implementation
==========================================================

Manages long-term semantic memory (user preferences, facts) using
LLM-based extraction and simple similarity search.

This replaces the need for external memory services like mem0,
implementing the same core principles natively.

Key Features:
- Automatic fact extraction from conversations
- Deduplication and memory updates
- Similarity-based retrieval
- Persistence to disk
- Memory consolidation (pruning old/irrelevant memories)

Author: DJ R3X Development Team
Date: 2025-11-16
"""

import json
import os
import time
import asyncio
from typing import List, Dict, Any, Optional
from collections import defaultdict
import uuid

from .memory_extraction import (
    MemoryExtractor,
    Memory,
    ExtractedFact,
    MemoryOperation,
    find_similar_memories
)


# ============================================================================
# Semantic Memory Manager
# ============================================================================

class SemanticMemoryManager:
    """
    Manages long-term semantic memory for DJ R3X.

    This is the equivalent of mem0's memory layer, but built from scratch.
    """

    def __init__(
        self,
        llm_client,
        logger,
        storage_path: str = "data/semantic_memories.json",
        similarity_threshold: float = 0.3
    ):
        """
        Initialize semantic memory manager.

        Args:
            llm_client: Claude or OpenAI client for extraction
            logger: Python logger
            storage_path: Path to JSON file for persistence
            similarity_threshold: Minimum similarity for memory matching
        """
        self.extractor = MemoryExtractor(llm_client, logger)
        self.logger = logger
        self.storage_path = storage_path
        self.similarity_threshold = similarity_threshold

        # In-memory storage: user_id -> list of Memory objects
        self.memories: Dict[str, List[Memory]] = defaultdict(list)

        # Rolling conversation summaries (per user)
        self.summaries: Dict[str, str] = {}

        # Load from disk
        self._load_from_disk()

    # ========================================================================
    # Core Operations (mem0-style API)
    # ========================================================================

    async def add(
        self,
        messages: List[Dict[str, str]],
        user_id: str = "default_user",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add conversation to memory with automatic extraction.

        This is the equivalent of mem0's add() method.

        Args:
            messages: Conversation messages [{"role": "user", "content": "..."}]
            user_id: User identifier for memory segmentation
            metadata: Optional metadata to attach to extracted memories

        Returns:
            Dict with extraction results and operations performed
        """
        self.logger.info(f"Adding conversation to memory for user: {user_id}")

        # Get recent summary for context
        recent_summary = self.summaries.get(user_id, "")

        # PHASE 1: Extract facts from conversation
        facts = await self.extractor.extract_facts(messages, recent_summary)

        if not facts:
            self.logger.info("No facts extracted from conversation")
            return {"facts_extracted": 0, "operations": []}

        # Get existing memories for this user
        existing_memories = self.memories[user_id]

        # PHASE 2: Decide operation for each fact
        operations = []
        for fact in facts:
            # Find similar existing memories
            similar = find_similar_memories(
                fact,
                existing_memories,
                self.similarity_threshold
            )

            # Decide what to do
            operation = await self.extractor.decide_operation(fact, similar)
            operations.append(operation)

            # Execute operation
            self._execute_operation(operation, user_id, metadata)

        # Update conversation summary
        await self._update_summary(user_id, messages)

        # Persist to disk
        self._save_to_disk()

        self.logger.info(
            f"Processed {len(facts)} facts, "
            f"performed {len(operations)} operations"
        )

        return {
            "facts_extracted": len(facts),
            "operations": [
                {
                    "operation": op.operation,
                    "fact": op.new_fact.content,
                    "reasoning": op.reasoning
                }
                for op in operations
            ]
        }

    def search(
        self,
        query: str,
        user_id: str = "default_user",
        limit: int = 5,
        category_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search memories by semantic similarity.

        This is the equivalent of mem0's search() method.

        Args:
            query: Search query text
            user_id: User to search memories for
            limit: Maximum number of results
            category_filter: Optional category filter ("preference", "fact", etc.)

        Returns:
            Dict with search results
        """
        user_memories = self.memories.get(user_id, [])

        if not user_memories:
            return {"results": []}

        # Apply category filter
        if category_filter:
            user_memories = [
                m for m in user_memories
                if m.category == category_filter
            ]

        # Calculate similarity scores
        scored_memories = []
        for memory in user_memories:
            # Simple token-based similarity (can upgrade to embeddings)
            from .memory_extraction import calculate_similarity
            score = calculate_similarity(query, memory.content)
            scored_memories.append((score, memory))

        # Sort by score descending
        scored_memories.sort(key=lambda x: x[0], reverse=True)

        # Take top-k
        results = []
        for score, memory in scored_memories[:limit]:
            # Update access stats
            memory.access_count += 1
            memory.last_accessed = time.time()

            results.append({
                "memory_id": memory.memory_id,
                "memory": memory.content,
                "category": memory.category,
                "importance": memory.importance,
                "similarity_score": score,
                "metadata": memory.metadata
            })

        self.logger.debug(f"Search for '{query}' returned {len(results)} results")

        return {"results": results}

    def get_all(
        self,
        user_id: str = "default_user",
        category_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all memories for a user.

        This is the equivalent of mem0's get_all() method.

        Args:
            user_id: User identifier
            category_filter: Optional category filter

        Returns:
            List of all memories
        """
        user_memories = self.memories.get(user_id, [])

        if category_filter:
            user_memories = [
                m for m in user_memories
                if m.category == category_filter
            ]

        return [
            {
                "memory_id": m.memory_id,
                "memory": m.content,
                "category": m.category,
                "importance": m.importance,
                "created_at": m.created_at,
                "metadata": m.metadata
            }
            for m in user_memories
        ]

    def delete(self, memory_id: str, user_id: str = "default_user") -> bool:
        """
        Delete a specific memory.

        This is the equivalent of mem0's delete() method.

        Args:
            memory_id: Memory ID to delete
            user_id: User identifier

        Returns:
            True if deleted, False if not found
        """
        user_memories = self.memories.get(user_id, [])

        for i, memory in enumerate(user_memories):
            if memory.memory_id == memory_id:
                del user_memories[i]
                self._save_to_disk()
                self.logger.info(f"Deleted memory {memory_id}")
                return True

        self.logger.warning(f"Memory {memory_id} not found")
        return False

    # ========================================================================
    # Internal Operations
    # ========================================================================

    def _execute_operation(
        self,
        operation: MemoryOperation,
        user_id: str,
        metadata: Optional[Dict[str, Any]]
    ):
        """Execute a memory operation (ADD, UPDATE, DELETE, etc.)."""

        if operation.operation == "ADD":
            # Create new memory
            memory = Memory(
                memory_id=f"mem_{uuid.uuid4().hex[:12]}",
                content=operation.new_fact.content,
                category=operation.new_fact.category,
                importance=operation.new_fact.importance,
                created_at=time.time(),
                updated_at=time.time(),
                metadata=metadata or {}
            )
            self.memories[user_id].append(memory)
            self.logger.debug(f"ADD: {memory.content}")

        elif operation.operation == "UPDATE":
            # Update existing memory
            for memory in self.memories[user_id]:
                if memory.memory_id == operation.existing_memory_id:
                    memory.content = operation.new_fact.content
                    memory.importance = max(
                        memory.importance,
                        operation.new_fact.importance
                    )
                    memory.updated_at = time.time()
                    self.logger.debug(f"UPDATE: {memory.content}")
                    break

        elif operation.operation == "DELETE":
            # Delete old memory
            self.memories[user_id] = [
                m for m in self.memories[user_id]
                if m.memory_id != operation.existing_memory_id
            ]
            self.logger.debug(f"DELETE: {operation.existing_memory_id}")

        elif operation.operation == "MERGE":
            # Merge into existing memory
            for memory in self.memories[user_id]:
                if memory.memory_id == operation.existing_memory_id:
                    memory.content = f"{memory.content}; {operation.new_fact.content}"
                    memory.updated_at = time.time()
                    self.logger.debug(f"MERGE: {memory.content}")
                    break

        elif operation.operation == "SKIP":
            self.logger.debug(f"SKIP: {operation.new_fact.content}")

    async def _update_summary(
        self,
        user_id: str,
        messages: List[Dict[str, str]]
    ):
        """
        Update rolling conversation summary.

        This provides context for future extractions.
        """
        # Simple approach: Keep last N messages as summary
        # Can be upgraded to LLM-based summarization
        recent_text = " ".join([
            f"{m['role']}: {m['content']}"
            for m in messages[-10:]  # Last 10 messages
        ])

        self.summaries[user_id] = recent_text[:500]  # Keep it short

    # ========================================================================
    # Memory Consolidation (Background Maintenance)
    # ========================================================================

    async def consolidate_memories(
        self,
        user_id: str = "default_user",
        max_age_days: int = 90,
        min_importance: float = 0.3
    ):
        """
        Consolidate memories: Remove old, low-importance, rarely-accessed ones.

        This mimics mem0's background consolidation process.

        Args:
            user_id: User to consolidate
            max_age_days: Remove memories older than this
            min_importance: Remove memories below this importance
        """
        user_memories = self.memories.get(user_id, [])
        current_time = time.time()
        max_age_seconds = max_age_days * 86400

        initial_count = len(user_memories)

        # Filter memories
        kept_memories = []
        for memory in user_memories:
            age = current_time - memory.created_at

            # Keep if:
            # 1. Recent (< max_age)
            # 2. Important (>= min_importance)
            # 3. Frequently accessed (access_count > 0)
            if (
                age < max_age_seconds or
                memory.importance >= min_importance or
                memory.access_count > 0
            ):
                kept_memories.append(memory)

        self.memories[user_id] = kept_memories
        removed_count = initial_count - len(kept_memories)

        if removed_count > 0:
            self._save_to_disk()
            self.logger.info(
                f"Consolidated memories for {user_id}: "
                f"removed {removed_count}, kept {len(kept_memories)}"
            )

    # ========================================================================
    # Persistence
    # ========================================================================

    def _load_from_disk(self):
        """Load memories from JSON file."""
        if not os.path.exists(self.storage_path):
            self.logger.info("No existing memory file found, starting fresh")
            return

        try:
            with open(self.storage_path, 'r') as f:
                data = json.load(f)

            # Reconstruct Memory objects
            for user_id, memories_data in data.get("memories", {}).items():
                self.memories[user_id] = [
                    Memory(**mem_data) for mem_data in memories_data
                ]

            # Load summaries
            self.summaries = data.get("summaries", {})

            self.logger.info(
                f"Loaded memories for {len(self.memories)} users "
                f"from {self.storage_path}"
            )

        except Exception as e:
            self.logger.error(f"Error loading memories: {e}")

    def _save_to_disk(self):
        """Save memories to JSON file."""
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

        try:
            # Convert to serializable format
            data = {
                "memories": {
                    user_id: [mem.to_dict() for mem in memories]
                    for user_id, memories in self.memories.items()
                },
                "summaries": self.summaries
            }

            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)

            self.logger.debug(f"Saved memories to {self.storage_path}")

        except Exception as e:
            self.logger.error(f"Error saving memories: {e}")


# ============================================================================
# Integration with ClaudeService
# ============================================================================

async def example_integration():
    """
    Example showing how to integrate with ClaudeService.
    """
    import logging
    from anthropic import AsyncAnthropic

    # Setup
    llm = AsyncAnthropic(api_key="your-key")
    logger = logging.getLogger(__name__)

    memory_manager = SemanticMemoryManager(
        llm_client=llm,
        logger=logger,
        storage_path="data/dj_r3x_memories.json"
    )

    # ========================================================================
    # USAGE IN ClaudeService
    # ========================================================================

    # 1. Before calling Claude API - SEARCH for relevant memories
    user_query = "Play some energetic music"
    user_id = "brandon"

    relevant_memories = memory_manager.search(
        query=user_query,
        user_id=user_id,
        limit=5
    )

    # 2. Build enhanced system prompt with memories
    memory_context = "\n".join([
        f"- {mem['memory']}"
        for mem in relevant_memories["results"]
    ])

    enhanced_prompt = f"""
You are DJ R3X, a Star Wars droid DJ assistant.

USER PREFERENCES & HISTORY:
{memory_context if memory_context else "No previous preferences stored."}

Respond based on the user's preferences above.
"""

    # 3. Call Claude API with enhanced prompt
    # ... (normal Claude API call)

    # 4. After receiving response - ADD to memory
    conversation = [
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": "Playing an energetic Star Wars track!"}
    ]

    result = await memory_manager.add(
        messages=conversation,
        user_id=user_id,
        metadata={"conversation_id": "conv_123", "timestamp": time.time()}
    )

    print(f"Memory extraction result: {result}")

    # 5. Periodic consolidation (run daily)
    await memory_manager.consolidate_memories(user_id=user_id)


if __name__ == "__main__":
    asyncio.run(example_integration())
