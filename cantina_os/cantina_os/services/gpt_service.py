"""
GPT Service

This service handles interactions with OpenAI's GPT models for natural language processing.
It receives transcriptions from the DeepgramTranscriptionService, processes them via OpenAI's API,
and emits response events. It also manages conversation context and session memory.
"""

import asyncio
import logging
import time
import json
import uuid
from typing import Optional, Dict, Any, List, Deque
from collections import deque
import aiohttp
from pydantic import BaseModel, ValidationError

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    BaseEventPayload,
    TranscriptionTextPayload,
    LLMResponsePayload,
    IntentPayload,
    ServiceStatus,
    LogLevel
)
from ..llm.command_functions import get_all_function_definitions, function_name_to_model_map

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
    """Manages conversation history and context for the GPT service."""
    
    def __init__(self, max_tokens: int = 4000, max_messages: int = 20):
        """Initialize session memory with token and message limits."""
        self.messages: Deque[Message] = deque(maxlen=max_messages)
        self.max_tokens = max_tokens
        self.current_token_count = 0
        self.system_prompt: Optional[str] = None
        
    def add_message(self, role: str, content: str, **kwargs) -> None:
        """
        Add a message to the conversation history.
        
        Args:
            role: The role of the message sender (system, user, assistant, tool)
            content: The message content
            **kwargs: Additional message attributes
        """
        message = Message(role=role, content=content, **kwargs)
        
        # Rough token estimation (can be refined with proper tokenization)
        # This is just a simple approximation
        estimated_tokens = len(content.split()) + 5  # +5 for message overhead
        
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
        """Get messages in format ready for OpenAI API."""
        result = []
        
        # Add system prompt if available
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
            
        # Add conversation history
        for msg in self.messages:
            message_dict = msg.model_dump(exclude_none=True)
            result.append(message_dict)
            
        return result
        
    def clear(self) -> None:
        """Clear the conversation history."""
        self.messages.clear()
        self.current_token_count = 0

class GPTService(BaseService):
    """
    Service for natural language processing using OpenAI's GPT models.
    
    Features:
    - Conversation context management
    - Tool calling support
    - Streaming responses
    - Conversation persistence
    - Intent detection through function calling
    """
    
    def __init__(
        self,
        event_bus,
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize the GPT service."""
        super().__init__("gpt_service", event_bus, logger)
        
        # Configuration
        self._config = self._load_config(config or {})
        
        # Session memory
        self._memory = SessionMemory(
            max_tokens=self._config["MAX_TOKENS"],
            max_messages=self._config["MAX_MESSAGES"]
        )
        
        # API state
        self._session: Optional[aiohttp.ClientSession] = None
        self._api_requests_in_progress = 0
        self._last_request_time = 0
        
        # Request tracking
        self._request_timestamps: List[float] = []
        self._rate_limit_window = 60  # 1 minute
        self._max_requests_per_window = 50  # Default OpenAI limit
        
        # Conversation state
        self._current_conversation_id: Optional[str] = None
        
        # Tool management
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._tool_schemas: List[Dict[str, Any]] = []
        
    def _load_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Load configuration from provided dict."""
        # OpenAI API key is required
        if "OPENAI_API_KEY" not in config:
            self.logger.warning("OPENAI_API_KEY not provided, service will fail to initialize")
            
        # Try to load DJ R3X persona from config path or common locations
        persona_paths = [
            config.get("PERSONA_FILE_PATH"),  # First try config-provided path
            "dj_r3x-persona.txt",             # Try current directory
            "cantina_os/dj_r3x-persona.txt",  # Try cantina_os directory
            "../dj_r3x-persona.txt",          # Try parent directory
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
            "OPENAI_API_KEY": config.get("OPENAI_API_KEY", ""),
            "MODEL": config.get("GPT_MODEL", "gpt-4.1-mini"),
            "MAX_TOKENS": config.get("MAX_TOKENS", 4000),
            "MAX_MESSAGES": config.get("MAX_MESSAGES", 20),
            "TEMPERATURE": config.get("TEMPERATURE", 0.7),
            "SYSTEM_PROMPT": system_prompt,
            "TIMEOUT": config.get("TIMEOUT", 30),  # seconds
            "RATE_LIMIT_REQUESTS": config.get("RATE_LIMIT_REQUESTS", 50),
            "STREAMING": config.get("STREAMING", True)
        }
        
    async def _initialize(self) -> None:
        """Initialize the GPT service."""
        try:
            # Initialize aiohttp session
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._config["TIMEOUT"])
            )
            
            # Initialize rate limiting
            self._max_requests_per_window = self._config["RATE_LIMIT_REQUESTS"]
            self._request_timestamps = []
            
            # Register command functions
            self._register_command_functions()
            self.logger.info("Registered command functions for intent detection")
            
            self.logger.info(
                f"Initialized GPT service with model={self._config['MODEL']}"
            )
            
        except Exception as e:
            error_msg = f"Failed to initialize GPT service: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise
            
    # Adding _start method to comply with architecture standards
    async def _start(self) -> None:
        """Start the GPT service following architecture standards.
        
        This method is called by BaseService.start() and ensures proper service lifecycle.
        """
        self.logger.info("GPTService _start method called - setting up service properly")
        
        try:
            # Initialize resources
            await self._initialize()
            
            # Set up event subscriptions
            await self._setup_subscriptions()
            
            self.logger.info("GPTService started successfully")
            
        except Exception as e:
            error_msg = f"Failed to start GPT service: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise
            
    async def _cleanup(self) -> None:
        """Clean up GPT service resources."""
        try:
            if self._session:
                await self._session.close()
                self._session = None
                
            self.logger.info("Cleaned up GPT service resources")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up GPT service resources: {str(e)}")
            
    async def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        # Get the topic values directly from the enum for comparison
        from ..event_topics import EventTopics
        transcription_final = EventTopics.TRANSCRIPTION_FINAL
        
        self.logger.info("GPTService attempting to set up subscriptions.") # Log entry for method call
        self.logger.info(f"SUBSCRIPTION DEBUG: TRANSCRIPTION_FINAL topic value: '{str(transcription_final)}', id: {id(transcription_final)}")
        self.logger.info(f"SUBSCRIPTION DEBUG: Module name: {transcription_final.__class__.__module__}")
        
        # Removed redundant subscription to raw_topic_value as the enum subscription is working.
        # asyncio.create_task(self.subscribe(
        #     raw_topic_value,  # Use raw string value instead of enum
        #     self._handle_transcription
        # ))
        
        # Subscribe using the EventTopics enum
        asyncio.create_task(self.subscribe(
            EventTopics.TRANSCRIPTION_FINAL, 
            self._handle_transcription
        ))
        self.logger.info(f"GPTService: Subscription tasks created for TRANSCRIPTION_FINAL.")
        
        # Add subscription for CLI text-based recording
        voice_listening_stopped_topic_value = str(EventTopics.VOICE_LISTENING_STOPPED)
        self.logger.info(f"GPTService subscribing to VOICE_LISTENING_STOPPED, actual string: '{voice_listening_stopped_topic_value}'")
        asyncio.create_task(self.subscribe(
            EventTopics.VOICE_LISTENING_STOPPED,
            self._handle_voice_transcript
        ))
        self.logger.info(f"GPTService: Subscription task created for VOICE_LISTENING_STOPPED.")
        
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
            
            # Always reset conversation state for a new voice interaction turn from mouse click.
            # This ensures each utterance is treated as a fresh start with the LLM.
            self.logger.info("Resetting conversation state for new voice input.")
            await self.reset_conversation() 

            # Process the transcript with the now-reset conversation state
            await self._process_with_gpt(transcript)
            
        except Exception as e:
            self.logger.error(f"Error processing voice transcript: {e}", exc_info=True)
            # Optionally, emit an error event or handle more gracefully
            
    async def _handle_transcription(self, payload: Dict[str, Any]) -> None:
        """Handle transcription text from the speech recognition service.
        
        Note: When using mouse clicks, this method will collect interim transcriptions
        but they won't be processed until the mouse click stop event triggers
        _handle_voice_transcript.
        """
        self.logger.debug(f"Received transcription: {str(payload)[:200]}...")
        
        try:
            # We're not processing individual transcriptions when using mouse clicks
            # The final accumulated transcript will be sent via VOICE_LISTENING_STOPPED event
            self.logger.debug("Individual transcription received but not processing - waiting for mouse click stop event")
            
        except Exception as e:
            error_msg = f"Error handling transcription: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            
    async def _process_with_gpt(self, user_input: str) -> None:
        """Process user input with GPT model."""
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

        # Prepare API request
        api_url = "https://api.openai.com/v1/chat/completions"
        request_data = {
            "model": self._config["MODEL"],
            "messages": self._memory.get_messages_for_api(),
            "temperature": self._config["TEMPERATURE"],
            "stream": self._config["STREAMING"],
        }

        # Log debug info about the messages being sent
        messages_for_api = self._memory.get_messages_for_api()
        self.logger.info(f"Sending {len(messages_for_api)} messages to API")
        for i, msg in enumerate(messages_for_api):
            self.logger.info(f"Message {i}: role={msg['role']}, content preview={msg['content'][:50]}...")

        # Add tool configurations if any are registered
        if self._tool_schemas:
            request_data["tools"] = self._tool_schemas
            # Allow the model to decide whether to call a function or generate text.
            # "auto" is the default when tools are present.
            request_data["tool_choice"] = "auto"
            self.logger.info(f"Including {len(self._tool_schemas)} tools in request, tool_choice set to 'auto'")

        # Log API request preparation
        self.logger.info(f"Preparing OpenAI API request with model: {self._config['MODEL']}")
        self.logger.debug(f"Request data: {json.dumps(request_data, indent=2)[:500]}...")

        try:
            self.logger.info("Making API call to OpenAI...")
            if self._config["STREAMING"]:
                await self._stream_gpt_response(api_url, request_data)
            else:
                await self._get_gpt_response(api_url, request_data)
            self.logger.info("API call completed successfully")
        except Exception as e:
            error_msg = f"Error processing with GPT: {str(e)}"
            self.logger.error(error_msg)
            # Include more details in the error message
            self.logger.error(f"Request details: URL={api_url}, Model={self._config['MODEL']}")
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            raise
            
    async def _get_gpt_response(self, api_url: str, request_data: Dict[str, Any]) -> None:
        """Get a non-streaming response from the GPT API."""
        if not self._session:
            raise RuntimeError("No active session for API request")

        headers = {
            "Authorization": f"Bearer {self._config['OPENAI_API_KEY']}",
            "Content-Type": "application/json"
        }

        self.logger.info(f"Making non-streaming API request to {api_url}")
        
        try:
            async with self._session.post(api_url, json=request_data, headers=headers) as response:
                self.logger.info(f"Received API response with status: {response.status}")
                
                if response.status != 200:
                    response_text = await response.text()
                    error_msg = f"API request failed with status {response.status}: {response_text[:200]}"
                    self.logger.error(error_msg)
                    await self._emit_status(
                        ServiceStatus.ERROR,
                        error_msg
                    )
                    raise Exception(error_msg)

                response_data = await response.json()
                self.logger.info(f"Successfully parsed API response")
                self.logger.debug(f"Response data: {json.dumps(response_data, indent=2)[:500]}...")
                
                message = response_data["choices"][0]["message"]
                
                # Add detailed debugging for tool call responses
                if message.get("tool_calls"):
                    self.logger.info("=== TOOL CALL RESPONSE DEBUG ===")
                    self.logger.info(f"Response message structure: {json.dumps(message, indent=2)}")
                    self.logger.info(f"Content field (raw): '{message.get('content')}'")
                    self.logger.info(f"Content field type: {type(message.get('content'))}")
                    self.logger.info(f"Content field length: {len(message.get('content') or '')}")
                    self.logger.info(f"Tool calls count: {len(message.get('tool_calls', []))}")
                    for i, tool_call in enumerate(message.get("tool_calls", [])):
                        self.logger.info(f"Tool call {i+1}: {tool_call['function']['name']}")
                    self.logger.info("=== END DEBUG ===")
                
                # Add assistant message to memory
                self._memory.add_message(
                    role="assistant",
                    content=message["content"] or "",
                    tool_calls=message.get("tool_calls")
                )

                # Process tool calls if any
                if message.get("tool_calls"):
                    await self._process_tool_calls(message["tool_calls"], message["content"] or "")

                # Emit response
                self.logger.info(f"Emitting LLM response: {message['content'][:50] if message['content'] else ''}...")
                await self._emit_llm_response(
                    message["content"] or "",
                    tool_calls=message.get("tool_calls")
                )
        except Exception as e:
            self.logger.error(f"Error in _get_gpt_response: {str(e)}")
            raise
            
    async def _stream_gpt_response(self, api_url: str, request_data: Dict[str, Any]) -> None:
        """Stream responses from the GPT API."""
        if not self._session:
            raise RuntimeError("No active session for API request")

        headers = {
            "Authorization": f"Bearer {self._config['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }

        self.logger.info(f"Making streaming API request to {api_url}")
        
        try:
            async with self._session.post(api_url, json=request_data, headers=headers) as response:
                self.logger.info(f"Established streaming connection with status: {response.status}")
                
                if response.status != 200:
                    response_text = await response.text()
                    error_msg = f"API request failed with status {response.status}: {response_text[:200]}"
                    self.logger.error(error_msg)
                    await self._emit_status(
                        ServiceStatus.ERROR,
                        error_msg
                    )
                    raise Exception(error_msg)

                full_content = ""
                tool_calls_collection = {}  # Dictionary to track tool calls by ID
                incomplete_tool_calls = set()  # Set to track incomplete tool calls
                complete_tool_calls = []  # List to store completed tool calls
                current_tool_call = None
                chunk_count = 0
                has_tool_calls = False
                is_complete = False

                self.logger.info("Starting to process streaming response chunks...")
                async for line in response.content:
                    if line:
                        try:
                            line = line.decode("utf-8").strip()
                            if line.startswith("data: ") and line != "data: [DONE]":
                                data = json.loads(line[6:])
                                delta = data["choices"][0]["delta"]
                                chunk_count += 1
                                
                                # Process text content
                                if "content" in delta:
                                    content = delta.get("content") or ""
                                    full_content += content
                                    if chunk_count % 10 == 0:  # Log periodically to avoid spam
                                        self.logger.debug(f"Processed {chunk_count} chunks, current content: {full_content[:50]}...")
                                    await self._emit_llm_stream_chunk(content, is_complete=False)
                                
                                # Handle tool calls in streaming mode
                                if "tool_calls" in delta:
                                    has_tool_calls = True
                                    self.logger.debug(f"Processing tool call chunk {chunk_count}")
                                    
                                    for tool_call_delta in delta["tool_calls"]:
                                        # Get tool call ID if present
                                        tool_call_id = tool_call_delta.get("id")
                                        
                                        if tool_call_id:
                                            # Start new tool call
                                            if tool_call_id not in tool_calls_collection:
                                                tool_calls_collection[tool_call_id] = {
                                                    "id": tool_call_id,
                                                    "type": "function",
                                                    "function": {"name": "", "arguments": ""}
                                                }
                                                incomplete_tool_calls.add(tool_call_id)
                                                self.logger.info(f"Started new tool call with id: {tool_call_id}")
                                            current_tool_call = tool_calls_collection[tool_call_id]
                                        
                                        # Process function data if present
                                        if "function" in tool_call_delta and current_tool_call:
                                            func_delta = tool_call_delta["function"]
                                            if "name" in func_delta:
                                                name_chunk = func_delta.get("name") or ""
                                                current_tool_call["function"]["name"] += name_chunk
                                                self.logger.debug(f"Appended to function name: {name_chunk}")
                                            if "arguments" in func_delta:
                                                args_chunk = func_delta.get("arguments") or ""
                                                current_tool_call["function"]["arguments"] += args_chunk
                                                args_preview = args_chunk[:30] + "..." if len(args_chunk) > 30 else args_chunk
                                                self.logger.debug(f"Appended to function arguments: {args_preview}")
                                            
                                            # Check if this tool call is complete
                                            if (current_tool_call["function"]["name"] and 
                                                current_tool_call["function"]["arguments"] and 
                                                tool_call_id in incomplete_tool_calls):
                                                try:
                                                    # Log the exact arguments string for debugging
                                                    args_str = current_tool_call["function"]["arguments"]
                                                    self.logger.info(f"Attempting to validate arguments JSON: '{args_str}'")
                                                    
                                                    # Try to parse the JSON arguments
                                                    json_args = json.loads(args_str)
                                                    
                                                    # If we get here, JSON is valid
                                                    self.logger.info(f"Valid JSON arguments parsed: {json_args}")
                                                    
                                                    # Tool call is complete
                                                    incomplete_tool_calls.remove(tool_call_id)
                                                    complete_tool_calls.append(current_tool_call)
                                                    self.logger.info(f"Completed tool call: {current_tool_call['function']['name']} with args: {json_args}")
                                                    
                                                    # Process this tool call immediately
                                                    await self._process_tool_calls([current_tool_call], full_content)
                                                except json.JSONDecodeError as e:
                                                    self.logger.debug(f"Tool call {tool_call_id} has incomplete arguments: {e}")
                                                    # If JSON is invalid but ends with a closing brace, it might be complete
                                                    # but with formatting issues - try cleanup and retry
                                                    if args_str.endswith('}'):
                                                        self.logger.info("Arguments end with '}', attempting JSON cleanup and retry")
                                                        try:
                                                            # Try to clean up common JSON errors
                                                            # 1. Missing quotes around property names
                                                            # 2. Single quotes instead of double quotes
                                                            # This is a simple approach - more sophisticated JSON repair could be added
                                                            import re
                                                            # Replace single quotes with double quotes
                                                            cleaned = args_str.replace("'", "\"")
                                                            json_args = json.loads(cleaned)
                                                            
                                                            self.logger.info(f"JSON cleanup successful! Parsed: {json_args}")
                                                            
                                                            # Update the arguments with the cleaned version
                                                            current_tool_call["function"]["arguments"] = cleaned
                                                            
                                                            # Mark as complete and process
                                                            incomplete_tool_calls.remove(tool_call_id)
                                                            complete_tool_calls.append(current_tool_call)
                                                            self.logger.info(f"Completed tool call after cleanup: {current_tool_call['function']['name']}")
                                                            
                                                            # Process this tool call immediately
                                                            await self._process_tool_calls([current_tool_call], full_content)
                                                        except Exception as cleanup_error:
                                                            self.logger.debug(f"JSON cleanup failed: {cleanup_error}")
                                            
                            elif line == "data: [DONE]":
                                is_complete = True
                                await self._emit_llm_stream_chunk("", tool_calls=None, is_complete=True)
                                self.logger.info("Received [DONE] in stream")
                                
                        except Exception as e:
                            self.logger.error(f"Error processing stream chunk: {str(e)}")
                
                # Final attempt to process any remaining tool calls that look complete
                for tool_id in list(incomplete_tool_calls):
                    tool_call = tool_calls_collection[tool_id]
                    if tool_call["function"]["name"] and tool_call["function"]["arguments"]:
                        self.logger.info(f"Final attempt to process tool call: {tool_call['function']['name']}")
                        try:
                            # Try direct processing without JSON validation
                            incomplete_tool_calls.remove(tool_id)
                            complete_tool_calls.append(tool_call)
                            self.logger.info(f"Processing tool call directly: {tool_call['function']['name']}")
                            await self._process_tool_calls([tool_call], full_content)
                        except Exception as e:
                            self.logger.error(f"Failed to process tool call in final attempt: {e}")
                
                # Log final statistics
                self.logger.info(f"Completed streaming response with {chunk_count} chunks")
                self.logger.info(f"Processed {len(complete_tool_calls)} complete tool calls")
                if incomplete_tool_calls:
                    self.logger.warning(f"Found {len(incomplete_tool_calls)} incomplete tool calls")
                    for tool_id in incomplete_tool_calls:
                        tool_call = tool_calls_collection[tool_id]
                        self.logger.warning(f"Incomplete tool call: {tool_call}")

                # Add complete message to memory
                self._memory.add_message(
                    role="assistant",
                    content=full_content,
                    tool_calls=complete_tool_calls if complete_tool_calls else None
                )

                # Emit the final LLM response with both text and tool calls
                # This ensures ElevenLabs gets the text content for speech synthesis
                # OpenAI will provide both tool calls AND text content in the same response
                self.logger.info(f"Emitting final LLM response with text: '{full_content[:50]}...' and {len(complete_tool_calls)} tool calls")
                
                # Always emit text content, even if empty, to ensure proper event flow
                await self._emit_llm_response(full_content, tool_calls=complete_tool_calls)

                # Emit final chunk
                self.logger.info("Emitting final LLM response chunk")
                
        except Exception as e:
            self.logger.error(f"Error in _stream_gpt_response: {str(e)}")
            raise

    async def _emit_llm_response(
        self, 
        response_text: str, 
        tool_calls: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Emit a complete LLM response event.
        
        Args:
            response_text: The complete response text
            tool_calls: Optional tool calls from the LLM
        """
        # Create response payload
        payload = LLMResponsePayload(
            text=response_text,
            tool_calls=tool_calls,
            is_complete=True,
            conversation_id=self._current_conversation_id
        )
        
        # Note: We no longer add the message to memory here as it's already done
        # in the _get_gpt_response or _stream_gpt_response methods
        # This prevents duplicate message storage
        
        # Emit the event
        self.logger.info(f"Emitting LLM_RESPONSE event with {len(response_text)} chars")
        await self.emit(EventTopics.LLM_RESPONSE, payload)
        
    async def _emit_llm_stream_chunk(
        self, 
        chunk_text: str, 
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        is_complete: bool = False
    ) -> None:
        """
        Emit an LLM response stream chunk event.
        
        Args:
            chunk_text: The chunk of response text
            tool_calls: Optional tool calls from the LLM
            is_complete: Whether this is the final chunk
        """
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
        
    def register_tool(self, tool_schema: Dict[str, Any]) -> None:
        """Register a tool for use with the GPT model."""
        tool_name = tool_schema["function"]["name"]  # Updated to get name from function property
        self._tools[tool_name] = tool_schema
        self._tool_schemas = list(self._tools.values())
        self.logger.info(f"Registered tool: {tool_name}")

    async def reset_conversation(self) -> None:
        """Reset the conversation state with a new ID."""
        self._current_conversation_id = str(uuid.uuid4())
        self._memory.clear()
        
        # Initialize with system prompt
        if self._config["SYSTEM_PROMPT"]:
            self._memory.add_message("system", self._config["SYSTEM_PROMPT"])
        
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
                        # Parse the arguments JSON
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
        
        # Make sure we emit the LLM response with both text and tool calls
        # This ensures ElevenLabs gets the text content for speech synthesis
        if response_text:
            self.logger.info(f"Emitting LLM response with text ({len(response_text)} chars) and {len(tool_calls)} tool calls")
            await self._emit_llm_response(response_text, tool_calls) 