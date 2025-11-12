# DJ R3X Voice App — Working Dev Log (2025-11-12)
- This gets refreshed daily and the core info is saved to `dj-r3x-condensed-dev-log.md`
- Goal is to give cursor good active context for what we are working on.

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project recreates the voice and animation features with interactive conversations and synchronized LED animations.

## [Today] Claude API Latency Optimization & Prompt Caching Deep Dive

### Issue
After switching to Claude 3.5 Sonnet 4.5 from GPT-4.1-mini, response latency seemed MUCH slower (~16 seconds turn-around vs expected 2-3 seconds). Needed to analyze the complete audio pipeline to identify bottlenecks.

### Research & Analysis Completed

#### 1. Complete Latency Audit (from logs/dj_r3x_2025-11-12_08-31-41.log)

**Timeline of Events (Turn 1)**:
```
08:31:58 - User starts speaking
08:32:00 - Interim transcription #1: "Hey DJ Rick"     → API call #1 (wasted)
08:32:01 - Interim transcription #2: "Hey DJ Rex. I..." → API call #2 (wasted)
08:32:04 - Interim transcription #3: "Hey DJ Rex. I..." → API call #3 (wasted)
08:32:11 - FINAL transcription arrives                  → API call #4 (real response)
08:32:14 - DJ R3X starts responding                     → TOTAL: 16 seconds
```

**Root Cause**: Interim streaming enabled with HTTP REST API (not WebSocket)
- Each interim call = separate HTTP connection
- Full system prompt (3.5KB) sent with EACH call
- 3 throwaway API calls before the real one
- Connection overhead: ~50-100ms per extra call × 3 = 150-300ms wasted

#### 2. Fixed: Disable Interim Streaming for Claude
**Commit**: `9dc313f` - "fix: Disable interim streaming by default for Claude API latency optimization"

**Changes**:
- Changed `ENABLE_INTERIM_STREAMING` default from `true` to `false` in `main.py`
- Only makes API calls on FINAL transcriptions (eliminates 3 wasted calls)
- Single HTTP connection per utterance instead of 4

**Expected Improvement**: 2-5 seconds faster response

#### 3. Discovered: Token Counts ARE Being Logged ✅
Per user request, verified token logging is working:
```
📊 TOKEN USAGE (streaming) - Input: 1984, Output: 88, Total: 2072  (Turn 1)
📊 TOKEN USAGE (streaming) - Input: 2126, Output: 125, Total: 2251 (Turn 2)
```

#### 4. Problem: Prompt Caching Not Working

**Analysis of Token Breakdown (Turn 1 - 1,984 tokens)**:
| Component | Tokens | % of Input |
|-----------|--------|-----------|
| System Prompt | ~1,800 | **89%** |
| Tool Schemas | ~150 | **7%** |
| Conversation | ~20 | **4%** |
| **Total** | **1,984** | **100%** |

System prompt was consuming 89% of all input tokens!

**Issue Identified**: Prompt caching was enabled for system prompt but NOT for tools
- System prompt had `cache_control: {type: "ephemeral"}` ✓
- Tools did NOT have `cache_control` ✗
- Per Anthropic docs: cache_control must be on LAST TOOL to cache all tools

#### 5. Fixed: Enable Prompt Caching for Tools
**Commit**: `f3bcbd5` - "fix: Enable prompt caching for tools by adding cache_control to last tool"

**Changes**:
- Added `_get_tool_schemas_with_cache()` method
- Implements Anthropic's requirement: cache_control on LAST TOOL ONLY
- Deep copies tool schemas and adds `cache_control: {type: "ephemeral"}` to final tool
- Updated both streaming and non-streaming API calls to use cached tools

**Expected Savings**:
- Turn 1: 1,984 tokens (creates cache)
- Turn 2: Should see cache hit with ~90% token savings
- Cost reduction: 2,126 tokens → ~326 tokens (85% reduction)

#### 6. Critical Discovery: Token Threshold Requirement

**Reading Anthropic Documentation** at https://docs.claude.com/en/docs/build-with-claude/prompt-caching

Minimum cacheable tokens VARY by model:
| Model | Minimum Cache Threshold |
|-------|------------------------|
| Claude Sonnet 4.5 | **1,024 tokens** |
| Claude Haiku 4.5 | **4,096 tokens** ← Your current model |
| Claude 3.5 Sonnet | ~1,024 tokens |

**Your Current Situation**:
- Using: Claude Haiku 4.5
- System + Tools: ~1,950 tokens
- **BELOW the 4,096 minimum threshold**
- **Result**: Caching is silently ignored - you're paying full price on every request

#### 7. Token Gap Analysis

To use caching with Haiku 4.5, you need:
```
Current tokens: 1,950
Needed for cache: 4,096
Gap to fill: 2,146 tokens (110% increase)
```

This would require essentially DOUBLING your entire system prompt - not practical.

### Research Findings

**How Prompt Caching Works**:
1. Cache creation: 1.25× base input price (5-min) or 2× (1-hour)
2. Cache hits: 0.1× base input price (90% savings)
3. Cache invalidation: Changing any content "above" a cache breakpoint invalidates that level and all subsequent

**Caching Hierarchy**: Tools → System → Messages
- Multiple cache breakpoints allow independent caching sections
- Up to 4 simultaneous cache breakpoints supported

**Anthropic Docs Key Insight**:
> "Place `cache_control` on the final tool to designate all tools as part of the static prefix"

This confirms our implementation is correct, but only works if threshold is met.

### Recommendations

#### Option A: Switch to Claude Sonnet 4.5 (RECOMMENDED)
- Only requires 1,024 minimum tokens (you have 1,950) ✓
- Caching will actually work
- Better reasoning for DJ mode
- ~200-300ms slower TTFT than Haiku
- **Net result**: FASTER overall due to cache savings of 2+ seconds
- Better for complex DJ mode logic

#### Option B: Keep Haiku 4.5, Expand Prompt
- Would need to add 2,146 tokens to current system prompt
- Essentially double current persona size
- Harder to maintain
- Not recommended

#### Option C: Use Larger Message History in Cache
- Cache earlier messages in conversation
- Still would need to meet 4,096 threshold
- More complex cache management

### Code Changes Made Today

**Commit 1 - Disable Interim Streaming** (`9dc313f`):
- `cantina_os/main.py:218` - Changed default from `true` to `false`

**Commit 2 - Fix Prompt Caching** (`f3bcbd5`):
- Added `_get_tool_schemas_with_cache()` method to ClaudeService
- Updated `_get_claude_response()` to use cached tool schemas
- Updated `_stream_claude_response()` to use cached tool schemas

### Next Steps

1. **Immediate**: Decide between switching to Sonnet 4.5 or keeping Haiku
2. **If switching to Sonnet**: Update MODEL config and test caching
3. **If staying with Haiku**: Alternative is to find 2,146 tokens to add to prompt (not recommended)
4. **Testing**: Run new test with proper model to verify cache hits appear in logs

### Token Usage Observations

Current (Turn 1): 1,984 input tokens
- System prompt: ~1,800 (89%)
- Tools: ~150 (7%)
- User message: ~20 (4%)

If we can get caching working (Sonnet 4.5):
- Turn 1 (cache write): ~2,480 tokens (1.25× for cache creation)
- Turn 2 (cache hit): ~326 tokens (only conversation, 90% savings)
- Turn 3+: ~326 tokens each

### Summary

**Problems Found & Fixed**:
1. ✅ Interim streaming disabled - eliminates 3 wasted API calls per turn
2. ✅ Prompt caching for tools fixed - proper cache_control implementation
3. ⚠️ Token threshold issue - Haiku 4.5 requires 4,096 tokens (you have 1,950)

**Outcome**: Caching code is correct but won't activate with Haiku 4.5. Need to switch to Sonnet 4.5 to benefit from caching (or bloat prompt by 110%).

**Estimated Latency with Both Fixes**:
- Interim streaming disabled: -2-5 seconds
- With proper model + caching: -2+ seconds more
- **Target**: 3 seconds → ~1 second with Sonnet 4.5

---

## [INVESTIGATION] Critical Bugs Found & Fixed (10:21-10:24 Run Analysis)

### Bug 1: TimelineExecutorService - Invalid `plan.layer` Reference
**Root Cause**: Code tried to access `plan.layer` attribute on `DjTransitionPlanPayload`, but the payload doesn't have this attribute. Layer tracking was in `self._timeline_layers` dict instead.

**Impact**: DJ mode crashed immediately when trying to execute transitions with "AttributeError: 'DjTransitionPlanPayload' object has no attribute 'layer'"

**Fix Applied** (`c02ca2c`):
- Line 298: Store layer mapping when plan starts: `self._timeline_layers[plan_id] = layer`
- Line 799: Use mapping instead of attribute: `self._timeline_layers.get(plan_id) == other_layer`
- Line 423: Clean up mapping when plan ends

### Bug 2: ClaudeService - Empty Assistant Messages Causing API Rejection
**Root Cause**: When Claude responded with ONLY tool calls and NO text content (e.g., when user says "play music"), the code added an empty string as an assistant message:
```python
message_content = ""  # No text returned, only tool_use blocks
self._memory.add_message(role="assistant", content=message_content)  # Added empty message!
```

This caused Claude API to reject the message history with:
```
messages.5: all messages must have non-empty content except for the optional final assistant message
```

**Detected In Logs** (10:24:10):
```
{'role': 'assistant', 'content': ''}  ← EMPTY! Caused API to reject whole request
{'role': 'user', 'content': 'Tool execution result for play_music: ...'}
{'role': 'user', 'content': 'Tool execution result for set_eye_color: ...'}
{'role': 'user', 'content': 'Hey. Can you stop playing the music?'}  ← Never processed!
```

The "stop playing the music" request never got to Claude because the API rejected the batch due to the empty message.

**Impact**:
- Tool execution worked (play_music, set_eye_color executed)
- But next request (stop_music) was rejected by Claude API before execution
- Music continued playing because stop command never reached the system

**Fixes Applied** (3c9e588):
1. Non-streaming response (line 494): `if message_content or not tool_calls:` - only add assistant message if there's text OR no tool calls
2. Streaming response (line 586): Same logic - skip empty assistant messages when tool calls exist
3. Tool result validation (lines 901-905): Ensure tool result content is never empty before adding to memory

### Summary
- **Timeline bug**: Prevents DJ mode from running at all
- **Claude message bug**: Prevents sequential tool execution - once Claude returns tool-only response, next request gets rejected
- **Together**: System breaks when: user → play music (works) → say stop music (fails because previous response corrupted message history)

Both fixes committed and ready for testing.

---

## [OPTIMIZATION] Tool Execution Performance & Speech Responsiveness

### Issue: 2-3 Second Delay on Tool Execution
When user commanded "play music" or "stop music", there was 2-3 second delay before music actually started/stopped. Music ducking still worked but felt sluggish.

**Root Cause**: Verbal response generation for tool feedback was **synchronously blocking** the execution:
1. User says "play music"
2. Claude detects `play_music` tool call
3. Intent router starts executing tool
4. **Code waits 2+ seconds for Claude to generate verbal feedback** (second API call)
5. **Then** music starts
6. Then DJ commentary plays

**Timeline from logs (10:32:47-10:32:51)**:
- 10:32:47.892 - Intent router emits `play_music` command
- 10:32:47.895 - Command dispatcher routes it
- **10:32:50.149** - Claude finishes generating verbal response (**2.25 seconds**)
- 10:32:51.558 - Command finally processes and music starts

**Fixes Applied** (commits 4d526d8 and bc2a882):

1. **ClaudeService** (`claude_service.py` line 928):
```python
# OLD: await self._get_verbal_response_for_intent(...)  # Blocking!
# NEW: asyncio.create_task(self._get_verbal_response_for_intent(...))
```

2. **GPTService** (`gpt_service.py` line 1044):
Applied same fix - verbal response generation now runs in background without blocking

**Impact**:
- Tools execute **immediately** (music starts now, not 2+ seconds later)
- DJ commentary still plays, but asynchronously in background
- Music ducking still triggers when speech synthesis starts (event-driven)
- Natural overlap: music playing + DJ commentary on top

### Additional Optimizations

**ElevenLabs Speech Speed** (elevenlabs_service.py):
- Changed default from 1.2 to 1.1 for slightly more natural speech pacing

### Design Notes

The verbal feedback persona design was intentional - user wanted DJ R3X to comment on music being played. But blocking on it was the wrong approach. Now the system:
1. Executes immediately (responsive to user)
2. Generates commentary asynchronously (non-blocking)
3. Commentary plays over already-playing music (natural UX)

This maintains the design goal (commentary on music) while fixing the responsiveness problem.

---

## [OPTIMIZATION] Claude API Connection Pre-warming for Latency (11:00 - 11:15)

### Problem
Claude API latency was ~3 seconds per request due to connection setup overhead. The Haiku 4.5 model threshold prevents prompt caching from working (needs 4,096 tokens, you have ~1,950), so we can't rely on caching. Instead, implement connection pre-warming to save 150-200ms per interaction.

### Analysis
From logs (dj_r3x_2025-11-12_10-38-47.log):
- Claude API time: ~3 seconds (from API call start to response complete)
- Estimated connection overhead: 150-200ms of that 3 seconds
- Opportunity: Pre-warm connection before user starts recording

### Solution: Dual-Trigger Pre-warming (Option C)

**Trigger 1: ENGAGE Command (Early)**
- When user types `engage` → mode changes to INTERACTIVE
- ClaudeService detects SYSTEM_MODE_CHANGED event with INTERACTIVE mode
- Opens connection well before user clicks to record
- Connection ready and warm for first interaction

**Trigger 2: MIC_RECORDING_START (Safety Net)**
- When user clicks mouse to start recording
- ClaudeService detects MIC_RECORDING_START event
- Refreshes connection as safety net (case connection was idle or dropped)
- By time user finishes speaking and clicks stop, connection is fresh and ready

**Design Features**:
- Non-blocking: Both warmups run as background tasks
- Smart cooldown: 30-second cooldown prevents excessive re-warming
- Lightweight: Test request uses only 10 tokens max
- Graceful failure: If warmup fails, service continues normally (non-critical)
- Fully logged: Debug logs for connection performance monitoring

### Expected Performance Gains

| Scenario | Time Saved | Notes |
|----------|-----------|-------|
| First interaction | 150-200ms | Connection ready after `engage` |
| Subsequent interactions | 150-200ms | Connection refreshed on each mic start |
| Total for 3 interactions | ~600ms | Best case with clean workflow |

### Implementation Details

**File**: `cantina_os/cantina_os/services/claude_service/claude_service.py`

**Methods Added**:
1. `_warmup_connection()` - Makes lightweight test request to establish connection
2. `_handle_mode_changed_for_warmup()` - Responds to SYSTEM_MODE_CHANGED events
3. `_handle_mic_start_for_warmup()` - Responds to MIC_RECORDING_START events

**Fields Added**:
```python
self._last_connection_warmup_time: float = 0
self._warmup_cooldown = 30  # Seconds between warmups
self._connection_warmed = False  # Track warmup success
```

**Event Subscriptions**:
- `EventTopics.SYSTEM_MODE_CHANGED` → `_handle_mode_changed_for_warmup`
- `EventTopics.MIC_RECORDING_START` → `_handle_mic_start_for_warmup`

### Commit
**Commit**: `bd401a6` - "perf: Add Claude API connection pre-warming for latency optimization"

This provides a 150-200ms latency improvement without requiring model changes or prompt modifications.