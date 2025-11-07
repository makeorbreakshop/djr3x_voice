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
            # Claude 3.5 Sonnet 4.5: 200-250ms first token (faster than GPT-4.1-mini)
            "MODEL": config.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
            "MAX_TOKENS": config.get("MAX_TOKENS", 4000),
            "MAX_MESSAGES": config.get("MAX_MESSAGES", 20),
            "TEMPERATURE": config.get("TEMPERATURE", 0.7),
            "SYSTEM_PROMPT": system_prompt,
            "TIMEOUT": config.get("TIMEOUT", 30),
            "RATE_LIMIT_REQUESTS": config.get("RATE_LIMIT_REQUESTS", 100),
            "STREAMING": config.get("STREAMING", True),
            "ENABLE_INTERIM_STREAMING": config.get("ENABLE_INTERIM_STREAMING", True)
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

            # Initialize Anthropic client - pass api_key explicitly to ensure it's set
            self._client = Anthropic(api_key=api_key)

            # Verify client was initialized with key
            if not self._client:
                raise RuntimeError("Failed to initialize Anthropic client")

            # Initialize rate limiting
            self._max_requests_per_window = self._config["RATE_LIMIT_REQUESTS"]
            self._request_timestamps = []

            # Register command functions
            self._register_command_functions()
            self.logger.info("Registered command functions for intent detection")

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

        # Add the user's input as a message to memory BEFORE making the API call
        self._memory.add_message("user", user_input)
        self.logger.info(f"Added user message to memory: {user_input}")

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
            response = self._client.messages.create(
                model=self._config["MODEL"],
                max_tokens=1024,
                system=self._config["SYSTEM_PROMPT"],  # Use dedicated system parameter
                messages=messages,
                temperature=self._config["TEMPERATURE"],
                tools=self._tool_schemas if self._tool_schemas else None
            )

            self.logger.info(f"Successfully received response from Claude")

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

            # Add assistant message to memory
            self._memory.add_message(
                role="assistant",
                content=message_content,
                tool_calls=tool_calls if tool_calls else None
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

            # Use streaming with Claude
            with self._client.messages.stream(
                model=self._config["MODEL"],
                max_tokens=1024,
                system=self._config["SYSTEM_PROMPT"],  # Use dedicated system parameter
                messages=messages,
                temperature=self._config["TEMPERATURE"],
                tools=self._tool_schemas if self._tool_schemas else None
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

            # Add complete message to memory
            self._memory.add_message(
                role="assistant",
                content=full_content,
                tool_calls=tool_calls if tool_calls else None
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
        """Register a tool for use with Claude model."""
        tool_name = tool_schema["function"]["name"]
        self._tools[tool_name] = tool_schema
        self._tool_schemas = list(self._tools.values())
        self.logger.info(f"Registered tool: {tool_name}")

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

            # Add tool response as a message
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

            # Now generate a verbal response about the action
            await self._get_verbal_response_for_intent(intent_name, parameters, result, success)

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

            # Load the specialized verbal feedback persona
            verbal_feedback_persona = None
            persona_paths = [
                "dj_r3x-verbal-feedback-persona.txt",
                "cantina_os/dj_r3x-verbal-feedback-persona.txt",
                "../dj_r3x-verbal-feedback-persona.txt",
            ]

            for path in persona_paths:
                try:
                    with open(path, "r") as f:
                        verbal_feedback_persona = f.read().strip()
                    self.logger.info(f"Successfully loaded verbal feedback persona from {path}")
                    break
                except Exception as e:
                    self.logger.debug(f"Could not load verbal feedback persona from {path}: {str(e)}")

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
            if context == "transition" and current_track and next_track:
                user_prompt = f"""
You are transitioning from "{current_track.title}" to "{next_track.title}".

Generate a brief, energetic DJ transition commentary (2-3 sentences max) that:
- Acknowledges the current track ending
- Introduces the next track with enthusiasm
- Maintains the Star Wars cantina atmosphere
- Sounds natural and conversational like a real DJ

Keep it concise and punchy - this will play over a crossfade.
"""
            elif context == "intro" and current_track:
                user_prompt = f"""
Generate a brief, enthusiastic introduction (2-3 sentences max) for the track "{current_track.title}" by {current_track.artist}.

Make it sound like DJ R3X is introducing this track to the cantina crowd.
Keep it energetic but concise.
"""
            else:
                # Fallback prompt
                user_prompt = "Generate a brief DJ commentary for the music. Keep it energetic and in character as DJ R3X."

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
