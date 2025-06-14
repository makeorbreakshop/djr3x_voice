# DJ Tab Rewrite - Simple Radio Station Approach

## Overview

Simplified rewrite of the DJ Tab component to create a radio station-style interface with just the essential controls. Focus on clear visual indicators and real-time monitoring of the automated DJ process.

## Current Issues

- ❌ Sending invalid fields (`interval`, `crossfade_duration`) causing Pydantic validation failures
- ❌ Over-engineered interface with settings that don't exist in backend
- ❌ Missing visual indicators for automated process status

## Core Functionality

**Three Simple Commands:**
1. **DJ Start** - Begin automated radio mode
2. **DJ Stop** - End radio mode
3. **DJ Next** - Skip current song and play next transition

**Mental Model**: Like a radio station control board - minimal controls, clear status indicators

## Implementation Plan

### Phase 1: Core Controls & Visual Status (MVP)

- [ ] **1.1 Three Button Control Panel**
  - [ ] Start button - Sends `{action: "start"}` 
  - [ ] Stop button - Sends `{action: "stop"}`
  - [ ] Next button - Sends `{action: "next"}`
  - [ ] Visual state for each button (enabled/disabled/active)

- [ ] **1.2 Live Status Indicators**
  - [ ] DJ Mode Status: OFF/ON with live indicator (●)
  - [ ] Current Status: "Playing", "Transitioning", "Preparing Next", etc.
  - [ ] Subscribe to `DJ_MODE_CHANGED` events
  - [ ] Visual feedback for button presses

### Phase 2: Radio Station Monitoring

- [ ] **2.1 Current & Next Track Display**
  - [ ] NOW PLAYING: Current track info with progress bar
  - [ ] UP NEXT: Next track queued indicator
  - [ ] Visual indicator when next track is loaded and ready
  - [ ] Subscribe to `MUSIC_PLAYBACK_STARTED` events

- [ ] **2.2 Voice Track Status**
  - [ ] Voice Track Status: "Ready" / "Generating" / "Not Ready"
  - [ ] Visual indicator (green checkmark when ready)
  - [ ] Show when AI commentary is prepared
  - [ ] Subscribe to relevant LLM/voice generation events

- [ ] **2.3 Process Flow Visualization**
  - [ ] Simple flow diagram or status lights showing:
    - Current track playing ✓
    - Next track queued ✓
    - Voice track ready ✓
    - Transition scheduled ✓

### Phase 3: Polish & Error Handling (Optional)

- [ ] **3.1 Error States**
  - [ ] Connection lost indicator
  - [ ] Command failed feedback
  - [ ] Service unavailable warnings

- [ ] **3.2 Session Info**
  - [ ] Simple "On Air" timer (how long DJ mode active)
  - [ ] Track count for current session

## Technical Details

### Pydantic Compliance
```typescript
// Only these three commands needed
const startCommand = { action: 'start' }
const stopCommand = { action: 'stop' }
const nextCommand = { action: 'next' }
```

### Essential Event Subscriptions
```typescript
// Minimal event subscriptions
socket.on('dj_status', handleDJStatusChange)        // ON/OFF state
socket.on('music_status', handleMusicStatus)        // Track info
socket.on('voice_status', handleVoiceTrackStatus)   // Commentary ready
```

### UI Layout Concept
```
┌─────────────────────────────────────────────┐
│ DJ MODE: ● ON                               │
├─────────────────────────────────────────────┤
│                                             │
│  [START DJ]  [STOP DJ]  [→ NEXT TRACK]     │
│                                             │
├─────────────────────────────────────────────┤
│ NOW PLAYING: Cantina Band - Figrin D'an    │
│ ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░  2:15 / 3:30         │
│                                             │
│ UP NEXT: ✓ Track loaded                     │
│ VOICE TRACK: ✓ Ready                        │
│                                             │
│ Status: Playing → Voice track in 15s        │
└─────────────────────────────────────────────┘
```

## Success Criteria

1. **Three working buttons** that send valid Pydantic-compliant commands
2. **Clear visual indicators** for DJ mode state and process status
3. **Real-time monitoring** of next track and voice track readiness
4. **No validation errors** - only send supported fields
5. **Radio station feel** - simple, professional, status-focused

## Implementation Notes

- **Remove all complex settings** - no intervals, crossfades, or timing controls
- **Focus on status visualization** - users want to see what's happening
- **Event-driven updates** - all state from CantinaOS events
- **Fail gracefully** - show clear error states when things go wrong
- **Keep it simple** - resist adding features beyond core requirements

---

**Created**: 2025-06-13  
**Updated**: 2025-06-14  
**Status**: Simplified  
**Estimated Effort**: 1 day  
**Priority**: Core functionality only