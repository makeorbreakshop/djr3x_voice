"""
Memory Service - Long-term memory for DJ R3X

This service manages long-term memory storage for person profiles and event history.
It is distinct from NervousSystemService which handles real-time operational state.

Memory Tiers:
- Person Profiles: Structured data about individuals (visit counts, preferences, notes)
- Event Timeline: Searchable history of interactions (JSONL format)

Person profiles are stored as JSON files in the memory_data/profiles/ directory.
Event timeline is stored as JSONL in memory_data/events.jsonl.
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


class MemoryService(BaseService):
    """
    Long-term memory service for DJ R3X.

    Manages person profiles and event history. Distinct from NervousSystemService
    which handles real-time operational state.

    Features:
    - Person profile storage (JSON files)
    - Automatic visit tracking from vision events
    - Token-efficient context injection
    - Event timeline logging (future)
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

        self.logger.info("MemoryService: Subscribed to vision events")

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
        """Handle VISION_PERSON_EXITED event - calculate interaction duration."""
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

            # Clear tracking
            if self._current_person == person_name:
                self._current_person = None
                self._person_arrival_time = None

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
