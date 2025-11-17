"""
Claude Service - LLM Processing with Claude 3.5 Sonnet 4.5

This service handles interactions with Anthropic's Claude models for natural language processing.
It receives transcriptions from the DeepgramTranscriptionService, processes them via Claude's API,
and emits response events. It also manages conversation context and session memory.

Key differences from GPTService:
- Uses dedicated `system` parameter (no message history integration needed)
- No sandwich method required (system prompt at beginning only)
- Simpler prompting, Claude infers intent better
- Larger 200K context window (vs GPT-4.1-mini's 128K)
- ~50ms faster first token latency (150-200ms vs 250-300ms)
"""

import asyncio
import logging
import time
import json
import uuid
from typing import Optional, Dict, Any, List, Deque
from collections import deque

from anthropic import Anthropic

from ...base_service import BaseService
from ...core.event_topics import EventTopics
from ...event_payloads import (
    BaseEventPayload,
    TranscriptionTextPayload,
    LLMResponsePayload,
    IntentPayload,
    ServiceStatus,
    LogLevel
)
from ...llm.command_functions import get_all_function_definitions, function_name_to_model_map
from pydantic import BaseModel, ValidationError


class Message(BaseModel):
    """Model for a conversation message."""
    role: str
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    def model_dump(self, exclude_none: bool = False, **kwargs) -> Dict[str, Any]:
        """Convert the model to a dictionary."""
        data = super().model_dump(**kwargs)
        if exclude_none:
            return {k: v for k, v in data.items() if v is not None}
        return data


class SessionMemory:
    """Manages conversation history and context for the Claude service."""

    def __init__(self, max_tokens: int = 4000, max_messages: int = 20):
        """Initialize session memory with token and message limits."""
        self.messages: Deque[Message] = deque(maxlen=max_messages)
        self.max_tokens = max_tokens
        self.current_token_count = 0
        self.system_prompt: Optional[str] = None

    def add_message(self, role: str, content: str, **kwargs) -> None:
        """Add a message to the conversation history."""
        message = Message(role=role, content=content, **kwargs)

        # Rough token estimation
        estimated_tokens = len(content.split()) + 5

        self.messages.append(message)
        self.current_token_count += estimated_tokens

        # If we exceed token limit, remove oldest messages until under limit
        while self.current_token_count > self.max_tokens and len(self.messages) > 1:
            removed_msg = self.messages.popleft()
            self.current_token_count -= len(removed_msg.content.split()) + 5

    def set_system_prompt(self, prompt: str) -> None:
        """Set the system prompt for the conversation."""
        self.system_prompt = prompt

    def get_messages_for_api(self) -> List[Dict[str, Any]]:
        """Get messages in format ready for Claude API (without system prompt)."""
        result = []

        # Claude uses a dedicated system parameter, not message history
        # So we only return user/assistant messages here
        for msg in self.messages:
            message_dict = msg.model_dump(exclude_none=True)
            result.append(message_dict)

        return result

    def clear(self) -> None:
        """Clear the conversation history."""
        self.messages.clear()
        self.current_token_count = 0


class ClaudeService(BaseService):
    """
    Service for natural language processing using Claude 3.5 Sonnet 4.5.

    Features:
    - Conversation context management with larger 200K token window
    - Tool calling support (same as OpenAI format)
    - Streaming responses via Claude SDK
    - Conversation persistence
    - Intent detection through tool calling
    - Cleaner system prompt handling (dedicated parameter)
    """

    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the Claude service."""
        super().__init__("claude_service", event_bus, logger)

        # Configuration
        self._config = self._load_config(config or {})

        # Session memory
        self._memory = SessionMemory(
            max_tokens=self._config["MAX_TOKENS"],
            max_messages=self._config["MAX_MESSAGES"]
        )

        # Anthropic client
        self._client: Optional[Anthropic] = None

        # Request tracking
        self._request_timestamps: List[float] = []
        self._rate_limit_window = 60  # 1 minute
        self._max_requests_per_window = 100  # Claude has higher limits

        # Conversation state
        self._current_conversation_id: Optional[str] = None

        # Tool management
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._tool_schemas: List[Dict[str, Any]] = []

        # Pre-loaded personas (optimization: avoid disk reads during API calls)
        self._main_persona: Optional[str] = None
        self._verbal_feedback_persona: Optional[str] = None

        # Connection pre-warming (latency optimization)
        self._last_connection_warmup_time: float = 0
        self._warmup_cooldown = 30  # Don't re-warm more than every 30 seconds
        self._connection_warmed = False

        # Vision context (event-driven state management)
        self._current_scene: Optional[str] = None
        self._scene_timestamp: Optional[float] = None
        self._current_person: Optional[str] = None
        self._person_confidence: Optional[float] = None


    def _load_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Load configuration from provided dict."""
        # Anthropic API key is required
        if "ANTHROPIC_API_KEY" not in config:
            self.logger.warning("ANTHROPIC_API_KEY not provided, service will fail to initialize")

        # Try to load DJ R3X persona from config path or common locations
        persona_paths = [
            config.get("PERSONA_FILE_PATH"),
            "dj_r3x-persona.txt",
            "cantina_os/dj_r3x-persona.txt",
            "../dj_r3x-persona.txt",
        ]

        system_prompt = None
        for path in persona_paths:
            if not path:
                continue
            try:
                with open(path, "r") as f:
                    system_prompt = f.read().strip()
                self.logger.info(f"Successfully loaded DJ R3X persona from {path}")
                break
            except Exception as e:
                self.logger.debug(f"Could not load persona from {path}: {str(e)}")

        if not system_prompt:
            self.logger.warning("Failed to load DJ R3X persona from any location, using default")
            system_prompt = "You are DJ R3X, a helpful and enthusiastic Star Wars droid DJ assistant."

        return {
            "ANTHROPIC_API_KEY": config.get("ANTHROPIC_API_KEY", ""),
            # Claude Haiku 4.5 (claude-haiku-4-5-20251001): fastest, best latency for voice interactions
            "MODEL": config.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
            "MAX_TOKENS": config.get("MAX_TOKENS", 40000),  # Increased from 4000 to utilize Claude's 200K window
            "MAX_MESSAGES": config.get("MAX_MESSAGES", 50),  # Increased from 20 for better context
            "TEMPERATURE": config.get("TEMPERATURE", 0.4),  # Lowered from 0.7 for faster, more predictable responses
            "SYSTEM_PROMPT": system_prompt,
            "TIMEOUT": config.get("TIMEOUT", 30),
            "RATE_LIMIT_REQUESTS": config.get("RATE_LIMIT_REQUESTS", 100),
            "STREAMING": config.get("STREAMING", True),
            "ENABLE_INTERIM_STREAMING": config.get("ENABLE_INTERIM_STREAMING", False)  # Disabled by default (saves API calls)
        }

    async def _initialize(self) -> None:
        """Initialize the Claude service."""
        try:
            # Get API key - try config first, then environment
            api_key = self._config.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                # Fallback to environment variable
                import os
                api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in config or environment")

            # Initialize Anthropic client with prompt caching enabled
            self._client = Anthropic(
                api_key=api_key,
                default_headers={
                    "anthropic-beta": "prompt-caching-2024-07-31"  # Enable prompt caching
                }
            )

            # Verify client was initialized with key
            if not self._client:
                raise RuntimeError("Failed to initialize Anthropic client")

            # Initialize rate limiting
            self._max_requests_per_window = self._config["RATE_LIMIT_REQUESTS"]
            self._request_timestamps = []

            # Register command functions
            self._register_command_functions()
            self.logger.info("Registered command functions for intent detection")

            # Pre-load personas to avoid disk I/O during API calls (OPTIMIZATION)
            self._load_personas()

            self.logger.info(
                f"Initialized Claude service with model={self._config['MODEL']}"
            )

        except Exception as e:
            error_msg = f"Failed to initialize Claude service: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise

    async def _start(self) -> None:
        """Start the Claude service following architecture standards."""
        self.logger.info("ClaudeService _start method called - setting up service properly")

        try:
            # Initialize resources
            await self._initialize()

            # Set up event subscriptions
            await self._setup_subscriptions()

            self.logger.info("ClaudeService started successfully")

        except Exception as e:
            error_msg = f"Failed to start Claude service: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise

    async def _cleanup(self) -> None:
        """Clean up Claude service resources."""
        try:
            # Anthropic client doesn't need explicit cleanup
            self._client = None
            self.logger.info("Cleaned up Claude service resources")

        except Exception as e:
            self.logger.error(f"Error cleaning up Claude service resources: {str(e)}")

    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        self.logger.info("ClaudeService setting up event subscriptions.")

        # Subscribe using the EventTopics enum
        asyncio.create_task(self.subscribe(
            EventTopics.TRANSCRIPTION_FINAL,
            self._handle_transcription
        ))
        self.logger.info("ClaudeService: Subscribed to TRANSCRIPTION_FINAL.")

        # Subscribe to interim transcriptions if streaming is enabled
        if self._config["ENABLE_INTERIM_STREAMING"]:
            asyncio.create_task(self.subscribe(
                EventTopics.TRANSCRIPTION_INTERIM,
                self._handle_interim_transcription
            ))
            self.logger.info("ClaudeService: Subscribed to TRANSCRIPTION_INTERIM (interim streaming enabled).")
        else:
            self.logger.info("ClaudeService: TRANSCRIPTION_INTERIM disabled (interim streaming disabled).")

        # Subscribe to vision events (event-driven vision context)
        asyncio.create_task(self.subscribe(
            EventTopics.VISION_SCENE_CAPTURED,
            self._handle_scene_captured
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.VISION_PERSON_DETECTED,
            self._handle_person_detected
        ))
        asyncio.create_task(self.subscribe(
            EventTopics.VISION_PERSON_EXITED,
            self._handle_person_exited
        ))
        self.logger.info("ClaudeService: Subscribed to vision events for context awareness.")

        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STOPPED,
            self._handle_voice_transcript
        ))
        self.logger.info("ClaudeService: Subscribed to VOICE_LISTENING_STOPPED.")

        asyncio.create_task(self.subscribe(
            EventTopics.INTENT_EXECUTION_RESULT,
            self._process_intent_execution_result
        ))
        self.logger.info("ClaudeService: Subscribed to INTENT_EXECUTION_RESULT.")

        asyncio.create_task(self.subscribe(
            EventTopics.DJ_COMMENTARY_REQUEST,
            self._handle_dj_commentary_request
        ))
        self.logger.info("ClaudeService: Subscribed to DJ_COMMENTARY_REQUEST.")


        # Subscribe to ENGAGE command for early connection pre-warming
        asyncio.create_task(self.subscribe(
            EventTopics.SYSTEM_MODE_CHANGED,
            self._handle_mode_changed_for_warmup
        ))
        self.logger.info("ClaudeService: Subscribed to SYSTEM_MODE_CHANGED for connection pre-warming.")

        # Subscribe to MIC_RECORDING_START for backup connection warmup
        asyncio.create_task(self.subscribe(
            EventTopics.MIC_RECORDING_START,
            self._handle_mic_start_for_warmup
        ))
        self.logger.info("ClaudeService: Subscribed to MIC_RECORDING_START for connection refresh.")

    async def _handle_voice_transcript(self, payload: Dict[str, Any]) -> None:
        """Handle text transcript from the VOICE_LISTENING_STOPPED event when recording ends."""
        try:
            # Extract text from payload
            if not payload:
                self.logger.warning("Received empty payload in VOICE_LISTENING_STOPPED event")
                return

            transcript = payload.get("transcript", "")
            if not transcript:
                self.logger.warning("Received empty transcript in VOICE_LISTENING_STOPPED event")
                return

            self.logger.info(f"Processing final transcript from mouse click: {transcript}")

            # Maintain conversation context across voice interactions
            await self._process_with_claude(transcript)

        except Exception as e:
            self.logger.error(f"Error processing voice transcript: {e}", exc_info=True)

    async def _handle_transcription(self, payload: Dict[str, Any]) -> None:
        """Handle transcription text from the speech recognition service."""
        self.logger.debug(f"Received transcription: {str(payload)[:200]}...")

        try:
            # Note: We're not processing individual transcriptions when using mouse clicks
            # The final accumulated transcript will be sent via VOICE_LISTENING_STOPPED event
            self.logger.debug("Individual transcription received but not processing - waiting for mouse click stop event")

        except Exception as e:
            error_msg = f"Error handling transcription: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )

    async def _handle_interim_transcription(self, payload: Dict[str, Any]) -> None:
        """Handle interim transcription text for low-latency responses."""
        try:
            if not payload:
                self.logger.debug("Received empty interim transcription payload")
                return

            interim_text = payload.get("text", "").strip()
            if not interim_text:
                self.logger.debug("Received interim transcription with empty text")
                return

            conversation_id = payload.get("conversation_id")

            # Only process interim if streaming is enabled
            if not self._config["ENABLE_INTERIM_STREAMING"]:
                self.logger.debug("Interim transcription received but streaming disabled")
                return

            self.logger.info(f"Processing interim transcription (streaming): {interim_text[:60]}...")

            # Create a temporary copy of memory without adding to it
            draft_memory_messages = self._memory.get_messages_for_api().copy()

            # Add the interim user input for draft processing
            draft_memory_messages.append({"role": "user", "content": interim_text})

            try:
                if self._config["STREAMING"]:
                    draft_response = await self._stream_draft_claude_response(draft_memory_messages)
                else:
                    draft_response = await self._get_draft_claude_response(draft_memory_messages)

                if draft_response:
                    self.logger.info(f"Emitting interim LLM response: {draft_response[:50]}...")
                    await self._emit_interim_llm_response(draft_response, conversation_id)

            except Exception as e:
                self.logger.debug(f"Error processing interim response: {str(e)}")
                # Don't fail on interim processing - this is optional optimization

        except Exception as e:
            self.logger.error(f"Error handling interim transcription: {str(e)}", exc_info=True)

    async def _process_with_claude(self, user_input: str) -> None:
        """Process user input with Claude model."""
        if not self._current_conversation_id:
            await self.reset_conversation()

        # Check rate limiting
        current_time = time.time()
        self._request_timestamps = [t for t in self._request_timestamps
                                  if current_time - t <= self._rate_limit_window]

        if len(self._request_timestamps) >= self._max_requests_per_window:
            raise Exception("Rate limit exceeded")

        self._request_timestamps.append(current_time)

        # Append vision context to user message
        vision_context = self._build_vision_context_for_message()
        user_input_with_context = user_input + vision_context

        # Add the user's input (with vision context) as a message to memory BEFORE making the API call
        self._memory.add_message("user", user_input_with_context)
        self.logger.info(f"Added user message to memory: {user_input_with_context[:100]}...")

        # Log debug info about the messages being sent
        messages_for_api = self._memory.get_messages_for_api()
        self.logger.info(f"Sending {len(messages_for_api)} messages to Claude API")
        for i, msg in enumerate(messages_for_api):
            self.logger.info(f"Message {i}: role={msg['role']}, content preview={msg['content'][:50]}...")

        try:
            self.logger.info("Making API call to Claude...")
            if self._config["STREAMING"]:
                await self._stream_claude_response(messages_for_api)
            else:
                await self._get_claude_response(messages_for_api)
            self.logger.info("API call completed successfully")
        except Exception as e:
            error_msg = f"Error processing with Claude: {str(e)}"
            self.logger.error(error_msg)
            self.logger.error(f"Request details: Model={self._config['MODEL']}")
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise

    async def _get_claude_response(self, messages: List[Dict[str, Any]]) -> None:
        """Get a non-streaming response from Claude API."""
        if not self._client:
            raise RuntimeError("No Anthropic client initialized")

        self.logger.info("Making non-streaming API request to Claude")

        try:
            # Use static system prompt (vision context is now in user messages)
            # OPTIMIZATION: Use prompt caching for system prompt and tools
            # This reduces latency on subsequent requests by ~100-200ms
            system_prompt_with_cache = [
                {
                    "type": "text",
                    "text": self._config["SYSTEM_PROMPT"],
                    "cache_control": {"type": "ephemeral"}  # Enables prompt caching
                }
            ]

            response = self._client.messages.create(
                model=self._config["MODEL"],
                max_tokens=1024,
                system=system_prompt_with_cache,  # Use cached static system prompt
                messages=messages,
                temperature=self._config["TEMPERATURE"],
                tools=self._get_tool_schemas_with_cache()  # Tools with cache_control on last tool
            )

            self.logger.info(f"Successfully received response from Claude")

            # Log token usage for cost/performance tracking
            if hasattr(response, 'usage'):
                usage = response.usage
                self.logger.info(f"📊 TOKEN USAGE - Input: {usage.input_tokens}, Output: {usage.output_tokens}, Total: {usage.input_tokens + usage.output_tokens}")
                if hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens:
                    self.logger.info(f"💾 CACHE CREATED: {usage.cache_creation_input_tokens} tokens")
                if hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens:
                    self.logger.info(f"⚡ CACHE HIT: {usage.cache_read_input_tokens} tokens saved")

            # Extract content from response
            message_content = response.content[0].text if response.content else ""

            # Check for tool calls
            tool_calls = []
            for block in response.content:
                if hasattr(block, 'type') and block.type == 'tool_use':
                    tool_calls.append({
                        "type": "function",
                        "id": block.id,
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input)
                        }
                    })

            # Add assistant message to memory ONLY if there's text content
            # If Claude only returned tool calls with no text, don't add empty message
            # NOTE: tool_calls should NOT be stored in messages sent back to Claude API
            # Claude API only accepts role, content, and name fields in messages
            if message_content or not tool_calls:
                self._memory.add_message(
                    role="assistant",
                    content=message_content
                )

            # Process tool calls if any
            if tool_calls:
                await self._process_tool_calls(tool_calls, message_content)

            # Emit response
            self.logger.info(f"Emitting LLM response: {message_content[:50] if message_content else ''}...")
            await self._emit_llm_response(
                message_content,
                tool_calls=tool_calls if tool_calls else None
            )
        except Exception as e:
            self.logger.error(f"Error in _get_claude_response: {str(e)}")
            raise

    async def _stream_claude_response(self, messages: List[Dict[str, Any]]) -> None:
        """Stream responses from Claude API."""
        if not self._client:
            raise RuntimeError("No Anthropic client initialized")

        self.logger.info("Making streaming API request to Claude")

        try:
            full_content = ""
            tool_calls = []

            # Use static system prompt (vision context is now in user messages)
            # OPTIMIZATION: Use prompt caching for system prompt and tools
            # This reduces latency on subsequent requests by ~100-200ms
            system_prompt_with_cache = [
                {
                    "type": "text",
                    "text": self._config["SYSTEM_PROMPT"],
                    "cache_control": {"type": "ephemeral"}  # Enables prompt caching
                }
            ]

            # Use streaming with Claude
            with self._client.messages.stream(
                model=self._config["MODEL"],
                max_tokens=1024,
                system=system_prompt_with_cache,  # Use cached static system prompt
                messages=messages,
                temperature=self._config["TEMPERATURE"],
                tools=self._get_tool_schemas_with_cache()  # Tools with cache_control on last tool
            ) as stream:
                chunk_count = 0
                current_tool_use = None

                for text in stream.text_stream:
                    if text:
                        full_content += text
                        chunk_count += 1
                        if chunk_count % 10 == 0:
                            self.logger.debug(f"Processed {chunk_count} chunks, current content: {full_content[:50]}...")
                        await self._emit_llm_stream_chunk(text, is_complete=False)

                # Get the final message after streaming completes
                final_message = stream.get_final_message()

                # Log token usage for cost/performance tracking
                if hasattr(final_message, 'usage'):
                    usage = final_message.usage
                    self.logger.info(f"📊 TOKEN USAGE (streaming) - Input: {usage.input_tokens}, Output: {usage.output_tokens}, Total: {usage.input_tokens + usage.output_tokens}")
                    if hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens:
                        self.logger.info(f"💾 CACHE CREATED: {usage.cache_creation_input_tokens} tokens")
                    if hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens:
                        self.logger.info(f"⚡ CACHE HIT: {usage.cache_read_input_tokens} tokens saved")

                # Extract tool calls from final message
                for block in final_message.content:
                    if hasattr(block, 'type') and block.type == 'tool_use':
                        tool_calls.append({
                            "type": "function",
                            "id": block.id,
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.input)
                            }
                        })

            self.logger.info(f"Completed streaming response with {chunk_count} chunks")
            self.logger.info(f"Processed {len(tool_calls)} tool calls")

            # Add complete message to memory ONLY if there's text content
            # If Claude only returned tool calls with no text, don't add empty message
            # NOTE: tool_calls should NOT be stored in messages sent back to Claude API
            # Claude API only accepts role, content, and name fields in messages
            if full_content or not tool_calls:
                self._memory.add_message(
                    role="assistant",
                    content=full_content
                )

            # Process tool calls if any
            if tool_calls:
                await self._process_tool_calls(tool_calls, full_content)

            # Emit the final LLM response
            self.logger.info(f"Emitting final LLM response with text: '{full_content[:50]}...' and {len(tool_calls)} tool calls")
            await self._emit_llm_response(full_content, tool_calls=tool_calls if tool_calls else None)

        except Exception as e:
            self.logger.error(f"Error in _stream_claude_response: {str(e)}")
            raise

    # Vision event handlers (event-driven state management)

    async def _handle_scene_captured(self, payload: Dict[str, Any]):
        """Handle VISION_SCENE_CAPTURED event."""
        import time
        self._current_scene = payload.get("description", "")
        self._scene_timestamp = time.time()
        self.logger.info(f"Scene updated: {self._current_scene[:100]}...")

    async def _handle_person_detected(self, payload: Dict[str, Any]):
        """Handle VISION_PERSON_DETECTED event."""
        self._current_person = payload.get("name", "Unknown")
        self._person_confidence = payload.get("confidence", 0.0)
        self.logger.info(f"Person detected: {self._current_person} (confidence: {self._person_confidence:.2f})")

    async def _handle_person_exited(self, payload: Dict[str, Any]):
        """Handle VISION_PERSON_EXITED event."""
        self._current_person = None
        self._person_confidence = None
        self.logger.info(f"Person exited: {payload.get('name', 'Unknown')}")

    def _build_vision_context_for_message(self) -> str:
        """Build vision context from internal state (event-driven)."""
        import time

        # Check if we have any vision context
        if not self._current_scene and not self._current_person:
            return ""

        # Build context section
        context_parts = []

        # Add scene if available and recent (within last 60 seconds)
        if self._current_scene and self._scene_timestamp:
            age_seconds = time.time() - self._scene_timestamp
            if age_seconds < 60:  # Scene context valid for 60 seconds
                context_parts.append(f"What you can see - {self._current_scene}")

        # Add person if currently present
        if self._current_person:
            context_parts.append(f"Speaking with: {self._current_person}")

        # Return formatted context or empty string
        if not context_parts:
            return ""

        context_section = "\n\n[System observation: " + " | ".join(context_parts) + "]"
        self.logger.debug(f"Vision context: {context_section[:100]}...")
        return context_section

    async def _emit_llm_response(
        self,
        response_text: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Emit a complete LLM response event."""
        # Create response payload
        payload = LLMResponsePayload(
            text=response_text,
            tool_calls=tool_calls,
            is_complete=True,
            conversation_id=self._current_conversation_id
        )

        # Emit the event
        self.logger.info(f"Emitting LLM_RESPONSE event with {len(response_text)} chars")
        await self.emit(EventTopics.LLM_RESPONSE, payload)

    async def _emit_llm_stream_chunk(
        self,
        chunk_text: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        is_complete: bool = False
    ) -> None:
        """Emit an LLM response stream chunk event."""
        # Create chunk payload
        payload = LLMResponsePayload(
            text=chunk_text,
            tool_calls=tool_calls,
            is_complete=is_complete,
            conversation_id=self._current_conversation_id
        )

        # Add to memory if this is the complete message
        if is_complete:
            self._memory.add_message("assistant", chunk_text)

        # Emit the event
        await self.emit(EventTopics.LLM_RESPONSE, payload)

    async def _emit_interim_llm_response(
        self,
        response_text: str,
        conversation_id: Optional[str] = None
    ) -> None:
        """Emit an interim LLM response event."""
        payload = LLMResponsePayload(
            text=response_text,
            tool_calls=None,
            is_complete=False,
            conversation_id=conversation_id
        )

        # Emit interim event - NEVER persist to memory
        self.logger.debug(f"Emitting interim LLM response: {response_text[:40]}...")
        await self.emit(EventTopics.LLM_RESPONSE_TEXT_INTERIM, payload)

    async def _get_draft_claude_response(self, messages: List[Dict[str, Any]]) -> str:
        """Get a non-streaming draft response from Claude API for interim processing."""
        if not self._client:
            raise RuntimeError("No Anthropic client initialized")

        try:
            response = self._client.messages.create(
                model=self._config["MODEL"],
                max_tokens=150,  # Limit draft responses to be brief
                system=self._config["SYSTEM_PROMPT"],
                messages=messages,
                temperature=self._config["TEMPERATURE"]
            )

            return response.content[0].text if response.content else ""

        except Exception as e:
            self.logger.debug(f"Error in _get_draft_claude_response: {str(e)}")
            return ""

    async def _stream_draft_claude_response(self, messages: List[Dict[str, Any]]) -> str:
        """Stream a draft response from Claude API for interim processing."""
        if not self._client:
            raise RuntimeError("No Anthropic client initialized")

        try:
            full_content = ""

            with self._client.messages.stream(
                model=self._config["MODEL"],
                max_tokens=150,  # Limit draft responses to be brief
                system=self._config["SYSTEM_PROMPT"],
                messages=messages,
                temperature=self._config["TEMPERATURE"]
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        full_content += text

            return full_content

        except Exception as e:
            self.logger.debug(f"Error in _stream_draft_claude_response: {str(e)}")
            return ""

    def register_tool(self, tool_schema: Dict[str, Any]) -> None:
        """Register a tool for use with Claude model.

        Converts OpenAI function-calling format to Claude's native tool format:
        OpenAI format: {"type": "function", "function": {...}}
        Claude format: {"name": ..., "description": ..., "input_schema": {...}}
        """
        # Extract function definition from OpenAI format
        if "function" in tool_schema:
            func_def = tool_schema["function"]
            tool_name = func_def["name"]

            # Convert to Claude format
            claude_tool = {
                "name": func_def["name"],
                "description": func_def.get("description", ""),
                "input_schema": func_def.get("parameters", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            }
        else:
            # Already in Claude format
            tool_name = tool_schema["name"]
            claude_tool = tool_schema

        self._tools[tool_name] = claude_tool
        self._tool_schemas = list(self._tools.values())
        self.logger.info(f"Registered tool: {tool_name}")

    def _get_tool_schemas_with_cache(self) -> Optional[List[Dict[str, Any]]]:
        """Get tool schemas with cache_control on the last tool for prompt caching.

        Per Anthropic docs, cache_control should only be on the LAST tool.
        This ensures all tool definitions are cached as a single prefix.
        """
        if not self._tool_schemas:
            return None

        # Deep copy to avoid modifying original
        import copy
        tools_copy = copy.deepcopy(self._tool_schemas)

        # Add cache_control to last tool only
        if tools_copy:
            tools_copy[-1]["cache_control"] = {"type": "ephemeral"}

        return tools_copy

    async def reset_conversation(self) -> None:
        """Reset the conversation state with a new ID."""
        self._current_conversation_id = str(uuid.uuid4())
        self._memory.clear()

        # Initialize with system prompt
        if self._config["SYSTEM_PROMPT"]:
            self._memory.set_system_prompt(self._config["SYSTEM_PROMPT"])

        self.logger.info(f"Reset conversation with new ID: {self._current_conversation_id}")

    @property
    def current_conversation_id(self) -> Optional[str]:
        """Get the current conversation ID."""
        return self._current_conversation_id

    def _load_personas(self) -> None:
        """Pre-load personas at startup to avoid disk I/O during API calls (OPTIMIZATION)."""
        # Load main DJ R3X persona
        persona_paths = [
            "dj_r3x-persona.txt",
            "cantina_os/dj_r3x-persona.txt",
            "../dj_r3x-persona.txt",
        ]

        for path in persona_paths:
            try:
                with open(path, "r") as f:
                    self._main_persona = f.read().strip()
                self.logger.debug(f"Pre-loaded main persona from {path}")
                break
            except Exception:
                pass

        # Load verbal feedback persona
        feedback_paths = [
            "dj_r3x-verbal-feedback-persona.txt",
            "cantina_os/dj_r3x-verbal-feedback-persona.txt",
            "../dj_r3x-verbal-feedback-persona.txt",
        ]

        for path in feedback_paths:
            try:
                with open(path, "r") as f:
                    self._verbal_feedback_persona = f.read().strip()
                self.logger.debug(f"Pre-loaded verbal feedback persona from {path}")
                break
            except Exception:
                pass

        self.logger.info(f"Persona pre-loading complete: main={bool(self._main_persona)}, feedback={bool(self._verbal_feedback_persona)}")

    def _register_command_functions(self) -> None:
        """Register all command functions for intent detection."""
        function_definitions = get_all_function_definitions()
        for function_def in function_definitions:
            self.register_tool(function_def)

        self.logger.info(f"Registered {len(function_definitions)} command functions")

    async def _process_tool_calls(self, tool_calls: List[Dict[str, Any]], response_text: str) -> None:
        """Process and emit intents from tool calls."""
        if not tool_calls:
            self.logger.warning("No tool calls to process - skipping intent emission")
            return

        self.logger.info(f"Processing {len(tool_calls)} tool calls")
        processed_count = 0

        for i, tool_call in enumerate(tool_calls):
            try:
                if tool_call["type"] != "function":
                    self.logger.warning(f"Unsupported tool call type: {tool_call['type']}")
                    continue

                function_name = tool_call["function"]["name"]
                function_args_str = tool_call["function"]["arguments"]

                if not function_name:
                    self.logger.warning(f"Tool call {i+1} has empty function name - skipping")
                    continue

                if not function_args_str:
                    self.logger.warning(f"Tool call {i+1} ({function_name}) has empty arguments - using empty dict")
                    function_args = {}
                else:
                    self.logger.info(f"Processing tool call {i+1}/{len(tool_calls)}: {function_name}")

                    try:
                        # Parse the arguments JSON (Claude already provides valid JSON)
                        function_args = json.loads(function_args_str)
                        self.logger.debug(f"Parsed arguments for {function_name}: {json.dumps(function_args)[:100]}...")

                        # Validate arguments against the Pydantic model if available
                        model_map = function_name_to_model_map()
                        if function_name in model_map:
                            param_model = model_map[function_name]
                            # Validate the parameters
                            validated_params = param_model(**function_args)
                            function_args = validated_params.model_dump()
                            self.logger.info(f"Validated parameters for function {function_name}")
                        else:
                            self.logger.warning(f"No parameter model found for function {function_name}")

                        # Create and emit the intent payload
                        intent_payload = IntentPayload(
                            intent_name=function_name,
                            parameters=function_args,
                            original_text=response_text,
                            conversation_id=self._current_conversation_id
                        )

                        self.logger.info(f"Emitting intent: {function_name} with params: {function_args}")
                        await self.emit(EventTopics.INTENT_DETECTED, intent_payload)
                        self.logger.info(f"Successfully emitted {function_name} intent")
                        processed_count += 1

                    except json.JSONDecodeError:
                        self.logger.error(f"Invalid JSON in function arguments: {function_args_str}")
                    except ValidationError as e:
                        self.logger.error(f"Parameter validation error for {function_name}: {e}")

            except Exception as e:
                self.logger.error(f"Error processing tool call: {e}")

        self.logger.info(f"Completed processing {processed_count}/{len(tool_calls)} tool calls successfully")

    async def _process_intent_execution_result(self, payload: Dict[str, Any]) -> None:
        """Process intent execution results to generate verbal feedback."""
        try:
            # Convert dict to Pydantic model for validation
            from ...event_payloads import IntentExecutionResultPayload
            result_payload = IntentExecutionResultPayload(**payload)

            # Extract information from the payload
            intent_name = result_payload.intent_name
            parameters = result_payload.parameters
            success = result_payload.success
            result = result_payload.result
            tool_call_id = result_payload.tool_call_id

            self.logger.info(f"Processing execution result for intent: {intent_name}")
            self.logger.info(f"Result success: {success}, parameters: {parameters}")

            # Add the tool response to conversation memory
            response_content = json.dumps(result) if result else "Action completed successfully."
            if not success and result_payload.error_message:
                response_content = f"Error: {result_payload.error_message}"

            # Validate that response_content is not empty before adding to memory
            # Claude API rejects messages with empty content
            if not response_content or not response_content.strip():
                self.logger.warning(f"Tool result content is empty for {intent_name}, using default message")
                response_content = f"{intent_name} completed."

            # Visual-only tools that shouldn't be part of conversation
            visual_only_tools = {"set_eye_color", "set_eye_pattern", "eye_pattern"}

            # Add tool response as a message (skip for visual-only tools)
            if intent_name not in visual_only_tools:
                if tool_call_id:
                    self.logger.info(f"Adding tool response for tool_call_id: {tool_call_id}")
                    self._memory.add_message(
                        role="user",  # Claude uses "user" role for tool results
                        content=response_content,
                        name=intent_name
                    )
                else:
                    self.logger.warning("No tool_call_id in payload, using generic tool response")
                    self._memory.add_message(
                        role="user",
                        content=f"Tool execution result for {intent_name}: {response_content}"
                    )

                # Generate verbal response in background WITHOUT waiting
                # This allows the tool to execute immediately while DJ commentary plays over it
                asyncio.create_task(self._get_verbal_response_for_intent(intent_name, parameters, result, success))
                self.logger.info(f"Started background verbal response generation for {intent_name}")
            else:
                # For visual-only tools, just log that we're skipping verbal feedback
                self.logger.info(f"Skipping verbal feedback for visual-only tool: {intent_name}")

        except Exception as e:
            self.logger.error(f"Error processing intent execution result: {e}", exc_info=True)

    async def _get_verbal_response_for_intent(
        self,
        intent_name: str,
        parameters: Dict[str, Any],
        result: Dict[str, Any],
        success: bool
    ) -> None:
        """Generate a verbal response about an executed intent."""
        self.logger.info(f"Generating verbal response for intent: {intent_name}")

        try:
            if not self._client:
                raise RuntimeError("No Anthropic client initialized")

            # OPTIMIZATION: Use pre-loaded verbal feedback persona (loaded at startup)
            verbal_feedback_persona = self._verbal_feedback_persona

            if not verbal_feedback_persona:
                self.logger.warning("Failed to load verbal feedback persona, using default instruction")
                verbal_feedback_persona = (
                    f"You are DJ R-3X, a Star Wars droid DJ. Generate a brief verbal response about the "
                    f"{intent_name} action that was just performed. Be natural, conversational, and specific "
                    f"about what was done. Keep your response short and enthusiastic as if you're DJ R3X speaking to a guest."
                )

            # Prepare intent details
            intent_details = (
                f"Intent executed: {intent_name}\n"
                f"Parameters: {json.dumps(parameters)}\n"
                f"Result: {json.dumps(result)}\n"
                f"Success: {success}"
            )

            messages = [
                {"role": "user", "content": intent_details}
            ]

            response = self._client.messages.create(
                model=self._config["MODEL"],
                max_tokens=200,
                system=verbal_feedback_persona,
                messages=messages,
                temperature=0.7
            )

            verbal_response = response.content[0].text if response.content else "Action completed successfully."
            self.logger.info(f"Generated verbal response: {verbal_response}")

            # Emit the verbal response
            await self._emit_llm_response(verbal_response)

        except Exception as e:
            self.logger.error(f"Error generating verbal response: {e}", exc_info=True)
            # Emit a fallback response
            fallback_msg = f"Action completed successfully."
            await self._emit_llm_response(fallback_msg)

    async def _handle_dj_commentary_request(self, payload: Dict[str, Any]) -> None:
        """Handle DJ commentary generation requests from BrainService."""
        try:
            self.logger.info("Handling DJ_COMMENTARY_REQUEST")

            if not self._client:
                raise RuntimeError("No Anthropic client initialized")

            # Import the request payload model
            from ...core.event_schemas import DjCommentaryRequestPayload

            # Parse the request payload
            request_payload = DjCommentaryRequestPayload(**payload)
            context = request_payload.context
            current_track = request_payload.current_track
            next_track = request_payload.next_track
            persona = request_payload.persona
            request_id = request_payload.request_id

            self.logger.info(f"Generating commentary for request_id: {request_id}")
            self.logger.info(f"Context: {context}, Next track: {next_track.title if next_track else 'None'}")

            # Create commentary prompt based on context and track information
            # Context instructions layer on top of main persona
            if context == "transition" and current_track and next_track:
                # Detect music source based on filepath
                is_spotify = current_track.filepath.startswith("spotify:") if current_track.filepath else False

                if is_spotify:
                    # Spotify tracks - Claude knows these artists/songs!
                    user_prompt = f"""
SITUATION: You're DJ R3X at Oga's Cantina, transitioning between tracks. You just finished playing "{current_track.title}" by {current_track.artist}, and you're about to drop "{next_track.title}" by {next_track.artist}.

YOUR MISSION:
Generate a brief DJ transition (2-3 sentences max) that:
1. **FINDS A CONNECTION** between the two songs - this is KEY! Look for:
   - Similar vibes/energy levels
   - Both artists from same era/genre
   - Contrasting styles that create an interesting shift
   - Thematic connections (love songs, party songs, emotional ballads, etc.)
   - Anything creative that ties them together!

2. **References the artists/songs naturally** - Don't just say titles! You can mention:
   - The vibe/feel of the song that just played
   - What makes the next artist/song special
   - How the crowd is reacting

3. **Stays 100% in character as DJ R3X**:
   - Quirky former pilot droid personality
   - Enthusiastic and slightly bumbling
   - Uses droid/Star Wars flavor ("circuits", "hyperspace", "cantina")
   - NEVER break character (no Earth references, no Spotify mentions)

CONNECTION EXAMPLES (match this style):
✅ "Man, that Kacey Musgraves track had us all in a GROOVE! But hold on - we're kicking it up a notch because Taylor Swift knows how to tug those heartstrings too. Here comes 'Never Grow Up'!"
✅ "Third Eye Blind bringing that 90s energy! And you know what? We're STAYING in that era - WALK THE MOON is about to get this whole cantina moving with 'Shut Up and Dance'!"
✅ "Whoa, Jonas Brothers had us all singing along! Alright, switching gears from party mode to something a little more magical - Dove Cameron's 'My Once Upon a Time' is coming in smooth!"
✅ "That Ben Rector track hit DIFFERENT, didn't it? Keeping those feel-good vibes rolling - here's some Judah & the Lion to keep your spirits HIGH!"

❌ AVOID: "That was '{current_track.title}'. Next is '{next_track.title}'." (Too robotic!)
❌ AVOID: Long explanations or music theory lessons (Keep it punchy!)

AUDIO TAGS (ElevenLabs V3 - use sparingly):
- [excited]: For upbeat, high-energy transitions (most common)
- [whispers]: For smooth transitions to slower/intimate songs

Remember: FIND THE CONNECTION, keep it NATURAL, stay IN CHARACTER!
"""
                else:
                    # Local cantina music - more generic/Star Wars themed
                    user_prompt = f"""
SITUATION: You're DJ R3X at Oga's Cantina, transitioning between classic cantina tracks. You just played "{current_track.title}" and you're about to play "{next_track.title}".

INSTRUCTIONS:
Generate a brief, energetic DJ transition (2-3 sentences max) that:
- Acknowledges the track that just played with enthusiasm
- Introduces the next track
- References the cantina atmosphere (crowd energy, dancing, drinks flowing, etc.)
- Stays in character as a quirky droid DJ

CANTINA-SPECIFIC EXAMPLES:
✅ "That track had everyone from Coruscant to Tatooine moving! Alright folks, let's keep this cantina ROCKING - next up is '{next_track.title}'!"
✅ "Whoa, I saw some SERIOUS dancing out there! Keeping those good vibes flowing with '{next_track.title}' - this one's a cantina favorite!"
✅ "My circuits are BUZZING from that energy! Alright, switching frequencies - here comes '{next_track.title}'!"

AUDIO TAGS (optional):
- [excited]: For upbeat moments
- [whispers]: For smooth transitions

Keep it punchy and fun!
"""
            elif context == "intro" and current_track:
                # Detect music source based on filepath
                is_spotify = current_track.filepath.startswith("spotify:") if current_track.filepath else False

                if is_spotify:
                    # Spotify track - Claude knows the artist!
                    user_prompt = f"""
SITUATION: You're DJ R3X at Oga's Cantina, introducing "{current_track.title}" by {current_track.artist}.

INSTRUCTIONS:
Generate a brief, enthusiastic introduction (2-3 sentences max) that:
- References something specific about this artist or song (you know their music!)
- Builds genuine excitement for what's about to play
- Stays in character as DJ R3X (quirky droid, enthusiastic, Star Wars flavor)

EXAMPLES (match this style):
✅ "[excited] Alright cantina crew, you're gonna LOVE this one! Taylor Swift knows how to hit you right in the feels - here's 'Never Grow Up'!"
✅ "Okay okay, who's ready to DANCE?! WALK THE MOON coming in HOT with 'Shut Up and Dance' - this one's IMPOSSIBLE to sit still for!"
✅ "Bringing some SERIOUS chill vibes right now - Kacey Musgraves with 'Slow Burn'. Trust me, this one's gonna groove!"

AUDIO TAGS (optional):
- [excited]: For upbeat, energetic songs (most common)
- [whispers]: For intimate, slower moments

Keep it punchy and fun!
"""
                else:
                    # Local cantina music
                    user_prompt = f"""
SITUATION: You're DJ R3X introducing a classic cantina track: "{current_track.title}".

INSTRUCTIONS:
Generate a brief introduction (2-3 sentences max) that:
- Builds excitement for this cantina classic
- References the cantina atmosphere
- Stays in character as a quirky droid DJ

EXAMPLES:
✅ "[excited] Alright folks, time for a cantina CLASSIC! Get ready for '{current_track.title}' - this one always gets the crowd moving!"
✅ "Oh THIS is a good one! '{current_track.title}' coming at you - I can already see folks heading to the dance floor!"

Keep it energetic!
"""
            else:
                # Fallback prompt
                user_prompt = "Generate a brief DJ commentary for the music. Keep it energetic and in character as DJ R3X, 2-3 sentences max."

            self.logger.info(f"Commentary prompt created for {context} context")

            response = self._client.messages.create(
                model=self._config["MODEL"],
                max_tokens=150,
                system=persona,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.8
            )

            commentary_text = response.content[0].text if response.content else ""
            self.logger.info(f"Generated commentary: {commentary_text}")

            # Emit the commentary response
            from ...core.event_schemas import GptCommentaryResponsePayload

            commentary_response = GptCommentaryResponsePayload(
                timestamp=time.time(),
                request_id=request_id,
                commentary_text=commentary_text,
                is_partial=False,
                context=context
            )

            await self.emit(
                EventTopics.GPT_COMMENTARY_RESPONSE,
                commentary_response.model_dump()
            )

            self.logger.info(f"Emitted GPT_COMMENTARY_RESPONSE for request_id: {request_id}")

        except Exception as e:
            self.logger.error(f"Error handling DJ commentary request: {e}", exc_info=True)

            # Emit error response if possible
            try:
                request_id = payload.get("request_id", "unknown")
                from ...core.event_schemas import GptCommentaryResponsePayload

                error_response = GptCommentaryResponsePayload(
                    timestamp=time.time(),
                    request_id=request_id,
                    commentary_text="",
                    is_partial=True,
                    context=payload.get("context", "unknown")
                )

                await self.emit(
                    EventTopics.GPT_COMMENTARY_RESPONSE,
                    error_response.model_dump()
                )
            except Exception as emit_error:
                self.logger.error(f"Failed to emit error response: {emit_error}")

    async def _warmup_connection(self) -> None:
        """
        Warm up the Claude API connection by making a lightweight request.
        This reduces latency on the next actual request by establishing the connection early.
        """
        current_time = time.time()

        # Check cooldown - don't re-warm too frequently
        if current_time - self._last_connection_warmup_time < self._warmup_cooldown:
            self.logger.debug("Connection warmup skipped (cooldown period)")
            return

        try:
            self.logger.info("🔄 Warming up Claude API connection for faster response latency")
            self._last_connection_warmup_time = current_time

            # Make a lightweight API call to establish connection
            # Use a very simple system prompt and short message to minimize tokens
            self._client.messages.create(
                model=self._config["MODEL"],
                max_tokens=10,
                system="You are a helpful assistant.",
                messages=[
                    {
                        "role": "user",
                        "content": "hi"
                    }
                ],
                temperature=0.1,
                timeout=5  # Short timeout for warmup
            )

            self._connection_warmed = True
            self.logger.info("✅ Claude API connection warmed up successfully")

        except Exception as e:
            # Don't fail the service if warmup fails - log but continue
            self.logger.warning(f"Connection warmup failed (non-critical): {str(e)}")
            self._connection_warmed = False

    async def _handle_mode_changed_for_warmup(self, payload: Dict[str, Any]) -> None:
        """
        Handle SYSTEM_MODE_CHANGED event to pre-warm connection when entering INTERACTIVE mode.
        This is triggered by the 'engage' command and happens well before the user clicks to record.
        """
        try:
            mode = payload.get("mode", "")
            if mode == "INTERACTIVE":
                self.logger.info("Mode changed to INTERACTIVE - pre-warming Claude API connection")
                # Run warmup in background without blocking
                asyncio.create_task(self._warmup_connection())
        except Exception as e:
            self.logger.error(f"Error in mode change warmup handler: {e}")

    async def _handle_mic_start_for_warmup(self, payload: Dict[str, Any]) -> None:
        """
        Handle MIC_RECORDING_START event to refresh connection before user finishes speaking.
        This is a safety net in case the connection was idle or dropped.
        By the time recording stops, connection will be fresh and ready for API call.
        """
        try:
            self.logger.info("Mic recording started - refreshing Claude API connection")
            # Run warmup in background without blocking recording
            asyncio.create_task(self._warmup_connection())
        except Exception as e:
            self.logger.error(f"Error in mic start warmup handler: {e}")
