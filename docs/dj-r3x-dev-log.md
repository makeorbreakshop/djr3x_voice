# DJ R3X Voice App — Dev Log (Engineering Journal)

## 📌 Project Overview
DJ R3X is an animatronic character from Star Wars that operates as a DJ at Oga's Cantina. This project aims to recreate the voice and animation features of DJ R3X, allowing for interactive conversations with realistic voice synthesis and synchronized LED animations.

The application is built with an event-driven architecture with the following core components:
- **Event Bus (EB)** - Central communication hub for all components
- **Voice Manager (VM)** - Handles speech recognition, AI processing, and voice synthesis
- **LED Manager (LM)** - Controls LED animations on eyes and mouth
- **Music Manager (MM)** - Manages background music and audio ducking
- **Holocron Knowledge (HK)** - Provides Star Wars canonical knowledge via RAG system

## 🏗 Architecture Evolution Log
| Date | Change | Reason |
|------|--------|--------|
| 2023-Initial | Established event-driven architecture | Enable loose coupling between components for independent development |
| 2023-Initial | Selected pyee.AsyncIOEventEmitter for Event Bus | Provides async event handling within Python's asyncio framework |
| 2023-Initial | Added voice processing pipeline (Whisper → GPT → ElevenLabs) | Provide high-quality speech recognition and synthesis for the character |
| 2023-Initial | Implemented LED control via Arduino Mega | Allows for synchronized mouth/eye animations |
| 2025-05-06 | Identified asyncio/thread bridge issue | Synchronous input (keyboard) needs proper bridge to async event bus |
| 2025-05-10 | Added Holocron Knowledge component | Provide enhanced Star Wars knowledge via RAG system |

## 🔎 Code Investigations & Discoveries
- Audio RMS envelope calculation effective for LED mouth synchronization (~50fps)
- Mouth animation latency must stay under 100ms for natural appearance
- Asyncio best practices critical for stability:
  - Tasks require explicit cancellation; boolean flags alone are insufficient
  - Blocking I/O (serial, audio) must use run_in_executor
  - Thread-based inputs (keyboard) need proper bridges via run_coroutine_threadsafe
  - Cross-thread communication requires explicit lifecycle management
- Arduino communication needs robust error handling, retries, and state reconciliation
- Components require explicit cleanup for mode transitions, not just application shutdown

## 🐞 Known Bugs / Technical Limitations
- Voice detection latency varies with microphone quality and ambient noise
- Arduino serial communication introduces slight delay in LED animations
- Audio ducking creates occasionally noticeable transitions in background music
- Audio playback via sounddevice fails silently on some platforms
- Cross-thread communication requires careful event loop reference management

## 💡 Feature Backlog & Design Notes
- Move LED Manager to ESP32 for wireless control
- Add servo control for physical movements
- Implement wake word detection for hands-free operation
- Add beat detection for music-synchronized animations
- Create web dashboard for configuration and monitoring
- Consider migrating from in-process Event Bus to MQTT for distributed architecture
- Implement Holocron Knowledge System (IN PROGRESS):
  - Star Wars knowledge retrieval via RAG with Supabase pgvector
  - In-character delivery ("consulting the holocron")
  - Hybrid activation (explicit questions + relevant responses)

## 🔗 References & Resources
- [Lights & Voice MVP](./lights-voice-mvp.md) - Technical implementation details and event flow
- [Requirements](./requirements.txt) - Python dependencies

## 🕒 Log Entries (Chronological)

### 2025-05-05: Initial Architecture Design
- Established event-driven architecture with Event Bus as central communication hub
- Defined core components: Voice Manager, LED Manager, Music Manager
- Created standard event types and payloads for cross-component communication
- Planned file structure for organized code development

### 2025-05-05: Voice Processing Pipeline Implementation
- Integrated Whisper for speech recognition
- Connected OpenAI GPT for conversational AI
- Implemented ElevenLabs for character voice synthesis
- Added audio level analysis for mouth animation synchronization

### 2025-05-05D: LED Animation System
- Established serial communication protocol with Arduino
- Created animation patterns for idle, listening, processing, and speaking states
- Implemented mouth movement synchronized with speech audio levels
- Added error recovery for connection issues

### 2025-05-05: Music Manager Development
- Implemented background music playback using python-vlc
- Added volume ducking during speech with smooth transitions
- Created event listeners for voice.speaking_started and voice.speaking_finished 

### 2025-05-06: Event Bus Architecture Investigation
- Investigated error: "RuntimeError: no running event loop" when using push-to-talk mode
- Confirmed event bus architecture is the right approach for coordinating multiple hardware components
- Discovered key issue: synchronous keyboard listener (pynput) cannot directly interact with asyncio event loop
- Need to implement proper "bridge" between synchronous threads and asyncio using thread-safe methods
- Architecture insight: The event bus design is sound for our multi-component system, but requires careful handling of cross-thread communication
- Approach: Use asyncio.run_coroutine_threadsafe() or loop.call_soon_threadsafe() to properly schedule events from synchronous contexts

### 2025-05-06: Event Bus Async Handler Fix
- Enhanced EventBus to properly handle mixed sync/async event handlers with task gathering
- Fixed LED animation timing issues by properly awaiting async event handlers 

### 2025-05-06: LED Configuration Update
- Centralized LED settings in `app_settings.py` with macOS-compatible defaults 

### 2025-05-06: Push-to-Talk Event Loop Reference Fix
- Fixed critical error: "Task got Future <_GatheringFuture pending> attached to a different loop"
- Root cause: Event loop reference mismatch between initialization and execution phases
- Problem detail: VoiceManager captures event loop with `asyncio.get_event_loop()` during init, but `asyncio.run()` in main.py creates a new loop
- When keyboard listener thread uses `run_coroutine_threadsafe()`, it references wrong loop
- Solution: Pass the running event loop explicitly from main.py to VoiceManager, ensuring consistent loop references
- Learning: When using asyncio with threaded callbacks, always capture the running loop explicitly with `asyncio.get_running_loop()` and pass it to components requiring cross-thread communication 

### 2025-05-06: Voice Interaction Pipeline Investigation
- Fixed OpenAI integration by storing model name directly in VoiceManager instance instead of config dictionary
- Fixed ElevenLabs integration by properly using VoiceConfig.to_dict() method for API calls
- Identified and resolved audio playback issues on macOS:
  - Verified ElevenLabs audio generation works (90KB+ files generated)
  - Added fallback from sounddevice to system audio commands (afplay/aplay)
  - Enhanced logging throughout voice pipeline for better debugging
- Added platform-specific audio playback support for macOS, Linux, and Windows 

### 2025-05-06: Added Startup Sound
- Added platform-compatible startup sound playback after component initialization for better UX feedback 

### 2025-05-06: LED Communication Protocol Update
- Implemented ArduinoJson v7.4.1 for robust JSON parsing
- Updated Arduino sketch with dynamic JSON allocation and proper error handling
- Added structured acknowledgments for reliable communication
- Next: Test communication reliability with voice state changes 

### 2025-05-06: LED JSON Communication Fix
- Fixed JSON communication between Python and Arduino
- Updated LED Manager to handle multiple response types (debug, parsed, ack)
- Added timeout protection and better error handling
- Reduced Arduino debug output with DEBUG_MODE flag
- Result: Eliminated "Invalid JSON" and "Unexpected acknowledgment" warnings 

### 2025-05-06: System Modes Architecture Design
- Implemented system mode architecture to fix debugging issues and improve interaction:
  - **Modes**: STARTUP → IDLE → AMBIENT SHOW/INTERACTIVE VOICE
- Key benefits: Explicit opt-in to voice interaction, state-based behavior control
- Implementation: Command input thread with asyncio bridge, EventBus for mode transitions
- Components respond to mode changes via event subscriptions

### 2025-05-06: System Modes Architecture Refinement
- Added distinct IDLE mode as default fallback state
- System boot sequence: STARTUP → IDLE (can transition to AMBIENT or INTERACTIVE)
- Commands: `ambient`, `engage`, `disengage` (returns to IDLE)
- Improved LED patterns for each mode and fixed command input display

### 2025-05-06: Voice Interaction and LED State Management Updates
- Fixed VoiceManager interaction loop for speech synthesis/playback
- Known Issue: LED transitions during pattern interruption need improvement 

### 2025-05-06: Music Playback System Design
- Implemented CLI music controls: `list music`, `play music <number/name>`, `stop music`
- Mode-specific behaviors:
  - IDLE: Limited controls; playing transitions to AMBIENT
  - AMBIENT: Full controls, continuous playback
  - INTERACTIVE: Full controls with audio ducking during speech
- Architecture: MusicManager listens for control commands and mode changes
- Next steps: CLI implementation, testing, voice command integration

### 2025-05-06: Asyncio State Transition Fix
- Fixed: Voice/LED persisting after mode changes; Arduino timeouts
- Solutions: Task cancellation, non-blocking I/O, resource cleanup
- Result: Clean transitions between system modes 

### 2025-05-06: Added System Reset Command
- Added `reset` CLI command for emergency system recovery
- Implementation:
  - Cancels all active tasks (voice, LED, music)
  - Cleans up hardware connections (Arduino, audio)
  - Forces transition to IDLE mode
  - Re-initializes core managers if needed
- Benefit: Quick recovery from stuck states without full restart 

### 2025-05-07: Holocron Knowledge Base Implementation
- **Data Collection**: 1,505 articles (531 canon, 836 legends, 134 unknown)
- **Pipeline**: 
  - Switched to MediaWiki API (60 req/min, 10 workers)
  - Optimized batch processing with progress tracking
  - 98% processing success rate
- **Next**: Full knowledge base population (4-5 hours)

### 2025-05-07: Wookieepedia Content Analysis
- **Stats**: 209,668 total (Canon: 49,286, Legends: 116,014, Other: 44,368)
- **Strategy**: Focus on Canon content (~49K articles)
- **Technical**: Implemented API pagination, rate limiting (1 req/sec)

### 2025-05-07: Canon Content Processing Strategy
- **Scale**: 49,286 articles, ~45 URLs/minute
- **Process**:
  1. Store URLs with priority flags
  2. Process in 10 batches (5,000 each)
  3. Priority order: Galaxy's Edge → Droids → Entertainment → General
- **Monitoring**: Real-time tracking, automatic recovery, API rate monitoring

### 2025-05-07: Canon URL Collection System
- **Priority System**:
  - Added weighted scoring for term matches
  - Enhanced categorization (Galaxy's Edge, Droids, Entertainment)
  - Improved metadata tracking
- **Technical**: Configurable thresholds, subcategory tracking

### 2025-05-07: Fixed Canon URL Collection Issues
- **Bug**: KeyError in subcategories, URLs not saving to Supabase
- **Fix**: 
  - Corrected priority levels to match database schema
  - Fixed batch processing and storage
  - Removed redundant processing calls
- **Result**: Successful URL collection with proper categorization

### 2025-05-07: Optimized Canon URL Collection
- **Issue**: Slow URL processing and database writes
- **Solution**: 
  - Batch size: 1000 URLs
  - Concurrent processing: 5 workers, 10 max requests
  - Transaction support for data consistency
- **Result**: Significant performance improvement with reliable processing

### 2025-05-07: Fixed Canon URL Collection Subcategories Bug
- **Issue:** KeyError in subcategories tracking during URL categorization
- **Root Cause:** Subcategories dictionary initialized with category types instead of priority levels
- **Fix:**
  ```python
  self.subcategories = {
      "high": set(),
      "medium-high": set(),
      "medium": set(),
      "medium-low": set(),
      "low": set()
  }
  ```
- **Additional Improvements:**
  - Fixed metadata storage to use priority levels as keys
  - Aligned subcategory tracking with priority-based organization
  - Ensures consistent categorization across all priority levels
  - Optimized dry-run mode to skip API calls, using URL-based categorization only
  - Removed overly conservative rate limiting (MediaWiki allows 50 req/sec)

### 2025-05-07: Adjusted Canon URL Priority Scoring
- **Initial Distribution Analysis:**
  - High Priority: 0.5% (244 articles)
  - Medium Priority: 0.9% (438 articles)
  - Low Priority: 98.6% (48,604 articles)
  - Medium-high and Medium-low: 0%

- **Scoring Adjustments:**
  - Increased weights for droid and entertainment terms (3.0 → 4.0)
  - Increased related content weight (1.0 → 2.0)
  - Adjusted category match weights upward
  - Lowered priority thresholds to better distribute content:
    ```python
    PRIORITY_THRESHOLDS = {
        "high": 4.5,         # Unchanged
        "medium-high": 3.0,  # Was 3.5
        "medium": 2.0,       # Was 2.5
        "medium-low": 1.0,   # Was 1.5
        "low": 0.0          # Unchanged
    }
    ```

- **Expected Improvements:**
  - Better distribution across priority levels
  - More droid and entertainment content in medium-high category
  - More related content in medium and medium-low categories
  - Reduced number of low-priority articles

### 2025-05-07: Optimized Canon URL Collection Process
- **Performance Bottleneck Resolution:**
  - Identified slow URL categorization and database writes
  - Previous: Sequential processing of 49,286 URLs
  - Solution: Implemented batch processing and parallel execution

- **Technical Improvements:**
  1. **Batch Processing:**
     - Added `BATCH_SIZE = 1000` for efficient database operations
     - Implemented URL batching for both categorization and storage
     - Enhanced progress tracking with time estimates
  
  2. **Parallel Processing:**
     - Added concurrent URL processing with `MAX_WORKERS = 5`
     - Implemented request rate limiting with `MAX_CONCURRENT_REQUESTS = 10`
     - Used `asyncio.Semaphore` for API request throttling
  
  3. **Progress Monitoring:**
     - Enhanced progress bars with completion percentage
     - Added time remaining estimates
     - Improved error handling and reporting
  
  4. **Database Optimization:**
     - Implemented batch database writes
     - Added transaction support for better data consistency
     - Enhanced error recovery for failed batches

- **Results:**
  - Significant reduction in processing time
  - More efficient resource utilization
  - Better visibility into progress and errors
  - Improved reliability with proper rate limiting

- **Next Steps:**
  1. Monitor batch size impact on performance
  2. Consider implementing retry mechanism for failed batches
  3. Add checkpointing for long-running processes
  4. Implement parallel content processing pipeline

### 2025-05-07: Fixed Canon URL Storage Issue
- **Issue Identified**: URLs not being saved to Supabase during batch collection
- **Root Cause Analysis**:
  1. Double processing in `collect_canon_urls.py`
  2. `process_all_urls()` called twice: once in main loop and again in `store_urls()`
  3. Second call overwrites results from first call
  4. Results in empty batches being sent to Supabase

- **Technical Details**:
  ```python
  # Original problematic flow:
  await collector.process_all_urls()  # First call in main()
  await store_urls(collector, dry_run=args.dry_run)
    # which calls process_all_urls() again internally
  ```

- **Fix Applied**:
  1. Removed redundant `process_all_urls()` call from main()
  2. Enhanced error handling in `store_batch()` to raise exceptions
  3. Added detailed logging for batch storage operations
  4. Added result verification after batch storage

### 2025-05-07: Fixed URL Priority Enum Error
- **Issue Identified**: Full URL collection failing with database enum errors
- **Root Cause Analysis**:
  1. Schema constraint in database requires specific enum values for `priority` field
  2. Script was using "medium-high" and "medium-low" values that don't exist in the database schema
  3. This caused batch inserts to fail with error: `invalid input value for enum url_priority: "medium-high"`

- **Technical Details**:
  ```
  ERROR: Error processing batch: Database error during batch storage: 
  {'code': '22P02', 'details': None, 'hint': None, 
  'message': 'invalid input value for enum url_priority: "medium-high"'}
  ```

- **Fix Applied**:
  1. Simplified priority levels to match database schema: "high", "medium", "low"
  2. Updated `PRIORITY_THRESHOLDS` in the code
  3. Modified the `determine_priority()` method to only return valid enum values
  4. Tested with small batches first, then full collection

- **Results**:
  1. Batch processing successfully stores URLs in database
  2. No more enum errors with priority values
  3. Full collection process working properly

### 2025-05-07: Holocron Knowledge System Vector Overlap Analysis
- **Investigation of Vector Overlap Mechanisms**:
  - Confirmed intentional embedding overlap in chunking strategy
  - Identified key overlap points:
    1. OVERLAP_TOKENS = 100: Creates a 100-token overlap between adjacent chunks
    2. Section headers & titles duplicated across related chunks to maintain context
    3. Metadata embedding (article title, source URL) across multiple chunks
  
- **Benefits of Vector Overlap**:
  - Maintains semantic continuity between chunks
  - Prevents important context loss at chunk boundaries
  - Improves retrieval quality for concepts spanning multiple segments
  - Enhances the RAG system's ability to find relevant information

- **Test Script Development**:
  - Created `scripts/simple_holocron_chat.py` for testing vector retrievals
  - Extended `holocron/knowledge/retriever.py` to support modern Supabase client
  - Updated OpenAI client usage to be compatible with SDK v1.0+
  - Isolated vector search from other dependencies for better testing

- **Technical Challenges**:
  - OpenAI SDK version compatibility issues (v0.x vs v1.0+)
  - SQL query format for pgvector semantic search
  - Supabase client initialization parameters
  - Cross-thread async execution with proper cancellation

- **Query Processing Flow**:
  1. Generate embedding for search query
  2. Execute vector similarity search against pgvector database
  3. Format retrieved contexts for prompt injection
  4. Generate DJ R3X character response using retrieved knowledge

- **Next Steps**:
  1. Benchmark semantic retrieval quality with different overlap settings
  2. Investigate performance impact of increased vector dimensions
  3. Consider hybrid retrieval strategies (keyword + semantic)
  4. Expand test suite with canonical query examples

### 2025-05-08: Vector Search Evolution and Migration
- **Initial Implementation**: Integrated Supabase with pgvector for Holocron knowledge retrieval
  - Used HNSW indexing with parameters m=16, ef_construction=64
  - Implemented tiered fallback system for reliability (RPC → SQL → Basic)
  - Added metadata filtering for targeted knowledge retrieval

- **Key Challenges**:
  - **Client API Compatibility**: Fixed multiple issues with Supabase client API changes and inconsistent method patterns
  - **Connection Management**: Implemented client factory with connection pooling for reliability
  - **Vector Type Handling**: Resolved vector dimension issues (1536D) and embedding format compatibility
  - **Query Performance**: Encountered timeout issues at scale with larger knowledge base
  - **Scaling Limitations**: As content grew (50k Wookieepedia URLs → ~200-300k vectors), hitting pgvector performance limits

- **Progressive Optimizations**:
  - Simplified search architecture to two-tier approach (Primary: RPC, Fallback: LLM)
  - Optimized RPC function for better vector handling and similarity calculations
  - Standardized error handling with graceful degradation
  - Improved monitoring and progress tracking for pipeline operations

- **Decision to Migrate to Qdrant (2025-05-08)**:
  - **Rationale**: Need for better scaling, performance, and control at our projected vector scale (200-300k)
  - **Implementation Plan**:
    - Export vectors + metadata from Supabase
    - Self-host Qdrant on cloud VM (Render, DigitalOcean, or Railway)
    - Replace SQL/RPC with Qdrant API/SDK while maintaining core workflow
  - **Expected Benefits**: Reduced costs, improved query performance, simpler scaling, full control over infrastructure

- **Core Learnings**:
  - Vector database selection should align with projected scale from the beginning
  - Purpose-built vector databases outperform general-purpose DB extensions at scale
  - Client API compatibility requires careful version management and abstraction
  - Self-hosting provides important control over performance tuning and cost management
  - Knowledge retrieval quality depends on both technical implementation and proper chunking strategy
  - **Supabase Client Patterns**:
    - Pin dependencies exactly (e.g., `supabase==2.3.5` not `>=2.3.4`) to prevent API drift
    - Use `ClientOptions` objects instead of dictionaries for client initialization
    - Properly handle synchronous vs. asynchronous client methods and avoid mixing patterns
    - Implement version detection to handle API changes gracefully
  - **Vector Search Optimization (pgvector)**:
    - HNSW index parameters significantly affect performance (m=16, ef_construction=64 works well for ~50k vectors)
    - Set appropriate `work_mem` (128MB) and `max_parallel_workers_per_gather` (2) for better performance
    - Embedding dimension standardization crucial (verify 1536D for text-embedding-ada-002)
    - Similarity threshold of 0.5 balances relevance with recall rate
  - **Pipeline Engineering**:
    - Batch processing (size 100-1000) dramatically improves throughput
    - Concurrent workers (5-10) with proper rate limiting essential for web scraping
    - Progress tracking with ETA calculations improves developer experience on long-running jobs
    - Error handling should include automatic retries with exponential backoff
    - URL processing requires careful handling of special characters (especially % symbols)
  - **Testing Vector Implementations**:
    - Mock fixtures need careful design to support both sync and async patterns
    - Test vectors must match production dimensions exactly
    - RPC function tests must validate similarity sorting and thresholds
    - Consider separating unit tests from integration tests for independent verification
    - Always test with realistic content volumes to catch scaling issues early

### 2025-05-08: Switched to GPT-4.1-mini for Holocron
- **Cost**: 80% reduction ($0.002/query vs $0.01/query)
- **Benefits**: Same context window (128K), faster responses
- **Changes**: 
  - Increased chunks (5 → 8) within budget
  - GPT-4o remains as fallback for complex queries



