# DJ R3X Voice App — Working Dev Log (Engineering Journal)
- This gets refreshed daily and the core info is saved to `dj-r3x-condensed-dev-log.md`
- Goal is to give cursor good active context for what we are working on.

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project recreates the voice and animation features with interactive conversations and synchronized LED animations.

## 🎯 Today's Progress (May 16, 2025)

### ✅ Implemented Layered Timeline Architecture
Successfully implemented the three core services for the layered timeline architecture:

1. **MemoryService**
- Working memory and state management
- Chat history tracking with pruning
- Intent and music state tracking
- Wait-for predicate functionality
- Full test coverage implemented

2. **BrainService**
- Three-Call GPT pattern implementation
- Intent handling for music commands
- Track intro generation
- Plan creation for timeline execution
- Test suite covering core functionality

3. **TimelineExecutorService**
- Layered execution (ambient, foreground, override)
- Audio ducking coordination
- Step execution with proper sequencing
- Layer priority management
- Comprehensive test coverage

### 🔄 Integration Status
- Services registered in main.py
- Event topics updated for new timeline events
- Event payloads defined for all new message types
- Service initialization order established

### 🧪 Testing Progress (Updated)
- Comprehensive test suites implemented for all three services:
  - BrainService: Intent handling, music playback, track intro generation
  - TimelineExecutorService: Layer management, audio ducking, plan execution
  - MemoryService: State management, chat history, wait predicates

### 🔍 Test Coverage Details
1. **BrainService Tests**
   - Intent detection and routing
   - Music playback event handling
   - Track intro plan generation
   - LLM response processing

2. **TimelineExecutorService Tests**
   - Layer priority management (ambient/foreground/override)
   - Audio ducking coordination for speech
   - Plan execution sequencing
   - Step execution with proper timing

3. **MemoryService Tests**
   - State get/set operations
   - Chat history management and pruning
   - Wait predicate functionality
   - Event handling for music, intents, and transcriptions

### 📝 Next Steps
1. Run integration tests with all services
2. Implement real GPT integration for track intros
3. Add monitoring and debugging tools
4. Expand test coverage for edge cases

### 🐛 Known Issues
- Need to implement proper event waiting in timeline executor
- Speech synthesis completion detection needs refinement
- Track intro generation currently uses placeholder text

### 🔧 Timeline Services Initialization Error (Fixed)
**Issue Identified**: The application failed to start due to incorrect service initialization pattern for the new timeline services. The error was:
```
Error creating service memory_service: StandardService.__init__() missing 2 required positional arguments: 'event_bus' and 'config'
```

**Root Cause**: The service initialization in main.py was using keyword arguments for the new timeline services (brain_service, timeline_executor_service, memory_service) instead of following the required positional argument pattern defined in our service standards.

**Fix Applied**: 
- Updated service initialization in main.py to use correct positional arguments (event_bus, config) for all StandardService-based classes
- Ensured consistent initialization pattern across all timeline services
- Added clear documentation in SERVICE_TEMPLATE_GUIDELINES.md to prevent similar issues

**Impact**: All timeline services now initialize correctly following our service standards.

### 📚 Architecture Notes
The layered timeline system now provides:
- Clear separation of concerns between memory, planning, and execution
- Proper handling of concurrent timelines
- Coordinated audio ducking for speech
- Priority-based layer management

### 🔧 Critical Import Path Issue (Fixed)
**Issue Identified**: The application failed to start due to an import path issue with `simple_eye_adapter.py`. The error was:
```
ModuleNotFoundError: No module named 'cantina_os.services.simple_eye_adapter'
```

**Root Cause**: There was a mismatch between the package installation method (development mode) and the import style. The package setup was expecting relative imports within the service directory, but the code was using fully-qualified absolute imports.

**Fix Applied**: 
- Changed absolute imports to relative imports for modules within the same directory
- Updated all affected services to use consistent import patterns
- Preserved cross-package imports to maintain architectural standards

**Impact**: This resolves startup failures and prevents similar issues in the new timeline services.

**Recommendation**:
- Use relative imports (from `.modulename import X`) for modules within the same directory
- Use absolute imports (from `cantina_os.services.X import Y`) for cross-directory imports
- Maintain this pattern consistently across all services

### 🔧 Service Initialization Pattern Fix
**Issue**: Three new services (MemoryService, BrainService, TimelineExecutorService) failed to initialize due to incorrect constructor pattern.

**Root Cause**: Services were using keyword-only arguments pattern instead of following StandardService initialization requirements:
```python
# INCORRECT (Previous)
def __init__(self, *, name: str = "service_name", config: Dict[str, Any] | None = None):
    super().__init__(name=name)

# CORRECT (Fixed)
def __init__(self, event_bus, config=None, name="service_name"):
    super().__init__(event_bus, config, name=name)
```

**Fix Applied**: 
- Updated all three services to use correct positional parameter pattern
- Ensured `event_bus` is first parameter
- Properly passed both `event_bus` and `config` to `super().__init__`
- Removed keyword-only arguments syntax

**Impact**: Services now initialize correctly following our service standards.

### 🔧 Event Subscription Pattern Fix
**Issue**: BrainService and TimelineExecutorService were using incorrect subscription patterns.

**Root Cause**: Services were wrapping subscription calls in asyncio.create_task() instead of properly awaiting them:
```python
# INCORRECT (Previous)
asyncio.create_task(self._subscribe(EventTopics.TOPIC, self._handle_event))

# CORRECT (Fixed)
await self._subscribe(EventTopics.TOPIC, self._handle_event)
```

**Fix Applied**:
- Removed asyncio.create_task() wrappers around subscription calls
- Used proper await pattern as specified in guidelines
- Ensured all subscriptions are properly tracked for cleanup

**Impact**: Services now handle events reliably with proper tracking for cleanup, preventing potential race conditions or missed events.

### 📋 Next Steps
1. Run integration tests with fixed services
2. Verify timeline execution with all layers
3. Test memory persistence across service lifecycle
4. Document initialization pattern in service template guide

### 🔧 Event Topic Reference Issue Fix
**Issue**: All three new services (MemoryService, BrainService, TimelineExecutorService) still failed to initialize with: `type object 'EventTopics' has no attribute 'EXAMPLE_TOPIC'`.

**Root Cause**: 
1. The `StandardService` class our services inherit from is actually re-exported from `service_template.py`
2. The template contains a comment example with `EventTopics.EXAMPLE_TOPIC`
3. When our services called `super()._setup_subscriptions()`, it tried to subscribe to this non-existent topic

**Fix Applied**:
- Removed calls to `super()._setup_subscriptions()` in all three services
- Kept only essential service-specific subscriptions needed for the initial flow

**Impact**: Services can now initialize without trying to access the template's example event topic that doesn't exist in our enum.

**Lesson Learned**:
- Template code should clearly mark example code that needs to be removed
- When extending classes, be aware of their actual implementation details
- Calling `super()` methods can sometimes invoke template/example code that should be removed

### 🔧 Missing _subscribe Method Fix
**Issue**: After removing `super()._setup_subscriptions()`, all three services failed with: `'ServiceName' object has no attribute '_subscribe'`

**Root Cause**:
1. The `_subscribe` helper method is defined in the `ServiceTemplate` class
2. By avoiding the call to `super()._setup_subscriptions()`, we also lost access to this helper method
3. Our services were still trying to use `_subscribe` but couldn't find it

**Fix Applied**:
- Added the `_subscribe` method implementation to each service:
```python
async def _subscribe(self, topic: EventTopics, handler: Callable) -> None:
    """Safe async subscription wrapper that tracks tasks for cleanup."""
    self._subs.append((topic, handler))
    task = asyncio.create_task(self.subscribe(topic, handler))
    self._tasks.append(task)
    await task  # Ensure the subscription is established before return
```

**Impact**: Services can now properly subscribe to events using the safe subscription wrapper.

**Lesson Learned**:
- When removing calls to parent methods, be aware of what helper methods you might be losing access to
- Carefully inspect template code to understand all dependencies before modifying
- Sometimes you need to copy helper methods when avoiding parent class calls

### 📋 Next Steps
1. Run integration tests with fixed services
2. Verify timeline execution with all layers
3. Test memory persistence across service lifecycle
4. Document initialization and subscription patterns for future service development

## 📝 Latest Dev Log Entry (May 16, 2025 - Evening Implementation Issues)

### 🐛 Layered Timeline Implementation Issues (#12)

**Issue Summary**: Tested a voice interaction ("Play me some funky music") and identified key issues with our new layered timeline architecture. While all three services (BrainService, TimelineExecutorService, MemoryService) initialized successfully, they're not operating correctly in the interaction flow.

**Root Causes**:

1. **Intent Routing Bypass**: IntentRouterService is bypassing the BrainService completely and sending commands directly to hardware services:
   ```python
   # Current behavior (incorrect)
   await self.emit(EventTopics.MUSIC_COMMAND, music_payload)
   
   # Expected behavior
   await self.emit(EventTopics.INTENT_DETECTED, intent_payload)  # To BrainService
   ```

2. **Missing BrainService Logic**: BrainService isn't receiving or handling intents, and consequently not generating the three-call GPT pattern or track-specific intro plans.

3. **TimelineExecutor Timing Issues**: Experiencing timeout errors waiting for speech synthesis to complete:
   ```
   ERROR - Timeout waiting for speech synthesis to complete for step intro
   ```

4. **MusicControllerService Missing Method**: Error during mode transition:
   ```
   AttributeError: 'MusicControllerService' object has no attribute '_handle_stop_request'
   ```

5. **Layer Management Problems**: While some layer coordination is happening ("Pausing ambient layer due to foreground priority"), the full layer management isn't working.

**Required Fixes**:

1. Modify IntentRouterService to emit `INTENT_DETECTED` to BrainService instead of direct hardware commands
2. Implement proper intent handling, music command forwarding, and GPT three-call pattern in BrainService
3. Fix TimelineExecutorService timeout handling and speech synthesis completion detection
4. Add the missing `_handle_stop_request` method to MusicControllerService
5. Ensure proper layer management in TimelineExecutorService

**Expected Impact**: Once fixed, the system should follow the flow defined in the PRD:
1. Voice transcription → GPT tool call → BrainService → MusicController (≤1.25s)
2. Quick filler line generated immediately (non-ducked)
3. Track-specific intro generated after music starts (with ducking)
4. Proper layer management including ambient resumption

**Implementation Plan**:
1. Fix IntentRouterService first to enable the core flow
2. Fix BrainService to handle intents and generate proper plans
3. Fix TimelineExecutorService timing and layer management issues
4. Add missing MusicControllerService method
5. Test the complete flow with logging to verify

**Related Documents**: Reference CantinaOS-Layered Timeline-PRD.md for the intended architecture and event flow.

## 🔧 Infinite Music Loop Bug Fix (#13)

**Issue Summary**: Voice interaction for "Play me some funky music" triggered an infinite loop that caused repeated command processing, multiple track playback attempts, and service instability.

**Root Cause Analysis**:
1. **Event Circular References**: IntentRouterService modified to emit INTENT_DETECTED to BrainService, but BrainService was forwarding to CommandDispatcher, which sent it back to IntentRouterService
2. **Service Communication Issues**: Improper direct pathways between GPT service, BrainService, and music controller services
3. **Duplicate Command Generation**: Same command being processed and duplicated at multiple points in the event chain

**Solution Implemented**:
1. Created direct pathway from GPT service to BrainService via new `BRAIN_MUSIC_REQUEST` event topic
   ```python
   # In event_topics.py
   BRAIN_MUSIC_REQUEST = "/brain/music_request"
   ```

2. Enhanced BrainService with direct track selection and playback logic:
   ```python
   async def _handle_music_request(self, payload):
       track_query = payload.get("track_query", "")
       selected_track = await self._smart_track_selection(track_query)
       await self._emit_dict(EventTopics.MUSIC_COMMAND, MusicCommandPayload(...))
   ```

3. Implemented robust track selection algorithm in BrainService:
   ```python
   async def _smart_track_selection(self, query):
       # Direct match against track names
       # Keyword matching (e.g., "funky" → "Elem Zadowz - Huttuk Cheeka")
       # Fallback to first track if no match
   ```

4. Modified GPT service to use the direct path for music intents:
   ```python
   # Special case for play_music intent
   if function_name == "play_music":
       brain_payload = {
           "track_query": function_args.get("track", ""),
           "tool_call_id": tool_call.get("id")
       }
       await self.emit(EventTopics.BRAIN_MUSIC_REQUEST, brain_payload)
   ```

5. Added proper event result emission for verbal responses:
   ```python
   async def _emit_intent_execution_result(self, intent_name, parameters, result, tool_call_id):
       # Properly formatted result for GPT's verbal feedback generation
   ```

**Impact**:
- Eliminated circular event references and infinite loops
- Preserved GPT → Brain → Music controller architecture
- Improved track selection with enhanced matching algorithm
- Maintained proper verbal feedback generation
- Fixed interaction flow from voice to music playback

**Architecture Improvements**:
- Clearer service responsibilities with direct communication pathways
- Removed IntentRouterService from music flow to simplify architecture
- Better track selection logic centralized in BrainService
- More reliable event handling with proper subscription patterns

**Testing Notes**:
- Voice command "Play me some funky music" now correctly plays a single track
- GPT correctly generates verbal feedback about track selection
- BrainService correctly handles track selection with smart matching
- Track-specific intro is generated after music starts

**Next Steps**:
- Add unit tests for the new direct pathway
- Enhance track selection with more sophisticated matching
- Implement proper track metadata retrieval from MusicController
- Document the new architecture in system diagrams

## 🔧 Timeline Execution & Audio Ducking Fix (#14)

**Issue Summary**: Identified and fixed two critical issues in the music playback system:
1. **Circular Logic for Track Stop**: Commands to stop music were creating a loop between GPT Service → IntentRouterService → BrainService → MusicController, causing instability
2. **Timeline Executor Bypassing**: BrainService was emitting direct `MUSIC_COMMAND` events instead of creating plans for TimelineExecutorService, bypassing audio ducking

**Changes Implemented**:
1. Added `BRAIN_MUSIC_STOP` event topic as direct path for stop commands (similar to `BRAIN_MUSIC_REQUEST`)
2. Modified BrainService to create proper plans for both play and stop commands:
   ```python
   # Create a plan for the timeline executor to stop music
   stop_plan = PlanPayload(
       plan_id=str(uuid.uuid4()),
       layer="foreground",
       steps=[
           PlanStep(id="stop_music", type="speak", text="Stopping the music now."),
           PlanStep(id="stop_command", type="play_music", genre="stop")
       ]
   )
   await self._emit_dict(EventTopics.PLAN_READY, stop_plan)
   ```
3. Updated GPT Service to use direct `BRAIN_MUSIC_STOP` path for stop music commands
4. Modified TimelineExecutorService to handle special "stop" genre value:
   ```python
   if step.genre == "stop":
       await self._emit_dict(EventTopics.MUSIC_COMMAND, {"action": "stop"})
       return True, {"action": "stop"}
   ```

**Impact**:
- Eliminated circular event references that caused infinite loops
- Ensured proper audio ducking through TimelineExecutorService
- Music commands now follow the correct architectural flow
- Both play and stop commands work reliably with consistent ducking

## 🔧 Immediate Audio Ducking & TimelineExecutor Fix (#15)

**Issue Summary**: The system had two critical audio ducking issues:
1. **Delayed Ducking on Voice Input**: Audio ducking wasn't triggered until TTS started, creating a poor UX with music continuing at full volume during voice processing
2. **TimelineExecutorService Initialization Failure**: Service would not start due to incorrect initialization pattern and Pydantic validation errors with ducking values

**Root Causes**:
1. MouseInputService wasn't emitting ducking events on click, only starting recording
2. MusicControllerService wasn't subscribed to direct ducking events
3. TimelineExecutorService used incorrect initialization signature (keyword-only arguments)
4. Pydantic model validation errors with float-to-int conversion

**Changes Implemented**:
1. Updated MouseInputService to emit ducking events immediately on mouse click:
   ```python
   # First emit audio ducking to immediately lower music volume
   await self.emit(EventTopics.AUDIO_DUCKING_START, {
       "reason": "voice_recording"
   })
   ```

2. Added direct ducking event handlers to MusicControllerService:
   ```python
   await self.subscribe(EventTopics.AUDIO_DUCKING_START, self._handle_audio_ducking_start)
   await self.subscribe(EventTopics.AUDIO_DUCKING_STOP, self._handle_audio_ducking_stop)
   ```

3. Fixed TimelineExecutorService initialization to follow architecture standards:
   ```python
   def __init__(
       self,
       event_bus,  # First parameter
       config: Dict[str, Any] | None = None,
       name: str = "timeline_executor_service",
   ) -> None:
       super().__init__(event_bus, config, name=name)
   ```

4. Enhanced Pydantic configuration with proper Field declarations:
   ```python
   default_ducking_level: float = Field(default=0.3, description="Default ducking level (0.0-1.0)")
   ducking_fade_ms: int = Field(default=500, description="Ducking fade time in milliseconds")
   ```

**Impact**:
- Audio ducking now happens immediately on mouse click, before voice processing begins
- Smoother user experience with no delay in audio ducking when starting to talk
- TimelineExecutorService initializes properly following our architecture standards
- More robust configuration validation with descriptive fields

## 🔧 Comprehensive Audio Ducking Implementation (#16)

**Issue Summary**: The system had inconsistent audio ducking behavior:
1. Track intro speech from timeline plans worked correctly (ducking/unducking)
2. Direct LLM responses (like jokes) did not trigger audio ducking
3. Microphone activity should also trigger audio ducking

**Changes Implemented**:
1. Enhanced TimelineExecutorService to handle ALL audio events:
   ```python
   # Added subscriptions to LLM_RESPONSE and voice listening events
   self.subscribe(EventTopics.LLM_RESPONSE, self._handle_llm_response)
   self.subscribe(EventTopics.VOICE_LISTENING_STARTED, self._handle_voice_listening_started)
   self.subscribe(EventTopics.VOICE_LISTENING_STOPPED, self._handle_voice_listening_stopped)
   ```

2. Created handlers for direct LLM responses:
   ```python
   async def _handle_llm_response(self, payload: Dict[str, Any]) -> None:
       # Only duck if this is a direct speech response (not a filler)
       response_type = payload.get("response_type", "")
       if response_type == "filler":
           self.logger.info("Received filler response, not ducking audio")
           return
           
       # Only duck if we have music playing and aren't already ducked
       if self._current_music_playing and not self._audio_ducked:
           self.logger.info("Ducking audio for direct LLM response")
           await self._emit_dict(
               EventTopics.AUDIO_DUCKING_START,
               {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
           )
           self._audio_ducked = True
   ```

3. Added microphone activity ducking:
   ```python
   async def _handle_voice_listening_started(self, payload: Dict[str, Any]) -> None:
       if self._current_music_playing and not self._audio_ducked:
           self.logger.info("Ducking audio for microphone activity")
           await self._emit_dict(
               EventTopics.AUDIO_DUCKING_START,
               {"level": self._config.default_ducking_level, "fade_ms": self._config.ducking_fade_ms}
           )
           self._audio_ducked = True
   ```

4. Maintained ducking between mic and speech response:
   ```python
   async def _handle_voice_listening_stopped(self, payload: Dict[str, Any]) -> None:
       # We intentionally don't unduck here since a speech response might follow
       # The full conversation flow is: mic → LLM response → unduck
       self.logger.info("Voice recording stopped, maintaining audio duck for potential response")
   ```

**Architecture Improvements**:
- Centralized all audio ducking logic in TimelineExecutorService
- Created a complete audio lifecycle: duck on mic → maintain during processing → unduck after speech
- Ensured consistent audio experience regardless of speech source (plans, direct LLM responses)
- Maintained proper state tracking with the `_audio_ducked` flag

**Design Notes**:
- This change aligns with the layered timeline architecture by treating ALL vocal activities as foreground events
- Maintains the principle that TimelineExecutorService is responsible for coordinating all temporal activities
- Properly chains events to ensure smooth transitions (mic → process → speak → unduck)
- State tracking prevents double-ducking or double-unducking

## 🔧 Source-Aware Music Playback Flow (#17)

**Issue Summary**: The system was generating track intros for all music playback, even when triggered from CLI commands, which was undesired.

**Root Cause Analysis**:
1. BrainService was listening for MUSIC_PLAYBACK_STARTED events from MusicControllerService
2. When any track started playing, regardless of source, BrainService would generate a track intro
3. There was no way to distinguish between voice-initiated and CLI-initiated playback

**Changes Implemented**:
1. Added source tracking to music playback events:
   ```python
   # In MusicControllerService._play_track_by_name:
   await self.emit(
       EventTopics.MUSIC_PLAYBACK_STARTED,
       {
           "track_name": track.name,
           "duration": track.duration,
           "source": source  # Added to track origin
       }
   )
   ```

2. Modified BrainService to check the source before generating track intros:
   ```python
   # In BrainService._handle_music_started:
   source = payload.get("source", "unknown")
   if source == "cli":
       self.logger.info(f"CLI-initiated music playback, skipping track intro")
       return
   
   # Only generate track intro for voice-initiated or unknown source playback
   await self._make_track_intro_plan(self._last_track_meta)
   ```

3. Updated TimelineExecutorService to pass "voice" as source when playing music from plans:
   ```python
   # In TimelineExecutorService._execute_play_music_step:
   await self._emit_dict(
       EventTopics.MUSIC_COMMAND,
       {
           "action": "play",
           "song_query": step.genre or "",
           "source": "voice",  # Indicate voice command
           "conversation_id": step.conversation_id if hasattr(step, "conversation_id") else None
       }
   )
   ```

4. Modified MusicControllerService._handle_play_request to detect source based on conversation_id:
   ```python
   # In MusicControllerService._handle_play_request:
   source = "voice" if payload.conversation_id else "cli"
   await self._smart_play_track(song_query, source=source)
   ```

**Impact**:
- CLI commands (`play music X`) now play tracks with no intro speech
- Voice commands ("Play me some funky music") generate track intros as before
- Track intro generation follows the Three-Call GPT pattern only for voice requests
- Audio ducking works correctly for all flows

**Architecture Improvements**:
- Better context awareness with source tracking in events
- Clear separation between CLI and voice interaction flows
- Maintained all existing functionality with more precision
- Improved user experience by avoiding unwanted speech

**Notes**: This change adheres to the principle that the system should respond based on the context of the request, treating CLI and voice paths differently where appropriate while maintaining a unified execution model.

## 🔧 Enhanced BrainService Track Selection Algorithm (#18)

**Issue Summary**: The BrainService's track selection logic had limited functionality that caused it to repeatedly play the same track ("Elem Zadowz - Huttuk Cheeka") regardless of different user queries.

**Root Cause Analysis**:
1. **Hardcoded Limited Track List**: BrainService was only aware of 5 hardcoded tracks while the system actually had 22 tracks available
2. **Limited Keyword Mapping**: Only a few keywords had defined mappings (e.g., "funky" mapped to "Elem Zadowz - Huttuk Cheeka")
3. **Default Fallback Behavior**: When no match was found, it always played the first track, which was "Elem Zadowz - Huttuk Cheeka"
4. **No Randomization**: The selection algorithm had no variation or randomization

**Changes Implemented**:
1. **Dynamic Track List Loading**: BrainService now fetches the real list of tracks from MusicControllerService
   ```python
   async def _fetch_available_tracks(self) -> None:
       # Request track list via MUSIC_COMMAND event
       track_list_payload = {"command": "list", "subcommand": None, "args": [], "raw_input": "list music"}
       await self.emit(EventTopics.MUSIC_COMMAND, track_list_payload)
       # ... tracks parsed from response ...
   ```

2. **Enhanced Keyword Mappings**: Added comprehensive keyword mappings for:
   - Artist names (e.g., "gaya", "mus kat", "zano")
   - Song title keywords (e.g., "utinni", "cheeka", "bukee")
   - Music styles/moods (e.g., "funky", "upbeat", "groovy", "slow", "electronic")
   ```python
   keyword_mapping = {
       # Excerpt of the expanded mappings:
       "funky": ["Elem Zadowz - Huttuk Cheeka", "Batuu Boogie", "Mus Kat & Nalpak - Turbulence"],
       "upbeat": ["Batuu Boogie", "Mus Kat & Nalpak - Bright Suns", "Duro Droids - Beep Boop Bop"],
       "groovy": ["Batuu Boogie", "Elem Zadowz - Huttuk Cheeka", "Vee Gooda, Ryco - Aloogahoo"],
       # ... many more mappings ...
   }
   ```

3. **Randomized Selection**: Added randomization to avoid playing the same tracks:
   ```python
   # Choose a random track from our options
   import random
   selected_track = random.choice(selection_pool)
   ```

4. **Recently Played Tracking**: Added tracking of recently played tracks to avoid repetition:
   ```python
   # Filter out recently played tracks if possible
   fresh_matches = [track for track in potential_matches if track not in self._recently_played_tracks]
   ```

**Impact**:
- Varied music selections for the same or similar music requests
- Better matching of music to user requests based on expanded keyword mappings
- Elimination of the issue where the same track would play repeatedly
- More natural DJ-like behavior with track variety and avoidance of repetition

**Next Steps**:
1. Implement feedback collection about user preferences to refine track selection
2. Add error handling for cases where MusicControllerService is unavailable
3. Consider implementing a proper track metadata system with genre tags for better matching

### 🔧 Track Synchronization Issue Fix
**Issue Identified**: The BrainService was consistently playing the same song even when finding different matches based on user queries.

**Root Cause**: 
1. BrainService was tracking tracks by name only using a simple list (`self._available_tracks = []`)
2. MusicControllerService was using a Pydantic MusicTrack model with full path information
3. When BrainService selected a track, it sent only the name to MusicControllerService
4. MusicControllerService's fuzzy matching sometimes found the wrong track with a similar name
5. No shared data model existed between services to ensure consistent track identification

**Fix Applied**:
1. Created a shared Pydantic model package in `cantina_os/models/`
2. Implemented shared `MusicTrack` and `MusicLibrary` models with proper validation
3. Updated MusicControllerService to use absolute file paths for reliable track identification
4. Modified BrainService to use the shared MusicLibrary model for track management
5. Added a new `MUSIC_LIBRARY_UPDATED` event topic for synchronization
6. Enhanced MusicControllerService to emit complete track metadata during library initialization
7. Updated BrainService to consume the track library updates

**Impact**: 
- BrainService and MusicControllerService now share the same track data representation
- Track selection is consistent across services
- Exact file paths are used for playback, eliminating mismatches
- The system can now properly play different tracks based on search criteria

**Lesson Learned**:
- Services that share data models should use common Pydantic schemas
- Track identification should use unique identifiers (full paths) rather than display names
- Event-based synchronization ensures data consistency across services
- Proper data serialization/deserialization is essential for reliable service communication

