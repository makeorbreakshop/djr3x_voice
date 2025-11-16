"""
Memory Extraction Service - First Principles Implementation
============================================================

Implements mem0-style memory extraction without external dependencies.
Uses Claude/GPT to extract facts from conversations and deduplicate.

Core Algorithm:
1. Extract facts from conversation using LLM
2. Compare with existing memories (semantic similarity)
3. Decide operation: ADD, UPDATE, DELETE, MERGE, SKIP
4. Store in structured format

Author: DJ R3X Development Team
Date: 2025-11-16
"""

import json
import time
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel
import asyncio


# ============================================================================
# Data Models
# ============================================================================

class ExtractedFact(BaseModel):
    """A single fact extracted from conversation."""
    content: str
    category: str  # "preference", "fact", "feedback", "context"
    importance: float  # 0.0-1.0
    temporal: bool = False  # Is this time-sensitive?

class Memory(BaseModel):
    """A stored memory with metadata."""
    memory_id: str
    content: str
    category: str
    importance: float
    created_at: float
    updated_at: float
    access_count: int = 0
    last_accessed: float = 0
    metadata: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return self.model_dump()


class MemoryOperation(BaseModel):
    """Result of comparing new fact with existing memories."""
    operation: Literal["ADD", "UPDATE", "DELETE", "MERGE", "SKIP"]
    new_fact: ExtractedFact
    existing_memory_id: Optional[str] = None
    reasoning: str = ""


# ============================================================================
# Memory Extraction Prompts (The Secret Sauce)
# ============================================================================

EXTRACTION_PROMPT = """You are a memory extraction system for DJ R3X, an AI voice assistant.

Your task: Extract FACTUAL information worth remembering from this conversation.

GUIDELINES:
1. Extract user preferences (music style, LED brightness, transition speed)
2. Extract factual statements (likes, dislikes, habits)
3. Skip temporary information (current time, one-off requests)
4. Skip obvious facts (DJ R3X is a robot)
5. Be concise - extract the essence, not verbatim quotes

CONVERSATION:
{conversation}

RECENT CONTEXT (for reference):
{recent_summary}

Extract memories as JSON array:
[
  {{
    "content": "User prefers energetic DJ transitions",
    "category": "preference",
    "importance": 0.8,
    "temporal": false
  }},
  {{
    "content": "User dislikes Artist X",
    "category": "preference",
    "importance": 0.9,
    "temporal": false
  }}
]

Categories: "preference", "fact", "feedback", "context"
Importance: 0.0 (trivial) to 1.0 (critical)
Temporal: true if time-sensitive, false if permanent

Return ONLY the JSON array, no other text.
"""

UPDATE_DECISION_PROMPT = """You are a memory update decision system.

TASK: Decide how to handle a new extracted fact given existing similar memories.

NEW FACT: {new_fact}

EXISTING SIMILAR MEMORIES:
{existing_memories}

DECIDE ONE OPERATION:
- ADD: New fact is unique, no similar memory exists
- UPDATE: New fact enhances/refines existing memory (provide memory_id to update)
- DELETE: New fact contradicts existing memory (provide memory_id to delete)
- MERGE: New fact should be combined with existing (provide memory_id to merge into)
- SKIP: New fact is redundant, already captured

Return JSON:
{{
  "operation": "ADD|UPDATE|DELETE|MERGE|SKIP",
  "memory_id": "mem_123" (if UPDATE/DELETE/MERGE, null otherwise),
  "reasoning": "Brief explanation"
}}
"""


# ============================================================================
# Memory Extractor
# ============================================================================

class MemoryExtractor:
    """
    Extracts and manages memories using LLM-based extraction.

    This is the core of the memory system - mimics mem0's approach
    but implemented from first principles.
    """

    def __init__(self, llm_client, logger):
        """
        Initialize memory extractor.

        Args:
            llm_client: Claude or OpenAI client for extraction
            logger: Python logger instance
        """
        self.llm = llm_client
        self.logger = logger

    async def extract_facts(
        self,
        conversation: List[Dict[str, str]],
        recent_summary: str = ""
    ) -> List[ExtractedFact]:
        """
        Extract facts from conversation using LLM.

        This is PHASE 1 of mem0's algorithm.

        Args:
            conversation: List of message dicts [{"role": "user", "content": "..."}]
            recent_summary: Optional summary of recent conversation history

        Returns:
            List of extracted facts
        """
        # Format conversation for prompt
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in conversation
        ])

        # Build extraction prompt
        prompt = EXTRACTION_PROMPT.format(
            conversation=conversation_text,
            recent_summary=recent_summary or "No prior context"
        )

        try:
            # Call LLM for extraction
            # This works with both Claude and OpenAI
            response = await self._call_llm(prompt, temperature=0.3)

            # Parse JSON response
            facts_data = json.loads(response)

            # Validate and convert to ExtractedFact objects
            facts = [ExtractedFact(**fact) for fact in facts_data]

            self.logger.info(f"Extracted {len(facts)} facts from conversation")
            return facts

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse extraction response: {e}")
            self.logger.debug(f"Raw response: {response}")
            return []
        except Exception as e:
            self.logger.error(f"Error during fact extraction: {e}")
            return []

    async def decide_operation(
        self,
        new_fact: ExtractedFact,
        similar_memories: List[Memory]
    ) -> MemoryOperation:
        """
        Decide how to handle a new fact given existing similar memories.

        This is PHASE 2 of mem0's algorithm.

        Args:
            new_fact: Newly extracted fact
            similar_memories: Existing memories similar to this fact

        Returns:
            MemoryOperation describing what to do
        """
        # If no similar memories, ADD is obvious
        if not similar_memories:
            return MemoryOperation(
                operation="ADD",
                new_fact=new_fact,
                reasoning="No similar memories exist"
            )

        # Format existing memories for prompt
        existing_text = "\n".join([
            f"[{mem.memory_id}] {mem.content} (importance: {mem.importance})"
            for mem in similar_memories
        ])

        # Build decision prompt
        prompt = UPDATE_DECISION_PROMPT.format(
            new_fact=new_fact.content,
            existing_memories=existing_text
        )

        try:
            # Call LLM for decision
            response = await self._call_llm(prompt, temperature=0.1)

            # Parse decision
            decision = json.loads(response)

            return MemoryOperation(
                operation=decision["operation"],
                new_fact=new_fact,
                existing_memory_id=decision.get("memory_id"),
                reasoning=decision.get("reasoning", "")
            )

        except Exception as e:
            self.logger.error(f"Error deciding operation: {e}")
            # Fallback: ADD by default
            return MemoryOperation(
                operation="ADD",
                new_fact=new_fact,
                reasoning=f"Error during decision: {e}"
            )

    async def _call_llm(self, prompt: str, temperature: float = 0.3) -> str:
        """
        Call LLM with prompt.

        This abstracts away the specific LLM client (Claude/OpenAI).
        Override this method to use different LLMs.

        Args:
            prompt: Prompt text
            temperature: Sampling temperature

        Returns:
            LLM response text
        """
        # Check if using Anthropic client
        if hasattr(self.llm, 'messages'):
            response = await self.llm.messages.create(
                model="claude-haiku-4-5-20251001",  # Fast model for extraction
                max_tokens=2048,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text

        # Check if using OpenAI client
        elif hasattr(self.llm, 'chat'):
            response = await self.llm.chat.completions.create(
                model="gpt-4o-mini",
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content

        else:
            raise ValueError("Unsupported LLM client type")


# ============================================================================
# Similarity Calculation (Simple but Effective)
# ============================================================================

def calculate_similarity(text1: str, text2: str) -> float:
    """
    Calculate semantic similarity between two texts.

    Simple token-based similarity (Jaccard) - can be upgraded to embeddings later.
    This is a lightweight first-principles approach.

    Args:
        text1, text2: Texts to compare

    Returns:
        Similarity score 0.0-1.0
    """
    # Tokenize (simple word splitting)
    tokens1 = set(text1.lower().split())
    tokens2 = set(text2.lower().split())

    # Jaccard similarity: intersection / union
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2

    if not union:
        return 0.0

    return len(intersection) / len(union)


def find_similar_memories(
    new_fact: ExtractedFact,
    existing_memories: List[Memory],
    threshold: float = 0.3
) -> List[Memory]:
    """
    Find existing memories similar to a new fact.

    Args:
        new_fact: New extracted fact
        existing_memories: All existing memories
        threshold: Minimum similarity score to consider

    Returns:
        List of similar memories, sorted by similarity (descending)
    """
    similarities = []

    for memory in existing_memories:
        score = calculate_similarity(new_fact.content, memory.content)
        if score >= threshold:
            similarities.append((score, memory))

    # Sort by similarity descending
    similarities.sort(key=lambda x: x[0], reverse=True)

    return [mem for _, mem in similarities]


# ============================================================================
# Usage Example
# ============================================================================

async def example_usage():
    """Example showing how to use the memory extractor."""

    # Initialize with your LLM client
    from anthropic import AsyncAnthropic
    import logging

    llm = AsyncAnthropic(api_key="your-api-key")
    logger = logging.getLogger(__name__)

    extractor = MemoryExtractor(llm, logger)

    # Example conversation
    conversation = [
        {"role": "user", "content": "I love energetic music transitions"},
        {"role": "assistant", "content": "Got it! I'll use upbeat crossfades for you."},
        {"role": "user", "content": "And keep the LED brightness around 80%"},
        {"role": "assistant", "content": "Perfect, I'll remember that preference."}
    ]

    # Extract facts
    facts = await extractor.extract_facts(conversation)

    print(f"Extracted {len(facts)} facts:")
    for fact in facts:
        print(f"  - {fact.content} (importance: {fact.importance})")

    # Simulate existing memories
    existing = [
        Memory(
            memory_id="mem_1",
            content="User prefers smooth transitions",
            category="preference",
            importance=0.7,
            created_at=time.time() - 86400,
            updated_at=time.time() - 86400
        )
    ]

    # Decide operation for each fact
    for fact in facts:
        similar = find_similar_memories(fact, existing)
        operation = await extractor.decide_operation(fact, similar)
        print(f"\nFact: {fact.content}")
        print(f"Operation: {operation.operation}")
        print(f"Reasoning: {operation.reasoning}")


if __name__ == "__main__":
    asyncio.run(example_usage())
