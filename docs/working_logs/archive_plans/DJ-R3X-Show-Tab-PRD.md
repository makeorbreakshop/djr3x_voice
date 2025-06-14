# DJ R3X Show Tab - Product Requirements Document

**Version:** 1.0  
**Date:** June 8, 2025  
**Author:** Development Team  
**Status:** Planning Phase  

---

## 1. Executive Summary

The DJ R3X Show Tab is a new dashboard interface designed for **audience entertainment and live demonstration**. Unlike the technical monitoring tabs, this interface transforms CantinaOS's rich real-time data into a **visually compelling, Star Wars-themed experience** suitable for public display, live events, and audience engagement.

### Key Objectives
- Create an **audience-facing entertainment interface**
- Leverage **authentic Star Wars UI design principles**
- Transform technical data into **engaging visual storytelling**
- Provide a **live demonstration platform** for DJ R3X capabilities

---

## 2. Design Philosophy

### 2.1 Star Wars Aesthetic Compliance
Following the **Star Wars Style Guide**, the interface will feature:
- **Retro-tech "used future" aesthetic** with embedded control panel feel
- **Aurebesh typography** with English subtitles
- **Cantina-appropriate color scheme** (green, amber, red on black)
- **Diegetic design** - interface exists "in-world" as cantina equipment
- **Minimal visual noise** with high readability

### 2.2 Visual Focus Over Technical Details
- **Large, cinematic presentation** optimized for viewing distance
- **Entertainment-focused language** (cantina terminology vs technical jargon)
- **Simplified status indicators** - small, unobtrusive service health dots
- **Emphasis on DJ R3X character** and performance aspects

---

## 3. Core Components

### 3.1 DJ R3X Character Stage
**Primary Visual Element - Upper Center**

**Visual Representation:**
- Large **pixelated droid avatar** representing DJ R3X
- **Real-time state animations** synchronized with CantinaOS events:
  - IDLE: Subtle pulsing glow
  - LISTENING: Audio waveform around character
  - THINKING: Swirling particle effects  
  - SPEAKING: Synchronized mouth/eye animation
  - DJING: Musical note particles, rhythm effects

**CantinaOS Integration:**
- `voice_status` events for listening/processing states
- `speech_synthesis_started/ended` for speaking animation
- `system_mode_change` for character behavior shifts
- Arduino LED pattern synchronization via `LED_COMMAND` events

### 3.2 Live Conversation Display
**Large Text Panel - Upper Right**

**Content Display:**
- **Current DJ R3X dialogue** in large, readable text
- **Real-time conversation scrolling** with teletype animation
- **Aurebesh translation overlay** (optional toggle)
- **Confidence indicators** as subtle visual elements

**Features:**
- **Entertainment-focused filtering** - technical responses translated to audience-friendly language
- **Conversation history** with smooth scrolling
- **Character personality emphasis** - highlight DJ R3X's entertainer persona

**CantinaOS Integration:**
- `transcription_update` for visitor speech
- `llm_response` for DJ R3X replies
- `transcription_final` for conversation flow

### 3.3 DJ Performance Center
**Entertainment Broadcast Panel - Center/Lower**

**Now Playing Display:**
- **Large album art frame** with holographic appearance
- **Track information** in Star Wars cantina styling
- **Progress visualization** with chunky, retro-tech progress bars
- **Volume/ducking indicators** during speech

**DJ Commentary Stream:**
- **AI-generated commentary** in scrolling holocron-style text
- **Track transition announcements**
- **Entertainment-focused narrative** about music selection

**Queue Preview:**
- **Next 3-5 tracks** with smooth animations
- **"Cantina playlist" theming** using Star Wars music terminology
- **Transition countdown** for automatic DJ mode

**CantinaOS Integration:**
- `music_status` for current playback state
- `dj_status` for automated DJ mode activities  
- `crossfade_started` for transition effects
- `llm_response` for DJ commentary generation

### 3.4 System Status Indicators
**Minimal, Ambient Display - Corners/Edges**

**Service Health:**
- **Small indicator dots** (●○◉) showing key service status
- **Color-coded** per Star Wars palette (green=healthy, amber=warning, red=error)
- **Unobtrusive placement** - corners or panel edges
- **Hover/click for details** (optional)

**System Information:**
- **Cantina system identifier** (e.g., "BATUU-SYS-6630-S")
- **Current system mode** as cantina operational status
- **Uptime/session time** as "Entertainment Hours Active"

**CantinaOS Integration:**
- `service_status_update` for health indicators
- `system_mode_change` for operational status
- `performance_metrics` for system health

### 3.5 Live Activity Feed
**Scrolling Event Display - Bottom**

**Content:**
- **Audience-friendly event descriptions**:
  - Technical: "SERVICE_STATUS_UPDATE received"
  - Display: "DJ R3X personality core: ENTERTAINER mode active"
- **Major system events** translated to cantina operations
- **Music transitions, visitor interactions, system modes**

**Visual Style:**
- **Scrolling text ticker** with scanline effects
- **Event icons** using Star Wars UI motifs
- **Fade-in animations** for new events

---

## 4. Technical Implementation

### 4.1 Component Architecture
```
ShowTab.tsx (Main Container)
├── DJCharacterStage.tsx (Animated R3X representation)
├── ConversationDisplay.tsx (Large dialogue panel)
├── DJPerformanceCenter.tsx (Music & entertainment focus)
├── SystemStatusIndicators.tsx (Minimal health indicators)
└── LiveActivityFeed.tsx (Event ticker)
```

### 4.2 Reusable Components
- **Extend existing `AudioSpectrum.tsx`** for character animations
- **Leverage `SocketContext.tsx`** for all real-time data
- **Build on Star Wars theming** from existing dashboard components
- **Adapt existing service status logic** for minimal indicators

### 4.3 Data Requirements

**Real-time Socket Events:**
- `voice_status` - Character state changes
- `transcription_update` - Live conversation
- `llm_response` - DJ responses and commentary
- `music_status` - Current playback information
- `dj_status` - DJ mode operations
- `service_status_update` - System health (minimal display)
- `system_mode_change` - Operational status
- `cantina_event` - General system events

**Event Translation Layer:**
- Technical events → Audience-friendly descriptions
- Service names → Cantina equipment terminology
- Error states → Entertainment-appropriate messaging

---

## 5. User Experience Goals

### 5.1 Primary Use Cases
1. **Live Demonstrations** - Showcasing DJ R3X capabilities to audiences
2. **Event Entertainment** - Public display at parties or gatherings  
3. **Development Showcase** - Presenting the system to stakeholders
4. **Educational Display** - Teaching about voice AI and Star Wars tech

### 5.2 Success Metrics
- **Visual Appeal** - Interface is engaging for non-technical viewers
- **Authenticity** - Feels like genuine Star Wars cantina equipment
- **Clarity** - Information is clear at viewing distance
- **Performance** - Real-time updates without lag or glitches
- **Immersion** - Maintains Star Wars atmosphere throughout use

---

## 6. Implementation Phases

### Phase 1: Core Layout & Static Design
- Create main component structure
- Implement Star Wars visual styling
- Add static content placeholders
- Establish responsive layout

### Phase 2: Real-time Data Integration
- Connect to CantinaOS socket events
- Implement character state animations
- Add live conversation display
- Create music performance panel

### Phase 3: Enhancement & Polish
- Add Aurebesh translation features
- Implement advanced animations
- Create event translation layer
- Performance optimization

### Phase 4: Testing & Refinement
- Test with live CantinaOS system
- Refine visual effects and timing
- Optimize for different screen sizes
- User acceptance testing

---

## 7. Technical Constraints

### 7.1 Performance Requirements
- **60fps animations** for smooth character movements
- **Sub-200ms latency** for real-time event display
- **Responsive design** for various screen sizes
- **Minimal resource usage** to avoid impacting CantinaOS

### 7.2 Integration Requirements
- **Read-only interface** - no system control capabilities
- **Socket.io compatibility** with existing WebBridge service
- **Event filtering** to avoid overwhelming visual display
- **Graceful degradation** if CantinaOS services are unavailable

---

## 8. Future Considerations

### 8.1 Potential Enhancements
- **Audience interaction features** (request submission)
- **Multiple display modes** (full-screen, windowed)
- **Recording capabilities** for demonstration videos
- **Multi-language support** for international audiences

### 8.2 Integration Opportunities
- **Physical hardware integration** with cantina lighting
- **External display support** for large screens
- **Mobile-responsive version** for tablet displays
- **Screen recording/streaming** for remote demonstrations

---

## 9. Acceptance Criteria

### 9.1 Functional Requirements
- [ ] Real-time display of DJ R3X character states
- [ ] Live conversation with large, readable text
- [ ] Current music information with visual progress
- [ ] Minimal system status indicators
- [ ] Event feed with audience-friendly descriptions
- [ ] Star Wars visual theming throughout

### 9.2 Technical Requirements
- [ ] Integration with all specified CantinaOS events
- [ ] Responsive layout for different screen sizes
- [ ] Smooth animations without performance impact
- [ ] Error handling for service outages
- [ ] Aurebesh typography implementation

### 9.3 Design Requirements
- [ ] Compliance with Star Wars Style Guide
- [ ] Authentic cantina equipment appearance
- [ ] High contrast, readable text at distance
- [ ] Consistent color scheme and visual hierarchy
- [ ] Professional quality suitable for public display

---

**Document Status:** Ready for Development  
**Next Steps:** Begin Phase 1 implementation with component structure and static design