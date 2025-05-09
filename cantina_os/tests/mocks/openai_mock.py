"""Mock implementation of the OpenAI GPT service."""
import asyncio
import json
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Union
from .base_mock import BaseMockService
from cantina_os.event_bus import EventBus

class OpenAIMock(BaseMockService):
    """Mock implementation of OpenAI's GPT service.
    
    This mock simulates the behavior of GPT's streaming API,
    including chat completions, function calls, and error conditions.
    """
    
    def __init__(self, event_bus: EventBus) -> None:
        """Initialize the OpenAI mock service."""
        super().__init__()
        self.event_bus = event_bus
        self._conversation_history: List[Dict[str, Any]] = []
        self._on_chunk: Optional[Callable[[Dict[str, Any]], None]] = None
        self._on_function_call: Optional[Callable[[Dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._streaming_task: Optional[asyncio.Task] = None
        self.is_streaming: bool = False
        self.simulate_error_flag: bool = False
        
    async def initialize(self) -> None:
        """Initialize the mock service."""
        await super().initialize()
        self.record_call('initialize')
        
    async def shutdown(self) -> None:
        """Shutdown the mock service and cleanup resources."""
        if self.is_streaming:
            await self.stop_streaming()
        await super().shutdown()
        self.record_call('shutdown')
        
    def on_chunk(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register chunk callback for streaming responses."""
        self.record_call('on_chunk', callback)
        self._on_chunk = callback
        
    def on_function_call(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Register function call callback."""
        self.record_call('on_function_call', callback)
        self._on_function_call = callback
        
    def on_error(self, callback: Callable[[str], None]) -> None:
        """Register error callback."""
        self.record_call('on_error', callback)
        self._on_error = callback
        
    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        functions: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        **kwargs
    ) -> Union[Dict[str, Any], AsyncGenerator[Dict[str, Any], None]]:
        """Simulate chat completion with optional streaming."""
        self.record_call('chat_completion', messages, functions, stream, **kwargs)
        self._conversation_history.extend(messages)
        
        if stream:
            return self._stream_response()
        else:
            return self.get_response('completion') or {
                'choices': [{
                    'message': {
                        'content': 'Mock response',
                        'role': 'assistant'
                    }
                }]
            }
            
    async def _stream_response(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream the mock response."""
        self.is_streaming = True
        response = self.get_response('stream') or [
            {'choices': [{'delta': {'content': 'Mock '}}]},
            {'choices': [{'delta': {'content': 'streaming '}}]},
            {'choices': [{'delta': {'content': 'response'}}]},
            {'choices': [{'delta': {'content': None}, 'finish_reason': 'stop'}]}
        ]
        
        for chunk in response:
            if self._on_chunk:
                self._on_chunk(chunk)
            yield chunk
            await asyncio.sleep(0.1)  # Simulate processing time
            
        self.is_streaming = False
        
    async def stop_streaming(self) -> None:
        """Stop any active streaming response."""
        self.record_call('stop_streaming')
        self.is_streaming = False
        if self._streaming_task:
            self._streaming_task.cancel()
            try:
                await self._streaming_task
            except asyncio.CancelledError:
                pass
            self._streaming_task = None
            
    def simulate_function_call(self, function_call: Dict[str, Any]) -> None:
        """Simulate a function call response."""
        self.record_call('simulate_function_call', function_call)
        if self._on_function_call:
            self._on_function_call(function_call)
            
    def simulate_error(self, error_msg: str) -> None:
        """Simulate an error condition."""
        self.record_call('simulate_error', error_msg)
        if self._on_error:
            self._on_error(error_msg)
            
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """Get the recorded conversation history."""
        return self._conversation_history.copy()
        
    def clear_conversation(self) -> None:
        """Clear the conversation history."""
        self._conversation_history.clear() 