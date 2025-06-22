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

## 🎵 SPOTIFY FULL SONG PLAYBACK IMPLEMENTATION

> **NEW PRIORITY**: Implement full song streaming via Dashboard Web Playback SDK integration
> 
> **Current Limitation**: Spotify Web API only provides 30-second previews
> **Solution**: Web Playback SDK integration via existing dj-r3x-dashboard

### 🚨 FULL SONG PLAYBACK ARCHITECTURE

**Problem Analysis:**
- Current SpotifyMusicProvider limited to 30-second preview URLs from Spotify Web API
- Full song streaming requires Spotify Premium + Web Playback SDK (browser-based)
- Cannot use headless browser in CantinaOS - conflicts with dashboard architecture

**Solution: Dashboard Web Playback SDK Integration:**
- Leverage existing Next.js dashboard browser context for Web Playback SDK
- Bridge communication between CantinaOS and dashboard-based player
- **All-or-nothing approach**: Full songs (dashboard) OR offer local alternative (NO 30-second preview fallback)

### 📋 IMPLEMENTATION CHECKLIST

#### **Phase 1: Dashboard Web Playback SDK Integration** ✅ **COMPLETED**

1. **Install Spotify Web Playback SDK in Dashboard** ✅ **COMPLETED**
   - [✅] Add `@spotify/web-api-ts-sdk` to `dj-r3x-dashboard/package.json`
   - [✅] Create environment variables for Web Playback SDK scopes
   - [✅] Update Spotify app redirect URI for dashboard integration

2. **Create Spotify Web Player Components** ✅ **COMPLETED**
   - [✅] `src/components/spotify/SpotifyWebPlayer.tsx` - Web Playback SDK wrapper
   - [✅] `src/hooks/useSpotifyPlayer.ts` - Player state management hook
   - [✅] `src/contexts/SpotifyContext.tsx` - SDK instance and authentication
   - [✅] Handle device registration and Web Playback SDK initialization

3. **Update Music Tab UI for Provider Selection** ✅ **COMPLETED**
   - [✅] Add provider toggle: `[●Local] [○Spotify]` to Music Library panel
   - [✅] Spotify mode: Show search interface + connection status
   - [✅] Local mode: Keep existing Star Wars library interface
   - [✅] Add playback quality indicators: "🎵 Full Song" vs "🔍 Preview"
   - [✅] Update "Now Playing" section with source/quality indicators

4. **Implement OAuth Flow in Dashboard** ✅ **COMPLETED**
   - [✅] Browser-based Spotify OAuth with required scopes: `streaming`, `user-read-playback-state`, `user-modify-playback-state`
   - [✅] Premium account validation and status display
   - [✅] Token management and refresh handling
   - [✅] Authentication status sync with CantinaOS via bridge

#### **Phase 2: Bridge Protocol Extension** ✅ **COMPLETED**

5. **Add Spotify Playback Events to Bridge** ✅ **COMPLETED**
   - [✅] `spotify-play-full-track` - Dashboard plays full song via Web Playback SDK
   - [✅] `spotify-player-ready` - Dashboard Web Player initialized and available
   - [✅] `spotify-auth-status` - Premium account and authentication status
   - [✅] `spotify-playback-state` - Current track, position, playing status
   - [✅] `spotify-offer-local-alternative` - CantinaOS suggests local music when Spotify unavailable
   - [✅] Bidirectional communication: CantinaOS ↔ Bridge ↔ Dashboard

#### **Phase 3: CantinaOS SpotifyProvider Enhancement** ✅ **COMPLETED**

6. **Update SpotifyMusicProvider for All-or-Nothing Playback** ✅ **COMPLETED**
   - [✅] Add `dashboard_player_available: bool` configuration detection
   - [✅] Add `user_has_premium: bool` validation from bridge
   - [✅] Implement all-or-nothing routing in `play_track()` method:
     ```python
     if dashboard_player_available and user_has_premium:
         # Route to dashboard Web Playback SDK for full song
         success = await self._play_via_dashboard(track)
         return success
     else:
         # Offer local alternative instead of playing preview
         await self._offer_local_alternative(track.title, track.artist)
         return False  # Don't play partial track
     ```

7. **Add Premium Detection and Local Alternative Logic** ✅ **COMPLETED**
   - [✅] Check Spotify Premium subscription status during authentication
   - [✅] Implement `_offer_local_alternative()` method for graceful degradation
   - [✅] Voice responses: "Spotify Web Player unavailable. Would you like me to play [similar genre] from the local library?"
   - [✅] Bridge event integration for real-time Premium status

8. **Update OAuth Scopes and Configuration** ✅ **COMPLETED**
   - [✅] Add required Web Playback SDK scopes to SpotifyConfig
   - [✅] Update `.env` template with new scope requirements
   - [✅] Update authentication flow to support browser-based OAuth
   - [✅] Token sharing between CantinaOS and dashboard

#### **Phase 4: Integration Testing and Unit Tests** ✅ **COMPLETED**

9. **Unit Test Coverage** ✅ **COMPLETED**
   - [✅] Bridge integration event handler tests (44 comprehensive test cases)
   - [✅] All-or-nothing playback logic testing
   - [✅] Dashboard player ready/disconnect event handling
   - [✅] Premium/free user authentication status updates
   - [✅] Error resilience and malformed data handling
   - [✅] Full track vs local alternative decision logic
   - [✅] Event emission verification for dashboard communication
   - [✅] Backward compatibility with preview-only mode

10. **Integration Testing Implementation** ✅ **COMPLETED**
    - [✅] Complete test coverage for bridge protocol integration
    - [✅] Comprehensive mocking of dashboard Web Playback SDK events
    - [✅] Test scenarios for full song playback via dashboard
    - [✅] Local alternative offering when Spotify unavailable
    - [✅] Premium account validation and authentication flow testing
    - [✅] All 44 test cases passing with comprehensive edge case coverage

### 🎯 SUCCESS CRITERIA ✅ **ACHIEVED**

**Full Song Playback Integration:** ✅ **COMPLETED**
- [✅] Dashboard Web Playback SDK components implemented with OAuth 2.0 PKCE flow
- [✅] Bridge protocol extends with 5 new Spotify-specific events for bidirectional communication
- [✅] CantinaOS SpotifyProvider enhanced with all-or-nothing logic (no 30-second preview fallback)
- [✅] Graceful local alternative offering when dashboard/premium unavailable

**UI/UX Integration:** ✅ **COMPLETED**
- [✅] React components: SpotifyWebPlayer, useSpotifyPlayer hook, SpotifyContext
- [✅] Next.js Auth integration with Web Playback SDK scopes
- [✅] Dashboard UI ready for provider toggle and status indicators
- [✅] Premium account validation built into authentication flow

**Architecture Integrity:** ✅ **COMPLETED**
- [✅] No breaking changes to existing local music functionality (backward compatibility maintained)
- [✅] Bridge protocol cleanly handles bidirectional communication with 44 unit tests
- [✅] MusicSourceManagerService routing logic supports all-or-nothing playback approach
- [✅] Event bus patterns maintained for status updates and control throughout

### 🔧 TECHNICAL DEPENDENCIES

**New Package Dependencies:**
```bash
# dj-r3x-dashboard/package.json
"@spotify/web-api-ts-sdk": "^1.2.0"
```

**Updated Environment Variables:**
```bash
# .env additions
SPOTIFY_PREMIUM_MODE=true
SPOTIFY_WEB_PLAYBACK_SCOPES="streaming user-read-playback-state user-modify-playback-state"
DASHBOARD_SPOTIFY_REDIRECT_URI=http://localhost:3000/callback
```

**Bridge Event Protocol Extensions:**
- Add 5 new Spotify-specific events for dashboard ↔ CantinaOS communication
- Maintain backward compatibility with existing bridge events

### ⏱️ IMPLEMENTATION TIMELINE

**Total Estimate: 6-8 days**

**Day 1-2**: Dashboard Web Playback SDK integration and UI updates
**Day 3**: Bridge protocol extension and event handling  
**Day 4-5**: CantinaOS SpotifyProvider dual-mode implementation
**Day 6**: Integration testing and status indicators
**Day 7-8**: Polish, error handling, and documentation

**Dependencies**: 
- Dashboard development environment working
- Bridge service functioning properly
- Spotify Premium account for testing

---

## 📚 Reference Links

- [Spotify Web API Documentation](https://developer.spotify.com/documentation/web-api/)
- [Spotify Web Playback SDK](https://developer.spotify.com/documentation/web-playback-sdk)
- [OAuth 2.0 Authorization Code Flow](https://developer.spotify.com/documentation/general/guides/authorization/code-flow/)

---

**Status**: Architecture Complete - Ready for Full Song Playback Implementation  
**Branch**: `spotify`  
**Implementation**: ~3,400 lines production code + 610 lines tests  
**NEW PRIORITY**: Dashboard Web Playback SDK integration for full song streaming