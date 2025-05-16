# DJ Mode Implementation Plan

## 🎯 Concept Overview
DJ R3X will function as an autonomous DJ that:
- Plays through music tracks automatically with intelligent sequencing
- Provides DJ-style introductions for upcoming tracks
- Creates smooth crossfades between songs
- Continues operating in the background without user input

## 🏗️ Architecture Changes

### New Components
1. **CachedSpeechService**
   - Pre-renders speech for precise timing control
   - Provides exact speech duration information
   - Enables synchronized music/voice transitions
   - Implements lookahead caching for transitions (current + next track)

### Modified Components
1. **MusicControllerService**
   - Add crossfade capability between tracks
   - Support pre-loading of next track
   - Expose track metadata for better transitions
   - Add methods to get precise track progress/remaining time
   - Emit TRACK_ENDING_SOON events when track nears completion

2. **BrainService**
   - Generate plans for DJ mode transitions (just-in-time planning)
   - Implement intelligent track sequencing algorithms
   - Generate track-specific transition commentary
   - Handle DJ CLI commands (next, stop, etc.)
   - Create transition plans for skip commands

3. **TimelineExecutorService**
   - Support coordinated transitions between tracks
   - Handle speech/music synchronization with precise timing
   - Manage layer transitions during crossfades

4. **MemoryService**
   - Store DJ mode state (active/inactive)
   - Track current playlist and play history
   - Remember user preferences and skip patterns
   - Maintain lookahead cache state

## 📝 Implementation Checklist

### Phase 1: Core Infrastructure (Week 1)
- [ ] Create CachedSpeechService
  - [ ] Speech pre-generation functionality
  - [ ] Implement lookahead caching system (current + skip track)
  - [ ] Duration calculation and metadata
  - [ ] Playback with precise timing
  - [ ] Thread-safe implementation per audio standards

- [ ] Add crossfade capabilities to MusicControllerService
  - [ ] Implement volume ramping for fade-out
  - [ ] Add secondary track player for fade-in
  - [ ] Create crossfade timing controls
  - [ ] Support track pre-loading
  - [ ] Add track progress/completion detection

- [ ] Add DJ Mode CLI commands
  - [ ] `dj start` - Begin DJ mode
  - [ ] `dj stop` - Exit DJ mode
  - [ ] `dj next` - Force next track
  - [ ] `dj queue <track>` - Queue specific track

### Phase 2: DJ Intelligence (Week 2)
- [ ] Enhance BrainService for DJ mode planning
  - [ ] Event handler for TRACK_ENDING_SOON
  - [ ] Just-in-time plan generation for transitions
  - [ ] CLI command handling for DJ controls
  - [ ] Track sequencing algorithm with genre/energy matching
  - [ ] Interaction with MemoryService for state tracking

- [ ] Implement MemoryService enhancements
  - [ ] DJ mode state persistence
  - [ ] Track history tracking for repetition avoidance
  - [ ] User preference storage
  - [ ] Lookahead cache state management

- [ ] Create DJ commentary generator
  - [ ] Track-specific intros based on metadata
  - [ ] Transition commentary between genres
  - [ ] Special commentary for mood shifts
  - [ ] Time-of-day appropriate remarks

### Phase 3: Timeline Integration (Week 3)
- [ ] Enhance TimelineExecutorService for DJ mode
  - [ ] Coordinated layer management during transitions
  - [ ] Precise timing for crossfades and speech
  - [ ] Dynamic plan handling for DJ mode transitions
  - [ ] Ambient layer management with music

- [ ] Implement event system for DJ mode
  - [ ] `DJ_MODE_CHANGED` event
  - [ ] `TRACK_ENDING_SOON` event (30 seconds before end)
  - [ ] `CROSSFADE_STARTED` event
  - [ ] `DJ_COMMENTARY_NEEDED` event
  - [ ] `DJ_NEXT_TRACK` event (for CLI skip command)

### Phase 4: Testing & Refinement (Week 4)
- [ ] Create test suite for DJ mode
  - [ ] Automated tests for crossfades
  - [ ] Timing verification for speech/music sync
  - [ ] Long-running stability tests
  - [ ] Test for CLI command handling

- [ ] Performance optimization
  - [ ] Cache management efficiency
  - [ ] Memory usage during extended sessions
  - [ ] CPU load during crossfades

## 🔄 Event Flow for DJ Mode

```
[DJ Mode Active]
        |
        v
[Current Track Playing]
        |
        v
[TRACK_ENDING_SOON] (30 seconds before end)
        |
        v
[BrainService selects next track]
        |
        v
[Generate transition commentary with CachedSpeechService]
        |
        v
[Cache next transition for potential skip commands]
        |
        v
[Pre-load next track]
        |
        v
[Start crossfade]
        |
        v
[Play DJ transition commentary during crossfade]
        |
        v
[Complete transition to new track]
        |
        v
[Return to ambient DJ state]
```

## 🔄 CLI Skip Command Flow

```
[DJ_NEXT command received]
        |
        v
[Check if next transition is cached]
        |
        ├── [Yes] Use cached transition
        |         |
        |         v
        |     [Execute skip plan]
        |
        └── [No] Generate transition on-demand
                |
                v
            [Execute skip plan]
```

## 🚩 Expected Challenges
1. **Speech Timing Precision**: Ensuring DJ commentary aligns perfectly with musical transitions
2. **Resource Management**: Handling memory usage with pre-cached audio files
3. **Musical Flow**: Creating algorithms that select tracks with complementary musical qualities
4. **Stability**: Ensuring the system can run for hours without degradation
5. **Content Variety**: Preventing repetitive DJ commentary over extended sessions
6. **Lookahead Cache Management**: Balancing responsiveness with resource usage

## 🔧 Technical Requirements
- ElevenLabs API direct integration (non-streaming)
- Audio buffer and track analysis capability
- Thread-safe cache management
- Event-driven architecture compatible with existing timeline services
- Precise audio timing for crossfades

This implementation plan leverages our existing layered timeline architecture by using BrainService as the primary plan generator, MemoryService for state tracking, and TimelineExecutorService for coordinated execution. The lookahead caching approach balances responsiveness with resource efficiency. 