"""
Memory Service - Long-term memory for DJ R3X

This service manages long-term memory storage for person profiles and event history.
It is distinct from NervousSystemService which handles real-time operational state.

Memory Tiers:
- Person Profiles: Structured data about individuals (visit counts, preferences, notes)
- Event Timeline: Searchable history of interactions (JSONL format)

Features:
- Person profile storage and automatic visit tracking from vision events
- Event timeline logging for all key system events:
  * Conversations (transcriptions, LLM responses, intents)
  * Person arrivals/departures (vision events)
  * Music playback (track changes)
  * System mode transitions
- Conversation history reconstruction for context injection
- Query/filter events by type, person, conversation ID, timestamp

Storage:
- Person profiles: memory_data/profiles/{name}.json
- Event timeline: memory_data/events.jsonl (append-only JSONL)
"""

import asyncio
import logging
import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # Graceful degradation if not installed

from ...base_service import BaseService
from ...core.event_topics import EventTopics


class PersonProfile(BaseModel):
    """Model for a person's profile in long-term memory."""
    name: str
    visit_count: int = 0
    first_seen: float = Field(default_factory=time.time)  # Unix timestamp
    last_seen: float = Field(default_factory=time.time)  # Unix timestamp
    total_interaction_time_seconds: float = 0.0
    preferences: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def get_time_since_last_seen(self) -> str:
        """Get human-readable time since last seen."""
        seconds_ago = time.time() - self.last_seen

        if seconds_ago < 60:
            return "just now"
        elif seconds_ago < 3600:
            minutes = int(seconds_ago / 60)
            return f"{minutes}m ago"
        elif seconds_ago < 86400:
            hours = int(seconds_ago / 3600)
            return f"{hours}h ago"
        else:
            days = int(seconds_ago / 86400)
            return f"{days}d ago"

    def get_minimal_context(self) -> str:
        """Get minimal context string for token-efficient injection.

        Format: [Name | N visits | Xh ago]
        Example: [Brandon | 47 visits | 2h ago]
        """
        return f"[{self.name} | {self.visit_count} visits | {self.get_time_since_last_seen()}]"


class TimelineEvent(BaseModel):
    """Model for an event in the timeline log."""
    timestamp: float = Field(default_factory=time.time)
    event_type: str
    event_data: Dict[str, Any] = Field(default_factory=dict)
    person: Optional[str] = None
    conversation_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryService(BaseService):
    """
    Long-term memory service for DJ R3X.

    Manages person profiles and event history. Distinct from NervousSystemService
    which handles real-time operational state.

    Features:
    - Person profile storage (JSON files)
    - Automatic visit tracking from vision events
    - Token-efficient context injection
    - Event timeline logging (conversations, music, system events)
    - Conversation history reconstruction
    - Searchable event queries
    """

    def __init__(self, event_bus, config: Optional[Dict[str, Any]] = None, name="memory_service"):
        super().__init__(service_name=name, event_bus=event_bus)

        self._config = config or {}

        # Storage paths
        self._memory_data_dir = Path(self._config.get("memory_data_dir", "memory_data"))
        self._profiles_dir = self._memory_data_dir / "profiles"
        self._events_file = self._memory_data_dir / "events.jsonl"

        # In-memory profile cache (for faster lookups)
        self._profile_cache: Dict[str, PersonProfile] = {}

        # Current person tracking (for calculating interaction time)
        self._current_person: Optional[str] = None
        self._person_arrival_time: Optional[float] = None

        # Track whether current person needs summary (conversation happened but not summarized)
        self._person_needs_summary: bool = False

        # Anthropic client for conversation summarization
        self._anthropic_client: Optional[Anthropic] = None
        self._summarization_enabled = self._config.get("enable_summarization", True)

        if self._summarization_enabled and Anthropic:
            api_key = self._config.get("ANTHROPIC_API_KEY")
            if api_key:
                self._anthropic_client = Anthropic(api_key=api_key)
                self.logger.info("Anthropic client initialized for conversation summarization")
            else:
                self.logger.warning("ANTHROPIC_API_KEY not found - conversation summarization disabled")
                self._summarization_enabled = False
        elif self._summarization_enabled and not Anthropic:
            self.logger.warning("anthropic package not installed - conversation summarization disabled")
            self._summarization_enabled = False

    async def _initialize(self) -> None:
        """Initialize the memory service."""
        try:
            # Create storage directories if they don't exist
            self._profiles_dir.mkdir(parents=True, exist_ok=True)
            self._memory_data_dir.mkdir(parents=True, exist_ok=True)

            # Load existing profiles into cache
            await self._load_profiles_into_cache()

            self.logger.info(f"Memory service initialized with {len(self._profile_cache)} cached profiles")
            self.logger.info(f"Profiles directory: {self._profiles_dir}")

            # Catchup: Summarize any conversations that weren't summarized
            if self._summarization_enabled:
                await self._catchup_unsummarized_conversations()

        except Exception as e:
            self.logger.error(f"Failed to initialize memory service: {str(e)}")
            raise

    async def _start(self) -> None:
        """Start the memory service."""
        self.logger.info("MemoryService _start method called")

        try:
            # Initialize resources
            await self._initialize()

            # Set up event subscriptions
            await self._setup_subscriptions()

            self.logger.info("MemoryService started successfully")

        except Exception as e:
            self.logger.error(f"Failed to start memory service: {str(e)}")
            raise

    async def _cleanup(self) -> None:
        """Clean up memory service resources."""
        try:
            # Save any pending profile updates
            await self._save_all_profiles()

            self.logger.info("Cleaned up memory service resources")

        except Exception as e:
            self.logger.error(f"Error cleaning up memory service resources: {str(e)}")

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        self.logger.info("MemoryService setting up event subscriptions")

        # Subscribe to vision events for automatic profile updates
        asyncio.create_task(self.subscribe(
            EventTopics.VISION_PERSON_DETECTED,
            self._handle_person_detected
        ))

        asyncio.create_task(self.subscribe(
            EventTopics.VISION_PERSON_EXITED,
            self._handle_person_exited
        ))

        # Subscribe to conversation events for timeline logging
        asyncio.create_task(self.subscribe(
            EventTopics.TRANSCRIPTION_FINAL,
            self._handle_transcription_final
        ))

        asyncio.create_task(self.subscribe(
            EventTopics.LLM_RESPONSE_TEXT,
            self._handle_llm_response
        ))

        asyncio.create_task(self.subscribe(
            EventTopics.INTENT_EXECUTION_RESULT,
            self._handle_intent_execution
        ))

        # Subscribe to music events for timeline logging
        asyncio.create_task(self.subscribe(
            EventTopics.TRACK_PLAYING,
            self._handle_track_playing
        ))

        # Subscribe to system events for timeline logging
        asyncio.create_task(self.subscribe(
            EventTopics.SYSTEM_MODE_CHANGED,
            self._handle_system_mode_change
        ))

        self.logger.info("MemoryService: Subscribed to vision, conversation, music, and system events")

    async def _handle_person_detected(self, payload: Dict[str, Any]) -> None:
        """Handle VISION_PERSON_DETECTED event - update visit count and timestamp."""
        try:
            person_name = payload.get("name", "Unknown")
            confidence = payload.get("confidence", 0.0)

            if person_name == "Unknown":
                self.logger.debug("Ignoring person detection for Unknown person")
                return

            self.logger.info(f"Person detected: {person_name} (confidence: {confidence:.2f})")

            # Get or create profile
            profile = await self.get_person_profile(person_name)

            # Track arrival time for interaction duration calculation
            self._current_person = person_name
            self._person_arrival_time = time.time()

            # Update visit tracking ONLY if this is a new visit
            # (not if we just saw them a few seconds ago)
            time_since_last = time.time() - profile.last_seen
            if time_since_last > 300:  # 5 minutes threshold for "new visit"
                profile.visit_count += 1
                self.logger.info(f"New visit recorded for {person_name} (total: {profile.visit_count})")
            else:
                self.logger.debug(f"Same visit continued for {person_name}")

            # Update last_seen timestamp
            profile.last_seen = time.time()

            # Save updated profile
            await self._save_profile(profile)

        except Exception as e:
            self.logger.error(f"Error handling person detected: {e}", exc_info=True)

    async def _handle_person_exited(self, payload: Dict[str, Any]) -> None:
        """Handle VISION_PERSON_EXITED event - calculate interaction duration and generate summary."""
        try:
            person_name = payload.get("name", "Unknown")

            if person_name == "Unknown":
                return

            self.logger.info(f"Person exited: {person_name}")

            # Calculate interaction duration if we were tracking this person
            if self._current_person == person_name and self._person_arrival_time:
                interaction_duration = time.time() - self._person_arrival_time

                # Update profile with interaction time
                profile = await self.get_person_profile(person_name)
                profile.total_interaction_time_seconds += interaction_duration

                await self._save_profile(profile)

                self.logger.info(f"Recorded {interaction_duration:.1f}s interaction for {person_name}")

                # Generate conversation summary if person had a conversation
                if self._person_needs_summary:
                    await self._update_person_summary(person_name)
                    self._person_needs_summary = False

            # Clear tracking
            if self._current_person == person_name:
                self._current_person = None
                self._person_arrival_time = None
                self._person_needs_summary = False

        except Exception as e:
            self.logger.error(f"Error handling person exited: {e}", exc_info=True)

    async def get_person_profile(self, person_name: str) -> PersonProfile:
        """
        Get a person's profile from memory.

        Returns existing profile or creates a new one if not found.
        This method is used by ClaudeService for context injection.
        """
        # Check cache first
        if person_name in self._profile_cache:
            return self._profile_cache[person_name]

        # Try loading from disk
        profile_path = self._profiles_dir / f"{person_name}.json"

        if profile_path.exists():
            try:
                with open(profile_path, 'r') as f:
                    profile_data = json.load(f)
                profile = PersonProfile(**profile_data)
                self._profile_cache[person_name] = profile
                self.logger.info(f"Loaded profile for {person_name} from disk")
                return profile
            except Exception as e:
                self.logger.error(f"Error loading profile for {person_name}: {e}")

        # Create new profile if not found
        self.logger.info(f"Creating new profile for {person_name}")
        profile = PersonProfile(name=person_name)
        self._profile_cache[person_name] = profile
        await self._save_profile(profile)

        return profile

    async def _save_profile(self, profile: PersonProfile) -> None:
        """Save a person's profile to disk."""
        try:
            profile_path = self._profiles_dir / f"{profile.name}.json"

            with open(profile_path, 'w') as f:
                json.dump(profile.model_dump(), f, indent=2)

            # Update cache
            self._profile_cache[profile.name] = profile

            self.logger.debug(f"Saved profile for {profile.name}")

        except Exception as e:
            self.logger.error(f"Error saving profile for {profile.name}: {e}")

    async def _save_all_profiles(self) -> None:
        """Save all cached profiles to disk."""
        for profile in self._profile_cache.values():
            await self._save_profile(profile)

    async def _load_profiles_into_cache(self) -> None:
        """Load all existing profiles into memory cache."""
        try:
            if not self._profiles_dir.exists():
                return

            for profile_file in self._profiles_dir.glob("*.json"):
                try:
                    with open(profile_file, 'r') as f:
                        profile_data = json.load(f)
                    profile = PersonProfile(**profile_data)
                    self._profile_cache[profile.name] = profile
                except Exception as e:
                    self.logger.error(f"Error loading profile from {profile_file}: {e}")

            self.logger.info(f"Loaded {len(self._profile_cache)} profiles into cache")

        except Exception as e:
            self.logger.error(f"Error loading profiles into cache: {e}")

    async def update_person_note(self, person_name: str, note: str) -> None:
        """Add a note to a person's profile."""
        profile = await self.get_person_profile(person_name)
        profile.notes.append(note)
        await self._save_profile(profile)
        self.logger.info(f"Added note to {person_name}'s profile")

    async def update_person_preference(self, person_name: str, key: str, value: Any) -> None:
        """Update a preference in a person's profile."""
        profile = await self.get_person_profile(person_name)
        profile.preferences[key] = value
        await self._save_profile(profile)
        self.logger.info(f"Updated preference '{key}' for {person_name}")

    def get_all_profiles(self) -> List[PersonProfile]:
        """Get all person profiles from cache."""
        return list(self._profile_cache.values())

    # ========================================================================
    # Event Timeline Logging
    # ========================================================================

    async def log_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        person: Optional[str] = None,
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log an event to the timeline.

        Args:
            event_type: Event topic/type (e.g., "TRANSCRIPTION_FINAL")
            event_data: Event payload data
            person: Associated person (if applicable)
            conversation_id: Conversation ID (if applicable)
            metadata: Additional metadata
        """
        try:
            event = TimelineEvent(
                event_type=event_type,
                event_data=event_data,
                person=person,
                conversation_id=conversation_id,
                metadata=metadata or {}
            )

            # Append to JSONL file
            with open(self._events_file, 'a') as f:
                f.write(json.dumps(event.model_dump()) + '\n')

            self.logger.debug(f"Logged event: {event_type}")

        except Exception as e:
            self.logger.error(f"Error logging event {event_type}: {e}", exc_info=True)

    async def get_recent_events(
        self,
        limit: int = 50,
        event_type: Optional[str] = None,
        person: Optional[str] = None,
        conversation_id: Optional[str] = None,
        since_timestamp: Optional[float] = None
    ) -> List[TimelineEvent]:
        """
        Query recent events from the timeline.

        Args:
            limit: Maximum number of events to return
            event_type: Filter by event type
            person: Filter by person
            conversation_id: Filter by conversation ID
            since_timestamp: Only events after this timestamp

        Returns:
            List of timeline events (most recent first)
        """
        try:
            if not self._events_file.exists():
                return []

            events = []

            # Read JSONL file in reverse order for efficiency
            with open(self._events_file, 'r') as f:
                lines = f.readlines()

            # Parse in reverse (most recent first)
            for line in reversed(lines):
                if len(events) >= limit:
                    break

                try:
                    event_data = json.loads(line.strip())
                    event = TimelineEvent(**event_data)

                    # Apply filters
                    if since_timestamp and event.timestamp < since_timestamp:
                        continue
                    if event_type and event.event_type != event_type:
                        continue
                    if person and event.person != person:
                        continue
                    if conversation_id and event.conversation_id != conversation_id:
                        continue

                    events.append(event)

                except Exception as e:
                    self.logger.error(f"Error parsing event line: {e}")
                    continue

            return events

        except Exception as e:
            self.logger.error(f"Error querying events: {e}", exc_info=True)
            return []

    async def get_conversation_history(
        self,
        person: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get recent conversation history (transcriptions and responses).

        Returns a simplified conversation log for context injection.

        Args:
            person: Filter by person (optional)
            limit: Maximum number of turns to return

        Returns:
            List of conversation turns: [{"user": "...", "assistant": "...", "timestamp": ...}]
        """
        try:
            # Get transcription and LLM response events
            transcriptions = await self.get_recent_events(
                limit=limit * 2,  # Get more to account for interleaving
                event_type=EventTopics.TRANSCRIPTION_FINAL.value,
                person=person
            )

            responses = await self.get_recent_events(
                limit=limit * 2,
                event_type=EventTopics.LLM_RESPONSE_TEXT.value,
                person=person
            )

            # Combine and sort by timestamp
            all_events = transcriptions + responses
            all_events.sort(key=lambda e: e.timestamp)

            # Build conversation turns
            conversation = []
            current_turn = {}

            for event in all_events:
                if event.event_type == EventTopics.TRANSCRIPTION_FINAL.value:
                    # Start new turn
                    if current_turn:
                        conversation.append(current_turn)
                    current_turn = {
                        "user": event.event_data.get("text", ""),
                        "timestamp": event.timestamp,
                        "conversation_id": event.conversation_id
                    }
                elif event.event_type == EventTopics.LLM_RESPONSE_TEXT.value:
                    # Add assistant response to current turn
                    if current_turn:
                        current_turn["assistant"] = event.event_data.get("text", "")
                        conversation.append(current_turn)
                        current_turn = {}

            # Add any incomplete turn
            if current_turn:
                conversation.append(current_turn)

            # Return most recent N turns
            return conversation[-limit:] if len(conversation) > limit else conversation

        except Exception as e:
            self.logger.error(f"Error building conversation history: {e}", exc_info=True)
            return []

    # ========================================================================
    # Event Handlers for Timeline Logging
    # ========================================================================

    async def _handle_transcription_final(self, payload: Dict[str, Any]) -> None:
        """Log user speech to timeline and mark that conversation is happening."""
        await self.log_event(
            event_type=EventTopics.TRANSCRIPTION_FINAL.value,
            event_data=payload,
            person=self._current_person,
            conversation_id=payload.get("conversation_id")
        )

        # Mark that a conversation is happening (needs summary when person exits)
        if self._current_person:
            self._person_needs_summary = True

    async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
        """Log LLM responses to timeline."""
        await self.log_event(
            event_type=EventTopics.LLM_RESPONSE_TEXT.value,
            event_data=payload,
            person=self._current_person,
            conversation_id=payload.get("conversation_id")
        )

    async def _handle_intent_execution(self, payload: Dict[str, Any]) -> None:
        """Log intent executions to timeline."""
        await self.log_event(
            event_type=EventTopics.INTENT_EXECUTION_RESULT.value,
            event_data=payload,
            person=self._current_person,
            conversation_id=payload.get("conversation_id")
        )

    async def _handle_track_playing(self, payload: Dict[str, Any]) -> None:
        """Log music track changes to timeline."""
        await self.log_event(
            event_type=EventTopics.TRACK_PLAYING.value,
            event_data=payload,
            person=self._current_person
        )

    async def _handle_system_mode_change(self, payload: Dict[str, Any]) -> None:
        """Log system mode changes to timeline and trigger summary when leaving INTERACTIVE mode."""
        await self.log_event(
            event_type=EventTopics.SYSTEM_MODE_CHANGED.value,
            event_data=payload
        )

        # If transitioning OUT of INTERACTIVE mode and we have a person with conversation, summarize
        old_mode = payload.get("old_mode")
        if old_mode == "INTERACTIVE" and self._current_person and self._person_needs_summary:
            self.logger.info(f"Exiting INTERACTIVE mode with {self._current_person}, generating summary...")
            await self._update_person_summary(self._current_person)
            self._person_needs_summary = False

    # ========================================================================
    # Conversation Summarization
    # ========================================================================

    async def _update_person_summary(self, person_name: str) -> None:
        """Generate or update rolling conversation summary for a person.

        Uses Claude Haiku to create a concise 2-3 sentence summary of the person's
        conversation history, incorporating both existing summary and new conversations.
        """
        if not self._summarization_enabled or not self._anthropic_client:
            self.logger.debug(f"Summarization disabled, skipping summary for {person_name}")
            return

        try:
            # Get recent conversation turns (last 20 to capture current session)
            recent_turns = await self.get_conversation_history(
                person=person_name,
                limit=20
            )

            if not recent_turns or len(recent_turns) < 2:
                self.logger.debug(f"Not enough conversation turns to summarize for {person_name}")
                return

            # Get existing summary from profile
            profile = await self.get_person_profile(person_name)
            existing_summary = profile.metadata.get("conversation_summary", "")

            # Format recent conversation
            recent_text = "\n".join([
                f"User: {turn.get('user', '')}\nAssistant: {turn.get('assistant', '')}"
                for turn in recent_turns
                if turn.get('user') or turn.get('assistant')
            ])

            # Build prompt to update summary
            if existing_summary:
                prompt = f"""You are maintaining a memory summary for DJ R3X's interactions with {person_name}.

EXISTING SUMMARY:
{existing_summary}

NEW CONVERSATION (just now):
{recent_text}

Update the summary to include new information from this conversation. Keep it concise (2-3 sentences).
Focus on:
- Music preferences and requests
- Conversation patterns and interests
- Any new topics or preferences
- Remove outdated information if necessary

UPDATED SUMMARY:"""
            else:
                prompt = f"""You are creating a memory summary for DJ R3X's first interaction with {person_name}.

CONVERSATION:
{recent_text}

Create a concise 2-3 sentence summary. Focus on:
- Music preferences and requests
- Topics discussed
- Any notable patterns

SUMMARY:"""

            # Call Claude Haiku API for summarization
            response = self._anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",  # Fast and cheap for summaries
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )

            new_summary = response.content[0].text.strip()

            # Store updated summary in profile metadata
            profile.metadata["conversation_summary"] = new_summary
            profile.metadata["summary_updated_at"] = time.time()
            profile.metadata["summary_visit_count"] = profile.metadata.get("summary_visit_count", 0) + 1

            await self._save_profile(profile)

            self.logger.info(f"✨ Updated conversation summary for {person_name} (visit #{profile.visit_count})")
            self.logger.debug(f"Summary: {new_summary[:100]}...")

        except Exception as e:
            self.logger.error(f"Error updating conversation summary for {person_name}: {e}", exc_info=True)

    async def _catchup_unsummarized_conversations(self) -> None:
        """Startup catchup: Generate summaries for conversations that weren't summarized.

        Checks each person's profile to see if they have recent conversation events
        but no summary or an outdated summary. Generates summaries for these cases.
        """
        if not self._summarization_enabled or not self._anthropic_client:
            self.logger.info("Summarization disabled, skipping startup catchup")
            return

        self.logger.info("Checking for unsummarized conversations...")

        try:
            catchup_count = 0

            for profile in self._profile_cache.values():
                # Check if person has recent activity but no/old summary
                last_seen = profile.last_seen
                summary_updated = profile.metadata.get("summary_updated_at", 0)

                # If person visited after last summary (or never summarized), check for conversations
                if last_seen > summary_updated:
                    # Get conversation history for this person
                    history = await self.get_conversation_history(
                        person=profile.name,
                        limit=20
                    )

                    if history and len(history) >= 2:
                        # Found unsummarized conversation, generate summary
                        self.logger.info(f"Found unsummarized conversation for {profile.name}, catching up...")
                        await self._update_person_summary(profile.name)
                        catchup_count += 1

            if catchup_count > 0:
                self.logger.info(f"✨ Catchup complete: Generated {catchup_count} conversation summaries")
            else:
                self.logger.info("No unsummarized conversations found")

        except Exception as e:
            self.logger.error(f"Error during summarization catchup: {e}", exc_info=True)
