# DJ R3X Voice - OpenAI Tool Calling Architecture Analysis

## Executive Summary

The DJ R3X system implements OpenAI's function calling (tool use) as a critical part of its intent detection and command execution pipeline. The architecture uses a **two-step process**: first detecting intents via tool calls, then executing those intents and generating verbal feedback. This document provides a detailed analysis of how tool calling is currently implemented.

---

## 1. Tool Definition & Registration

### 1.1 Tool Schema Definition (`cantina_os/llm/command_functions.py`)

Tools are defined using Pydantic models for parameter validation and OpenAI JSON schema generation:

```python
# Three available tools:

1. play_music
   - Parameters: track (string) - song or music track to play
   - Description: "Play a specific song or music genre"

2. stop_music
   - Parameters: (none)
   - Description: "Stop the currently playing music"

3. set_eye_color
   - Parameters: 
     * color (string) - color for the eyes
     * pattern (optional string) - LED pattern (defaults to 'solid')
     * intensity (optional float, 0.0-1.0) - brightness level
   - Description: "Change the color of DJ R3X's LED eyes"
```

### 1.2 Pydantic Parameter Models

Each tool has a corresponding Pydantic model for validation:

```python
class PlayMusicParams(BaseModel):
    track: str = Field(..., description="...")

class StopMusicParams(BaseModel):
    pass  # No parameters needed

class SetEyeColorParams(BaseModel):
    color: str = Field(...)
    pattern: Optional[str] = Field(None, description="...")
    intensity: Optional[float] = Field(None, ge=0.0, le=1.0)
```

### 1.3 Function Definition Creation

Tools are created using helper functions that return OpenAI-compatible format:

```python
def create_play_music_function() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": "Play a specific song or music genre",
            "parameters": PlayMusicParams.schema()  # Converts Pydantic to JSON schema
        }
    }
```

### 1.4 Registration with GPT Service

During GPTService initialization, tools are registered:

```python
def _register_command_functions(self) -> None:
    """Register all command functions for intent detection."""
    function_definitions = get_all_function_definitions()
    for function_def in function_definitions:
        self.register_tool(function_def)
    # Stores in self._tools dict and self._tool_schemas list
```

---

## 2. Tool Call Request Handling

### 2.1 API Request Configuration

When making GPT API calls, tools are included if any are registered:

```python
# In _process_with_gpt():
request_data = {
    "model": "gpt-4.1-mini",
    "messages": self._memory.get_messages_for_api(),
    "temperature": 0.7,
    "stream": True,
    "tools": self._tool_schemas,  # Include all registered tools
    "tool_choice": "auto"          # Allow model to choose when to use tools
}
```

### 2.2 Conversation History with Tool Messages

SessionMemory tracks conversation including tool interactions:

```python
class SessionMemory:
    def __init__(self, max_tokens: int = 4000, max_messages: int = 20):
        self.messages: Deque[Message] = deque(maxlen=max_messages)
        # Tracks: system, user, assistant, tool messages
        
    def add_message(self, role: str, content: str, **kwargs) -> None:
        # Supports: role, content, name, tool_calls, tool_call_id
        message = Message(role=role, content=content, **kwargs)
        self.messages.append(message)
        
    def get_messages_for_api(self) -> List[Dict[str, Any]]:
        # Returns OpenAI-compatible message format including tool calls/responses
```

---

## 3. Streaming Tool Call Processing

### 3.1 Streaming Response Handling (`_stream_gpt_response`)

The streaming handler reconstructs tool calls from chunks:

```python
# In _stream_gpt_response():
tool_calls_collection = {}       # Track by tool call ID
incomplete_tool_calls = set()    # Track incomplete calls
complete_tool_calls = []         # Track completed calls

# For each streamed chunk with tool_calls data:
if "tool_calls" in delta:
    for tool_call_delta in delta["tool_calls"]:
        tool_call_id = tool_call_delta.get("id")
        
        if tool_call_id not in tool_calls_collection:
            tool_calls_collection[tool_call_id] = {
                "id": tool_call_id,
                "type": "function",
                "function": {"name": "", "arguments": ""}
            }
            incomplete_tool_calls.add(tool_call_id)
        
        # Accumulate function name and arguments
        if "function" in tool_call_delta:
            func_delta = tool_call_delta["function"]
            if "name" in func_delta:
                current_tool_call["function"]["name"] += func_delta.get("name") or ""
            if "arguments" in func_delta:
                current_tool_call["function"]["arguments"] += func_delta.get("arguments") or ""
```

### 3.2 Tool Call Validation During Streaming

As arguments stream in, JSON parsing is attempted to detect completion:

```python
# Check if tool call is complete (name + valid JSON arguments):
try:
    json_args = json.loads(current_tool_call["function"]["arguments"])
    incomplete_tool_calls.remove(tool_call_id)
    complete_tool_calls.append(current_tool_call)
    # Immediately process the completed tool call
    await self._process_tool_calls([current_tool_call], full_content)
except json.JSONDecodeError:
    # JSON still incomplete - continue streaming
    pass
```

### 3.3 JSON Error Recovery

If arguments end with `}` but have formatting issues, attempt cleanup:

```python
if args_str.endswith('}'):
    try:
        # Replace single quotes with double quotes
        cleaned = args_str.replace("'", "\"")
        json_args = json.loads(cleaned)
        # Update and process the cleaned version
        current_tool_call["function"]["arguments"] = cleaned
        await self._process_tool_calls([current_tool_call], full_content)
    except Exception:
        pass  # Continue without cleanup
```

---

## 4. Tool Call Processing Flow

### 4.1 `_process_tool_calls` Method

Core method that transforms OpenAI tool calls into system intents:

```python
async def _process_tool_calls(self, tool_calls: List[Dict[str, Any]], response_text: str) -> None:
    """Process and emit intents from tool calls."""
    
    for tool_call in tool_calls:
        function_name = tool_call["function"]["name"]
        function_args_str = tool_call["function"]["arguments"]
        
        # Parse JSON arguments
        try:
            function_args = json.loads(function_args_str)
        except json.JSONDecodeError:
            self.logger.error(f"Invalid JSON in function arguments")
            continue
        
        # Validate against Pydantic model
        model_map = function_name_to_model_map()
        if function_name in model_map:
            param_model = model_map[function_name]
            validated_params = param_model(**function_args)  # Raises ValidationError if invalid
            function_args = validated_params.model_dump()
        
        # Create and emit INTENT_DETECTED event
        intent_payload = IntentPayload(
            intent_name=function_name,
            parameters=function_args,
            original_text=response_text,
            conversation_id=self._current_conversation_id
        )
        await self.emit(EventTopics.INTENT_DETECTED, intent_payload)
```

### 4.2 Event Payloads for Tool Calling

**IntentPayload** (emitted when tool is detected):
```python
class IntentPayload(BaseEventPayload):
    intent_name: str              # Function name (e.g., "play_music")
    parameters: Dict[str, Any]    # Validated arguments
    confidence: Optional[float]   # Confidence score
    original_text: str            # The LLM's response text
    conversation_id: Optional[str]
```

**IntentExecutionResultPayload** (emitted after tool executes):
```python
class IntentExecutionResultPayload(BaseEventPayload):
    intent_name: str              # Function name
    parameters: Dict[str, Any]    # Parameters used
    result: Dict[str, Any]        # Execution result data
    success: bool                 # Whether execution succeeded
    error_message: Optional[str]  # Error if failed
    tool_call_id: Optional[str]   # Original OpenAI tool call ID
    original_text: Optional[str]  # Original response text
```

---

## 5. Intent Routing & Execution

### 5.1 IntentRouterService

Bridges the gap between detected intents and hardware commands:

```python
class IntentRouterService(BaseService):
    def __init__(self, event_bus, config=None, logger=None):
        self._intent_handlers = {
            "play_music": self._handle_play_music_intent,
            "stop_music": self._handle_stop_music_intent,
            "set_eye_color": self._handle_set_eye_color_intent
        }
    
    async def _handle_intent(self, payload: Dict[str, Any]) -> None:
        """Handle INTENT_DETECTED events."""
        intent_name = payload.get("intent_name")
        parameters = payload.get("parameters")
        
        if intent_name in self._intent_handlers:
            handler = self._intent_handlers[intent_name]
            result = await handler(parameters, conversation_id)
            
            # Emit execution result for verbal feedback
            await self._emit_intent_execution_result(
                intent_name, parameters, result, tool_call_id, conversation_id
            )
```

### 5.2 Intent Handler Example: `_handle_play_music_intent`

```python
async def _handle_play_music_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
    track = parameters.get("track", "")
    
    # Smart track selection (maps generic names to actual tracks)
    selected_track = await self._select_smart_track(track)
    
    # Emit CLI_COMMAND for unified processing
    cli_payload = {
        "command": "play",
        "subcommand": "music",
        "args": [selected_track],
        "raw_input": f"play music {selected_track}",
        "conversation_id": conversation_id
    }
    await self.emit(EventTopics.CLI_COMMAND, cli_payload)
    
    return {
        "success": True,
        "track": selected_track,
        "original_request": track,
        "message": f"Now playing: {selected_track}"
    }
```

---

## 6. Two-Step Verbal Feedback Process

### 6.1 The Two-Step Architecture

The system uses a deliberate two-step process for reliability:

```
Step 1: Tool Calling & Intent Detection
  User speaks → GPTService receives tool call → emits LLM_RESPONSE + INTENT_DETECTED
  
Step 2: Intent Execution & Verbal Response
  IntentRouterService executes intent → emits INTENT_EXECUTION_RESULT
  GPTService receives result → emits LLM_RESPONSE (verbal feedback)
  
Benefits:
- Ensures tool execution doesn't block LLM response to user
- Allows LLM to explain what action is being taken
- Provides error feedback if tool execution fails
```

### 6.2 `_get_verbal_response_for_intent` Method

After a tool executes, GPT generates natural language feedback:

```python
async def _get_verbal_response_for_intent(
    self,
    intent_name: str,
    parameters: Dict[str, Any],
    result: Dict[str, Any],
    success: bool
) -> None:
    """Generate verbal response about an executed intent."""
    
    # Create a focused GPT call for feedback
    api_url = "https://api.openai.com/v1/chat/completions"
    
    intent_details = f"""
Intent executed: {intent_name}
Parameters: {json.dumps(parameters)}
Result: {json.dumps(result)}
Success: {success}
"""
    
    # Load verbal feedback persona for DJ R3X
    messages = [
        {"role": "system", "content": verbal_feedback_persona},
        {"role": "user", "content": intent_details}
    ]
    
    request_data = {
        "model": "gpt-4.1-mini",
        "messages": messages,
        "temperature": 0.7,
        "stream": False,
        "tool_choice": "none"  # Force text-only response
    }
    
    # Get response and emit
    async with self._session.post(api_url, json=request_data, headers=headers) as response:
        response_data = await response.json()
        verbal_response = response_data["choices"][0]["message"]["content"]
        await self._emit_llm_response(verbal_response)
```

### 6.3 Tool Response Integration into Memory

When tool execution completes, the result is added to conversation memory:

```python
async def _process_intent_execution_result(self, payload: Dict[str, Any]) -> None:
    """Process tool execution results."""
    result_payload = IntentExecutionResultPayload(**payload)
    
    # Add tool response to conversation memory
    response_content = json.dumps(result_payload.result) if result_payload.result else "Action completed successfully."
    
    if result_payload.tool_call_id:
        self._memory.add_message(
            role="tool",
            content=response_content,
            tool_call_id=result_payload.tool_call_id,
            name=result_payload.intent_name
        )
    
    # Generate verbal feedback
    await self._get_verbal_response_for_intent(
        result_payload.intent_name,
        result_payload.parameters,
        result_payload.result,
        result_payload.success
    )
```

---

## 7. Event Flow Diagram

```
User speaks
    ↓
DeepgramDirectMicService (TRANSCRIPTION_FINAL)
    ↓
GPTService._handle_transcription()
    ↓
_process_with_gpt(user_input)
    ↓
OpenAI API (with tools)
    ↓
LLM returns:
  - response text
  - tool_calls (if any)
    ↓
_stream_gpt_response()  [if streaming enabled]
    ├─ Accumulate chunks
    ├─ Parse JSON arguments for each tool call
    └─ Emit LLM_RESPONSE event
    ↓
_process_tool_calls()
    ├─ Parse function_name and function_args
    ├─ Validate against Pydantic model
    └─ Emit INTENT_DETECTED event
    ↓
ElevenLabsService listens to LLM_RESPONSE
    ↓
Synthesize and play LLM's response text
    ↓
IntentRouterService listens to INTENT_DETECTED
    ├─ Route to appropriate handler
    ├─ Execute the tool
    └─ Emit INTENT_EXECUTION_RESULT
    ↓
GPTService listens to INTENT_EXECUTION_RESULT
    ├─ Add tool result to memory
    ├─ Generate verbal feedback via second API call
    └─ Emit LLM_RESPONSE (feedback)
    ↓
ElevenLabsService
    ↓
Synthesize and play feedback
```

---

## 8. Message Format in SessionMemory

### 8.1 User Message
```python
{"role": "user", "content": "play some music"}
```

### 8.2 Assistant Message with Tool Calls
```python
{
    "role": "assistant",
    "content": "I'll play some Cantina Band music for you!",
    "tool_calls": [
        {
            "id": "call_abc123",
            "type": "function",
            "function": {
                "name": "play_music",
                "arguments": "{\"track\": \"cantina_band\"}"
            }
        }
    ]
}
```

### 8.3 Tool Response
```python
{
    "role": "tool",
    "content": "{\"success\": true, \"track\": \"cantina_band\", \"message\": \"Now playing: Cantina Band\"}",
    "tool_call_id": "call_abc123",
    "name": "play_music"
}
```

---

## 9. Configuration & Constraints

### 9.1 GPTService Configuration
```python
"MODEL": "gpt-4.1-mini"  # Optimized for tool calling (50% faster, 83% cheaper)
"MAX_TOKENS": 4000       # Max tokens in conversation history
"MAX_MESSAGES": 20       # Max messages before pruning oldest
"TEMPERATURE": 0.7       # Creativity for both intent detection and verbal feedback
"STREAMING": True        # Enable streaming responses
"tool_choice": "auto"    # Allow model to choose when to use tools
```

### 9.2 Tool Constraints
- Maximum 3 tools currently registered (play_music, stop_music, set_eye_color)
- Parameters validated using Pydantic before routing
- Tool call IDs tracked for linking execution results back to original calls
- Only valid JSON arguments are processed

---

## 10. Known Limitations & Trade-offs

### 10.1 Current Limitations

1. **Limited Tool Inventory**
   - Only 3 tools currently implemented
   - Adding new tools requires modifying `command_functions.py`
   - No dynamic tool registration at runtime

2. **No Function Execution in GPTService**
   - Tool calls are not executed by GPTService itself
   - Two separate services handle detection (GPTService) vs execution (IntentRouterService)
   - Adds latency due to two event hops

3. **Smart Track Selection is Hardcoded**
   - `_select_smart_track()` uses hardcoded keyword mapping
   - No access to actual music library at intent detection time
   - Generic requests always default to "cantina_band"

4. **Manual JSON Repair**
   - Only handles single quote → double quote conversion
   - More complex formatting errors will still fail
   - No schema validation of streamed arguments

5. **Separate Verbal Feedback Call**
   - Two separate API calls per intent execution (one for tool calling, one for feedback)
   - Increases latency and API costs
   - No support for multi-turn feedback based on tool results

6. **Tool Call Storage**
   - Tool call IDs not fully preserved across all steps
   - IntentRouterService attempts to extract from payload but may fail if not present
   - No global tool call registry for tracking

### 10.2 Design Trade-offs

**Trade-off 1: Two-Step vs One-Step**
- Current: Two API calls + streaming reconstruction
- Reason: Ensures reliability, separates concerns, allows interim responses
- Cost: Higher latency, more API calls

**Trade-off 2: SessionMemory vs Centralized Memory**
- Current: Tool call history only in SessionMemory (ephemeral)
- Reason: Simple, no distributed state needed
- Cost: No persistence across bot restarts, no shared state with other services

**Trade-off 3: String-based Tool Routing**
- Current: Intent names as strings matched against handler dict
- Reason: Flexible, easy to add new handlers
- Cost: No type safety, runtime string lookup required

---

## 11. Areas for Enhancement

### 11.1 Short-term Improvements

1. **Dynamic Tool Registration**
   ```python
   # Register tools at runtime based on available services
   for service in enabled_services:
       for tool in service.get_available_tools():
           gpt_service.register_tool(tool)
   ```

2. **Better JSON Error Recovery**
   ```python
   # Use json_repair library or similar for resilient parsing
   from json_repair import repair_json
   cleaned = repair_json(args_str)
   ```

3. **Unified Tool Execution**
   - Move execution logic from IntentRouterService into GPTService
   - Maintain single responsibility but reduce event hops

### 11.2 Medium-term Improvements

1. **Typed Tool Registry**
   - Use TypedDict for tool schemas
   - Enable IDE autocompletion for tool definitions

2. **Tool Execution Timeouts**
   - Add configurable per-tool execution timeouts
   - Handle long-running operations gracefully

3. **Tool Result Caching**
   - Cache frequently-executed tool results
   - Reduce API calls for repeated intents

### 11.3 Long-term Architecture

1. **Distributed Tool Calling**
   - Service discovery for available tools
   - Remote tool execution via gRPC/HTTP

2. **Tool Analytics**
   - Track which tools are called most frequently
   - Monitor tool execution latency and error rates

3. **Conditional Tool Availability**
   - Enable/disable tools based on system state or capabilities
   - Different tools for different DJ modes

---

## 12. Code Patterns for Tool Calling

### 12.1 Minimal Tool Definition Pattern

```python
class MyToolParams(BaseModel):
    param1: str = Field(..., description="First parameter")
    param2: Optional[int] = Field(None, description="Optional parameter")

def create_my_tool_function() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "What this tool does",
            "parameters": MyToolParams.schema()
        }
    }
```

### 12.2 Handler Implementation Pattern

```python
async def _handle_my_tool_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
    try:
        param1 = parameters.get("param1", "")
        param2 = parameters.get("param2", None)
        
        # Execute the action
        result = await self._do_action(param1, param2)
        
        return {
            "success": True,
            "result_data": result,
            "message": f"Action completed: {result}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
```

### 12.3 Registration Pattern

```python
self._intent_handlers = {
    "my_tool": self._handle_my_tool_intent,
    # Add more handlers here
}
```

---

## 13. Summary Table

| Aspect | Implementation | Event Type | Notes |
|--------|----------------|-----------|-------|
| **Tool Definition** | Pydantic models | None | Used for JSON schema generation |
| **Tool Registration** | `register_tool()` method | None | Called during GPTService init |
| **API Request** | `tools` + `tool_choice` | None | OpenAI chat completion endpoint |
| **Streaming Handling** | Chunk accumulation | LLM_RESPONSE | Reconstructs tool calls from deltas |
| **Intent Detection** | JSON parsing + validation | INTENT_DETECTED | Emitted by GPTService |
| **Intent Routing** | Dictionary-based dispatch | None | Handler lookup in IntentRouterService |
| **Intent Execution** | Service-specific handlers | INTENT_EXECUTION_RESULT | Emitted by IntentRouterService |
| **Verbal Feedback** | Second API call | LLM_RESPONSE | Emitted by GPTService |
| **Memory Integration** | Tool messages added | None | SessionMemory tracks conversation |

---

## 14. Conclusion

The DJ R3X tool calling architecture demonstrates a clean separation between:

1. **Intent Detection** (GPTService) - converts speech→intents via OpenAI tools
2. **Intent Routing** (IntentRouterService) - maps intents to hardware commands
3. **Verbal Feedback** (GPTService) - explains actions taken

This architecture prioritizes **reliability and decoupling** over minimal latency, making it suitable for an interactive DJ system where users can tolerate a 2-3 second response time for the benefit of robust intent understanding and error handling.

The main areas for improvement center on **generalization** (dynamic tool registration, better JSON handling) and **optimization** (reduced API calls, better streaming reconstruction).
