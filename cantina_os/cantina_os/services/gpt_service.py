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
from pydantic import BaseModel

from ..base_service import BaseService
from ..event_topics import EventTopics
from ..event_payloads import (
    BaseEventPayload,
    TranscriptionTextPayload,
    LLMResponsePayload,
    ServiceStatus,
    LogLevel
)

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
            
        return {
            "OPENAI_API_KEY": config.get("OPENAI_API_KEY", ""),
            "MODEL": config.get("GPT_MODEL", "gpt-4o"),
            "MAX_TOKENS": config.get("MAX_TOKENS", 4000),
            "MAX_MESSAGES": config.get("MAX_MESSAGES", 20),
            "TEMPERATURE": config.get("TEMPERATURE", 0.7),
            "SYSTEM_PROMPT": config.get("SYSTEM_PROMPT", "You are DJ R3X, a helpful and enthusiastic Star Wars droid DJ assistant."),
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
            
    async def _cleanup(self) -> None:
        """Clean up GPT service resources."""
        try:
            if self._session:
                await self._session.close()
                self._session = None
                
            self.logger.info("Cleaned up GPT service resources")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up GPT service resources: {str(e)}")
            
    def _setup_subscriptions(self) -> None:
        """Set up event subscriptions."""
        self.subscribe(
            EventTopics.AUDIO_TRANSCRIPTION_FINAL,
            self._handle_transcription
        )
        
        # Add subscription for CLI text-based recording
        self.subscribe(
            EventTopics.VOICE_LISTENING_STOPPED,
            self._handle_voice_transcript
        )
        
    async def _handle_voice_transcript(self, payload: Dict[str, Any]) -> None:
        """Handle text transcript from the CLI recording mode."""
        try:
            # Extract text from payload
            if not payload or "transcript" not in payload:
                self.logger.warning("Received empty transcript in VOICE_LISTENING_STOPPED event")
                return
                
            transcript = payload["transcript"]
            self.logger.info(f"Received text transcript: {transcript}")
            
            # Process with GPT
            await self._process_with_gpt(transcript)
            
        except Exception as e:
            error_msg = f"Error handling voice transcript: {str(e)}"
            self.logger.error(error_msg)
            await self._emit_status(
                ServiceStatus.ERROR,
                error_msg
            )
            
    async def _handle_transcription(self, payload: Dict[str, Any]) -> None:
        """Handle transcription text from the speech recognition service."""
        try:
            # Extract text from payload
            transcription = TranscriptionTextPayload(**payload)
            
            # Update conversation ID if needed
            if transcription.conversation_id:
                self._current_conversation_id = transcription.conversation_id
            elif not self._current_conversation_id:
                await self.reset_conversation()
            
            # Add user message to memory
            self._memory.add_message("user", transcription.text)
            
            # Process with GPT
            await self._process_with_gpt(transcription.text)
            
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

        # Add user message to memory
        self._memory.add_message("user", user_input)

        # Check rate limiting
        current_time = time.time()
        self._request_timestamps = [t for t in self._request_timestamps 
                                  if current_time - t <= self._rate_limit_window]
        
        if len(self._request_timestamps) >= self._max_requests_per_window:
            raise Exception("Rate limit exceeded")

        self._request_timestamps.append(current_time)

        # Prepare API request
        api_url = "https://api.openai.com/v1/chat/completions"
        request_data = {
            "model": self._config["MODEL"],
            "messages": self._memory.get_messages_for_api(),
            "temperature": self._config["TEMPERATURE"],
            "stream": self._config["STREAMING"],
        }

        # Add tool configurations if any are registered
        if self._tool_schemas:
            request_data["tools"] = self._tool_schemas
            request_data["tool_choice"] = "auto"

        try:
            if self._config["STREAMING"]:
                await self._stream_gpt_response(api_url, request_data)
            else:
                await self._get_gpt_response(api_url, request_data)
        except Exception as e:
            error_msg = f"Error processing with GPT: {str(e)}"
            self.logger.error(error_msg)
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

        async with self._session.post(api_url, json=request_data, headers=headers) as response:
            if response.status != 200:
                error_msg = f"API request failed with status {response.status}"
                self.logger.error(error_msg)
                await self._emit_status(
                    ServiceStatus.ERROR,
                    error_msg
                )
                raise Exception(error_msg)

            response_data = await response.json()
            message = response_data["choices"][0]["message"]
            
            # Add assistant message to memory
            self._memory.add_message(
                role="assistant",
                content=message["content"],
                tool_calls=message.get("tool_calls")
            )

            # Emit response
            await self._emit_llm_response(
                message["content"],
                tool_calls=message.get("tool_calls")
            )
            
    async def _stream_gpt_response(self, api_url: str, request_data: Dict[str, Any]) -> None:
        """Stream responses from the GPT API."""
        if not self._session:
            raise RuntimeError("No active session for API request")

        headers = {
            "Authorization": f"Bearer {self._config['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }

        async with self._session.post(api_url, json=request_data, headers=headers) as response:
            if response.status != 200:
                error_msg = f"API request failed with status {response.status}"
                self.logger.error(error_msg)
                await self._emit_status(
                    ServiceStatus.ERROR,
                    error_msg
                )
                raise Exception(error_msg)

            full_content = ""
            tool_calls = []
            is_complete = False

            async for line in response.content:
                if line:
                    try:
                        line = line.decode("utf-8").strip()
                        if line.startswith("data: ") and line != "data: [DONE]":
                            data = json.loads(line[6:])
                            delta = data["choices"][0]["delta"]
                            
                            if "content" in delta:
                                content = delta["content"]
                                full_content += content
                                await self._emit_llm_stream_chunk(content, is_complete=False)
                            
                            if "tool_calls" in delta:
                                tool_calls.extend(delta["tool_calls"])
                                
                    except Exception as e:
                        self.logger.error(f"Error processing stream chunk: {str(e)}")

            # Add complete message to memory
            self._memory.add_message(
                role="assistant",
                content=full_content,
                tool_calls=tool_calls if tool_calls else None
            )

            # Emit final chunk
            await self._emit_llm_stream_chunk(
                full_content,
                tool_calls=tool_calls if tool_calls else None,
                is_complete=True
            )

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
        
        # Add the message to memory for context
        self._memory.add_message("assistant", response_text)
        
        # Emit the event
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
        tool_name = tool_schema["name"]
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