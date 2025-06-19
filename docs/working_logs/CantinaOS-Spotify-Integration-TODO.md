# CantinaOS Spotify Integration - TODO

> **Status**: Architecture Issues - Service Integration Needed
> 
> **Branch**: `spotify` (commits: 809e03b, 4f1b2cf, 3653eb5)

## ⚠️ CRITICAL ISSUE IDENTIFIED

**Problem**: Duplicate event subscription violations causing audio conflicts. Multiple services subscribing to the same `MUSIC_COMMAND` events. This causes:
- Duplicate event processing (violates CantinaOS ARCHITECTURE_STANDARDS.md §1.3)
- Multiple MUSIC_PLAYBACK_STARTED events 
- Slow/dragged audio playback
- DJ mode failures

**Root Cause**: 
1. **MusicControllerService** subscribes to `MUSIC_COMMAND` events
2. **MusicSourceManagerService** subscribes to `MUSIC_COMMAND` events  
3. **Providers re-emit `MUSIC_COMMAND`** events creating feedback loops
4. **Both services process same commands** causing VLC conflicts

**Correct PRD Architecture**: MusicSourceManagerService → Providers → **Direct method calls** to MusicControllerService (no event re-emission)

## 📋 What's Done

✅ **Architecture Framework**:
- MusicSourceManagerService with provider system
- Local music provider implementation
- Spotify provider with OAuth framework
- Command integration and event system
- Comprehensive tests with mocking

✅ **Service Integration Fixed**:
- Dual event subscription conflicts resolved
- Architecture follows proper provider pattern
- Audio playback restored to normal speed

## 🚨 REQUIRED FIXES BEFORE API INTEGRATION

**Architecture Fixes (MUST BE COMPLETED FIRST):**

1. **Remove MusicControllerService MUSIC_COMMAND Subscription** ✅ **COMPLETED** (20 minutes)
   - [✅] Remove `EventTopics.MUSIC_COMMAND` subscription from MusicControllerService
   - [✅] Keep MusicControllerService as registered service (both services stay per ARCHITECTURE_STANDARDS.md)
   - [✅] Only MusicSourceManagerService should handle MUSIC_COMMAND events

2. **Fix Provider Event Re-emission** ✅ **COMPLETED** (30 minutes)
   - [✅] Update LocalMusicProvider to call MusicControllerService methods directly (no event emission)
   - [✅] Update SpotifyMusicProvider to call MusicControllerService methods directly (no event emission)  
   - [✅] Pass MusicControllerService instance to both providers via dependency injection
   - [✅] Remove `self.event_bus.emit(EventTopics.MUSIC_COMMAND, ...)` from both providers

3. **Update MusicSourceManagerService Integration** ✅ **COMPLETED** (25 minutes)
   - [✅] Pass MusicControllerService instance to providers during initialization
   - [✅] Remove event re-emission from `_handle_music_command` (line 597)
   - [✅] Route commands directly to providers using method calls
   - [✅] Maintain async patterns with direct service method calls

4. **Validate Single Event Flow** ✅ **COMPLETED** (15 minutes)
   - [✅] Test: MUSIC_COMMAND → MusicSourceManagerService → Provider → music_controller.method()
   - [✅] Verify only ONE MUSIC_PLAYBACK_STARTED event per command
   - [✅] Test both local and Spotify tracks use same VLC engine
   - [✅] Confirm normal audio playback speed and DJ mode functionality

**Expected Behavior After Fixes:**
- Single event subscription: Only MusicSourceManagerService handles MUSIC_COMMAND
- Provider delegation: Local/Spotify providers call MusicControllerService methods directly  
- Both track types: Local files and Spotify previews use same VLC playback engine
- Normal audio playback speed with no duplicate processing
- DJ mode functions correctly with single event flow

---

## 📚 SYSTEM DOCUMENTATION UPDATES (After Architecture Fixes)

**Update System Architecture Documentation (30 minutes):**

5. **Update Service Registry Table in CANTINA_OS_SYSTEM_ARCHITECTURE.md** (10 minutes)
   - [ ] Add MusicSourceManagerService entry to Service Registry Table (line 53)
   - [ ] Specify correct event subscriptions: MUSIC_COMMAND, SPOTIFY_COMMAND
   - [ ] Specify correct event publications: MUSIC_PROVIDER_CHANGED, SPOTIFY_STATUS_UPDATE
   - [ ] Remove MUSIC_COMMAND from MusicControllerService subscriptions

6. **Update Event Topology Section** (10 minutes)
   - [ ] Change MUSIC_COMMAND subscribers from MusicControllerService to MusicSourceManagerService (line 95)
   - [ ] Add new SPOTIFY_COMMAND event topic with proper routing
   - [ ] Update event flow descriptions to reflect provider orchestration

7. **Update Command Flow Diagrams** (10 minutes)
   - [ ] Update Unified Command Processing Flow (lines 208-219) to show provider layer
   - [ ] Add diagram: CLI → CommandDispatcher → MusicSourceManagerService → Provider → MusicControllerService
   - [ ] Document provider selection and fallback mechanisms

8. **Add Provider Architecture Pattern Section** (Optional - 15 minutes)
   - [ ] Document the orchestrator pattern with dependency injection
   - [ ] Explain provider registration and lifecycle management
   - [ ] Document fallback and health monitoring patterns
   - [ ] Add integration examples for new providers

**Current Documentation Issues Found:**
- MusicSourceManagerService completely missing from Service Registry Table
- Event topology shows MusicControllerService as direct MUSIC_COMMAND subscriber (incorrect)
- Command flow diagrams missing provider orchestration layer
- No documentation of provider pattern architecture

---

## 🔧 SPOTIFY API INTEGRATION (After Architecture Fixes)

**API Setup Steps:**

1. **Get Spotify API Credentials** (15 minutes)
   - [✅] Create app at https://developer.spotify.com/dashboard
   - [✅] Set redirect URI: `http://127.0.0.1:8080/callback`
   - [✅] Add credentials to `.env`:
     ```bash
     SPOTIFY_CLIENT_ID=your_client_id
     SPOTIFY_CLIENT_SECRET=your_client_secret
     SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080/callback
     ENABLE_SPOTIFY=true
     ```
   
   **SETUP GUIDE:**
   1. Go to https://developer.spotify.com/dashboard
   2. Click "Create app"
   3. Fill out:
      - App name: "DJ R3X CantinaOS"  
      - App description: "Voice-controlled music system for DJ R3X droid"
      - Redirect URI: `http://127.0.0.1:8080/callback`
      - Which API/SDKs: Web API
      - Check agreement boxes
   4. Copy Client ID and Client Secret
   5. Add to .env file in project root:
      ```bash
      # Spotify API Configuration
      SPOTIFY_CLIENT_ID=your_client_id_here
      SPOTIFY_CLIENT_SECRET=your_client_secret_here
      SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080/callback
      ENABLE_SPOTIFY=true
      ```

2. **Test Real Authentication** (30 minutes)
   - [ ] Start CantinaOS with Spotify enabled
   - [ ] Run first `spotify search jazz` command
   - [ ] Complete OAuth flow in browser
   - [ ] Verify token storage works

3. **Validate Basic Functionality** (15 minutes)
   - [ ] Test `spotify search <query>` with real API
   - [ ] Test `spotify play <track>` with preview URLs
   - [ ] Test fallback to local when Spotify fails
   - [ ] Verify commands show in help system

### 🎯 Done When

**Phase 1 - Architecture Fixes Complete:** ✅ **FULLY COMPLETED**
- ✅ Single service handling MUSIC_COMMAND events (no duplicates)
- ✅ Local music plays at normal speed (not slow/dragged)
- ✅ DJ mode functions correctly
- ✅ Only one MUSIC_PLAYBACK_STARTED event per command

**Phase 2 - Documentation Updates Complete:**
- ✅ MusicSourceManagerService properly documented in Service Registry
- ✅ Event topology reflects correct MUSIC_COMMAND routing
- ✅ Command flow diagrams show provider orchestration
- ✅ Provider pattern architecture documented

**Phase 3 - Spotify Integration Complete:**
- ✅ "spotify search jazz" returns real Spotify tracks
- ✅ "spotify play <song>" plays 30-second previews  
- ✅ Local music still works as default
- ✅ Service starts without errors

**Total Time**: ~2.5 hours (1.5 hours architecture fixes + 0.5 hours documentation + 1 hour Spotify API integration)

---

## 📚 Reference Links

- [Spotify Web API Documentation](https://developer.spotify.com/documentation/web-api/)
- [OAuth 2.0 Authorization Code Flow](https://developer.spotify.com/documentation/general/guides/authorization/code-flow/)

---

**Status**: Architecture Fixes Complete - Ready for Spotify API Integration  
**Branch**: `spotify`  
**Implementation**: ~3,400 lines production code + 610 lines tests  
**Priority**: Test Spotify OAuth authentication and API functionality