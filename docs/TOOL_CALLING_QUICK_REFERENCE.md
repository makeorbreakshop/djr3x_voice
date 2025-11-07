# DJ R3X Tool Calling - Quick Reference Guide

## Key Files

| File | Purpose |
|------|---------|
| `cantina_os/services/gpt_service.py` | Main tool calling engine, intent detection, verbal feedback |
| `cantina_os/llm/command_functions.py` | Tool definitions, Pydantic models, function schemas |
| `cantina_os/services/intent_router_service.py` | Intent routing and execution handlers |
| `cantina_os/event_payloads.py` | Event payload models (IntentPayload, IntentExecutionResultPayload) |
| `cantina_os/core/event_topics.py` | Event topic enums (INTENT_DETECTED, INTENT_EXECUTION_RESULT, LLM_RESPONSE) |

---

## Core Concepts

### Tool = OpenAI Function Call
A tool is a function that the LLM can choose to call. Defined as Pydantic model + JSON schema.

### Intent = Parsed Tool Call
When the LLM returns a tool call, GPTService parses and validates it, then emits INTENT_DETECTED event.

### Tool Handler = Intent Executor
IntentRouterService listens to INTENT_DETECTED and routes to service-specific handlers (e.g., MusicControllerService).

### Verbal Feedback = Explanation
After tool execution, GPTService generates natural language description of what action was taken.

---

## Tool Definition Checklist

```python
# 1. Create Pydantic model for parameters
class MyToolParams(BaseModel):
    required_param: str = Field(..., description="Description")
    optional_param: Optional[int] = Field(None, description="Description")

# 2. Create function definition generator
def create_my_tool_function() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "What this tool does",
            "parameters": MyToolParams.schema()
        }
    }

# 3. Add to AVAILABLE_FUNCTIONS list
AVAILABLE_FUNCTIONS = [
    create_play_music_function(),
    create_stop_music_function(),
    create_set_eye_color_function(),
    create_my_tool_function()  # <- Add here
]

# 4. Add to function_name_to_model_map()
def function_name_to_model_map() -> Dict[str, Any]:
    return {
        "play_music": PlayMusicParams,
        "stop_music": StopMusicParams,
        "set_eye_color": SetEyeColorParams,
        "my_tool": MyToolParams  # <- Add here
    }
```

---

## Intent Handler Checklist

```python
# In IntentRouterService.__init__()
self._intent_handlers = {
    "my_tool": self._handle_my_tool_intent
}

# Add handler method
async def _handle_my_tool_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
    try:
        # Extract parameters
        param1 = parameters.get("required_param")
        
        # Execute the action
        result = await self._do_something(param1)
        
        # Return result dict
        return {
            "success": True,
            "result_key": result,
            "message": f"Action completed: {result}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
```

---

## Event Flow (Minimal)

```
User speaks
  ↓
TRANSCRIPTION_FINAL → GPTService
  ↓
OpenAI API (with tools) → tool_calls in response
  ↓
LLM_RESPONSE + INTENT_DETECTED
  ↓
ElevenLabsService (speech)  &  IntentRouterService (execution)
  ↓
INTENT_EXECUTION_RESULT
  ↓
GPTService (verbal feedback)
  ↓
LLM_RESPONSE (feedback)
  ↓
ElevenLabsService (speech)
```

---

## SessionMemory Message Roles

| Role | Created By | Purpose |
|------|-----------|---------|
| `system` | GPTService | System prompt defining DJ R3X personality |
| `user` | Transcription service | User's spoken input |
| `assistant` | OpenAI API | LLM response (may include tool_calls) |
| `tool` | GPTService | Tool execution result |

---

## Streaming Tool Call Reconstruction

When streaming=True, tool calls arrive in fragments:

```
Chunk 1: {"id": "call_123", "function": {"name": "p"}}
Chunk 2: {"id": "call_123", "function": {"name": "lay_music"}}
Chunk 3: {"id": "call_123", "function": {"arguments": "{\"t"}}
Chunk 4: {"id": "call_123", "function": {"arguments": "rack\": \"cantina"}}
...

Result after reconstruction:
{
  "id": "call_123",
  "type": "function",
  "function": {
    "name": "play_music",
    "arguments": "{\"track\": \"cantina_band\"}"
  }
}
```

**Detection of completion**: When JSON parses successfully (valid `}` at end), the tool call is marked complete and immediately processed.

---

## Parameter Validation Flow

```
Tool arguments JSON string: "{\"track\": \"cantina_band\"}"
                 ↓
           json.loads() → Python dict
                 ↓
  Pydantic model validation (PlayMusicParams)
                 ↓
    Raises ValidationError if invalid types/constraints
                 ↓
  model.model_dump() → Final parameters dict
                 ↓
    Passed to handler function
```

---

## Error Handling by Stage

| Stage | Error | Handling |
|-------|-------|----------|
| **Streaming** | Invalid JSON | Continue streaming, log warning, skip if incomplete |
| **Parsing** | JSONDecodeError | Log error, skip tool call, continue processing others |
| **Validation** | ValidationError | Log error, skip tool call, emit status event |
| **Execution** | Handler exception | Return error dict, emit failed INTENT_EXECUTION_RESULT |
| **API** | OpenAI API error | Log and emit SERVICE_STATUS error event |

---

## Two-Step Tool Calling Pattern (Why?)

**Step 1: Detect & Respond**
- LLM generates response text + tool calls simultaneously
- Response text synthesized to speech immediately
- Tool calls processed in parallel

**Step 2: Execute & Provide Feedback**
- Tool executes (may take time)
- Second GPT call generates verbal description of action
- Feedback synthesized and played

**Benefits:**
- User hears initial response within 2 seconds
- Tool execution happens in background
- Failure feedback still natural and conversational
- Reduces API latency perception

---

## Configuration for Tool Calling

```python
# In GPTService config:
{
    "MODEL": "gpt-4.1-mini",       # Optimized for function calling
    "STREAMING": True,              # Reconstruct tool calls from chunks
    "TEMPERATURE": 0.7,             # Balance between consistency and creativity
    "MAX_TOKENS": 4000,             # Prevent memory bloat
    "MAX_MESSAGES": 20,             # Rolling conversation window
}

# In OpenAI API request:
{
    "tools": self._tool_schemas,    # All registered tools
    "tool_choice": "auto"           # Let model decide when to use tools
}
```

---

## Current Tools Available

| Tool | Parameters | Use Case |
|------|-----------|----------|
| `play_music` | `track` (str) | Start music playback by track name or number |
| `stop_music` | (none) | Stop current music playback |
| `set_eye_color` | `color`, `pattern?`, `intensity?` | Change LED eye color and animation |

---

## Common Debugging Checklist

- [ ] Tool appears in `AVAILABLE_FUNCTIONS`
- [ ] Tool appears in `function_name_to_model_map()`
- [ ] Handler registered in `self._intent_handlers`
- [ ] Pydantic model fields match OpenAI schema
- [ ] Handler returns dict with `success` key
- [ ] INTENT_DETECTED event emitted successfully
- [ ] INTENT_EXECUTION_RESULT event emitted successfully
- [ ] Verbal feedback API call succeeds

---

## Performance Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| ASR latency | < 500ms | Deepgram transcription |
| LLM latency | < 2s | OpenAI API (streaming) |
| Tool call reconstruction | < 100ms | JSON parsing |
| Tool execution | < 1s | Handler execution |
| TTS latency | < 3s | ElevenLabs API |
| Total round-trip | < 5s | User-perceived latency |

---

## Adding a New Tool - Step-by-Step

### Step 1: Define in `command_functions.py`

```python
class NewToolParams(BaseModel):
    param1: str = Field(..., description="...")

def create_new_tool_function() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "new_tool",
            "description": "...",
            "parameters": NewToolParams.schema()
        }
    }

AVAILABLE_FUNCTIONS.append(create_new_tool_function())

def function_name_to_model_map() -> Dict[str, Any]:
    return {
        ...
        "new_tool": NewToolParams
    }
```

### Step 2: Add Handler in `intent_router_service.py`

```python
self._intent_handlers = {
    ...
    "new_tool": self._handle_new_tool_intent
}

async def _handle_new_tool_intent(self, parameters: Dict[str, Any], conversation_id: Optional[str]) -> Dict[str, Any]:
    param1 = parameters.get("param1")
    # Execute...
    return {"success": True, "message": "..."}
```

### Step 3: Test

```bash
# Start DJ R3X system
cd cantina_os
../venv/bin/python -m cantina_os.main

# Test with voice: "activate new tool with param1 value"
```

### Step 4: Monitor Logs

- Check `INTENT_DETECTED` emitted with correct parameters
- Check handler executed successfully
- Check `INTENT_EXECUTION_RESULT` emitted with success=True
- Check verbal feedback generated and played

---

## Testing Tool Calling

```python
# Minimal test
async def test_play_music_tool():
    event_bus = AsyncIOEventEmitter()
    gpt_service = GPTService(event_bus, config)
    
    # Mock transcription
    payload = TranscriptionTextPayload(
        text="play music",
        source="test"
    )
    
    # Trigger processing
    await gpt_service._handle_transcription(payload.model_dump())
    
    # Verify INTENT_DETECTED emitted
    # Verify LLM_RESPONSE emitted
```

---

## Key Limitations & Future Work

| Issue | Current | Ideal |
|-------|---------|-------|
| **Tool Count** | 3 static tools | Dynamic registration |
| **JSON Repair** | Quote replacement only | Full JSON recovery |
| **API Calls** | 2 per intent (detect + feedback) | 1 unified call |
| **Tool Execution** | Separate service | Same service, cleaner |
| **Tool Call Logging** | Basic logging | Full audit trail |
| **Tool Result Caching** | None | Cache common results |

---

## Resources

- Main Analysis: `docs/dj-r3x-tool-calling-architecture-summary.md`
- Flow Diagram: `docs/tool-calling-flow-diagram.txt`
- OpenAI Docs: https://platform.openai.com/docs/guides/function-calling
- Pydantic Docs: https://docs.pydantic.dev/

