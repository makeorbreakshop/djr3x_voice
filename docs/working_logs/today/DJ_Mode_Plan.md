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

2. **DJ Mode Controller**
   - Manages the autonomous DJ playlist
   - Handles crossfades and track sequencing
   - Generates contextual DJ patter between tracks

### Modified Components
1. **MusicControllerService**
   - Add crossfade capability between tracks
   - Support pre-loading of next track
   - Expose track metadata for better transitions

2. **BrainService**
   - Add DJ mode state tracking
   - Implement intelligent track sequencing algorithms
   - Generate track-specific transition commentary

3. **TimelineExecutorService**
   - Support coordinated transitions between tracks
   - Handle speech/music synchronization with precise timing
   - Manage layer transitions during crossfades

## 📝 Implementation Checklist

### Phase 1: Core Infrastructure (Week 1)
- [ ] Create CachedSpeechService
  - [ ] Speech pre-generation functionality
  - [ ] Cache management system
  - [ ] Duration calculation and metadata
  - [ ] Playback with precise timing

- [ ] Add crossfade capabilities to MusicControllerService
  - [ ] Implement volume ramping for fade-out
  - [ ] Add secondary track player for fade-in
  - [ ] Create crossfade timing controls
  - [ ] Support track pre-loading

- [ ] Add DJ Mode CLI commands
  - [ ] `dj start` - Begin DJ mode
  - [ ] `dj stop` - Exit DJ mode
  - [ ] `dj next` - Force next track
  - [ ] `dj queue <track>` - Queue specific track

### Phase 2: DJ Intelligence (Week 2)
- [ ] Implement track sequencing algorithm in BrainService
  - [ ] Genre-based progression logic
  - [ ] Energy/tempo matching between tracks
  - [ ] Variety controls to prevent repetition
  - [ ] User preference learning

- [ ] Create DJ commentary generator
  - [ ] Track-specific intros based on metadata
  - [ ] Transition commentary between genres
  - [ ] Special commentary for mood shifts
  - [ ] Time-of-day appropriate remarks

### Phase 3: Timeline Integration (Week 3)
- [ ] Enhance TimelineExecutorService for DJ mode
  - [ ] Coordinated layer management during transitions
  - [ ] Precise timing for crossfades and speech
  - [ ] Ambient layer management with music

- [ ] Implement event system for DJ mode
  - [ ] `DJ_MODE_CHANGED` event
  - [ ] `TRACK_ENDING_SOON` event (30 seconds before end)
  - [ ] `CROSSFADE_STARTED` event
  - [ ] `DJ_COMMENTARY_NEEDED` event

### Phase 4: Testing & Refinement (Week 4)
- [ ] Create test suite for DJ mode
  - [ ] Automated tests for crossfades
  - [ ] Timing verification for speech/music sync
  - [ ] Long-running stability tests

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

## 🚩 Expected Challenges
1. **Speech Timing Precision**: Ensuring DJ commentary aligns perfectly with musical transitions
2. **Resource Management**: Handling memory usage with pre-cached audio files
3. **Musical Flow**: Creating algorithms that select tracks with complementary musical qualities
4. **Stability**: Ensuring the system can run for hours without degradation
5. **Content Variety**: Preventing repetitive DJ commentary over extended sessions

## 🔧 Technical Requirements
- ElevenLabs API direct integration (non-streaming)
- Audio buffer and track analysis capability
- Thread-safe cache management
- Event-driven architecture compatible with existing timeline services

This implementation plan adheres to our layered timeline architecture while extending it to support continuous autonomous operation in DJ mode. 