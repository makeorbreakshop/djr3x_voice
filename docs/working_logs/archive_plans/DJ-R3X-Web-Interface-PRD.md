# DJ R3X Monitoring Dashboard - Product Requirements Document (PRD)

## 1. Executive Summary

### 1.1 Overview
The DJ R3X Monitoring Dashboard is a Star Wars-inspired data pad interface that provides real-time monitoring and control of the CantinaOS-powered DJ R3X voice assistant system. This dashboard complements the existing CLI by offering visual system monitoring, audio visualization, and convenient interaction controls for the primary developer/operator.

### 1.2 Problem Statement
The current DJ R3X system operates entirely through CLI, making it difficult to:
- Monitor real-time system status and service health
- Visualize audio processing and voice interactions
- Quickly trigger voice interactions and music controls
- Debug system performance and service issues
- Get visual feedback on voice transcription and processing

### 1.3 Solution
A monitoring dashboard built with modern web technologies that provides:
- Real-time system monitoring with performance metrics
- Live audio visualization and voice transcription display
- One-click voice interaction triggers and music controls
- Service health monitoring with detailed logs
- Star Wars terminal aesthetic optimized for single-user operation

## 2. User Profile & Use Cases

### 2.1 Primary User
**System Operator/Developer** (Single User - You)
- Operating DJ R3X system from same computer
- Needs visual monitoring of system status and real-time feedback
- Wants quick access to voice controls and music management
- Requires debugging capabilities and performance monitoring
- Prefers Star Wars aesthetic with clean, modern interface

### 2.2 Key Use Cases

1. **System Status Check**: Open dashboard to see overall system health and service status
2. **Voice Interaction Monitoring**: Trigger voice recording and monitor live transcription
3. **Music Management**: Visual music library browsing and playback control
4. **DJ Mode Operation**: Start/stop DJ mode with visual queue and commentary monitoring
5. **Real-time Debugging**: Monitor event logs and service performance metrics
6. **Audio Visualization**: Real-time spectrum analysis and voice processing feedback

## 3. Technical Architecture

### 3.1 Technology Stack

**Frontend Framework: Next.js + TypeScript**
- Server-side rendering for fast initial dashboard load
- Excellent WebSocket support with Socket.io
- Rich React ecosystem for UI components
- Optimized for single-page dashboard applications
- Better development experience for complex interfaces

**Styling: Tailwind CSS + Star Wars Theme**
- Star Wars-inspired data pad aesthetic
- Blue accent colors with terminal/hologram styling
- Dark theme with modern typography
- Responsive design optimized for desktop

**Backend Bridge: FastAPI + Python**
- Seamless integration with existing CantinaOS Python architecture
- High-performance async WebSocket handling with Socket.io
- Automatic API documentation generation
- Type safety with Pydantic models

**Communication Layer: Socket.io + REST API**
- Socket.io for reliable real-time event streaming
- REST API for command execution and configuration
- Automatic reconnection and event buffering
- Structured event protocol with room-based filtering

**State Management: React Context + Real-time Updates**
- React Context for global dashboard state
- Real-time state updates from Socket.io events
- Efficient re-rendering for high-frequency updates
- Event history with performance monitoring

### 3.2 Integration Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Web Frontend  │────▶│  Web Bridge     │────▶│   CantinaOS     │
│   (Svelte)      │     │  Service        │     │  Event Bus      │
│                 │◀────│  (FastAPI)      │◀────│                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                        │                        │
        │                        │                        │
    WebSocket              Event Filtering         15+ Microservices
   REST API                WebSocket Manager       (Voice, Music, LEDs)
```

### 3.3 WebSocket Event Streams

**Event Categories:**
- `voice`: Transcription, processing status, audio levels
- `audio`: Amplitude data, spectrum analysis, visualization  
- `music`: Playback status, track info, volume changes
- `leds`: Pattern changes, Arduino connectivity, brightness
- `system`: Service health, performance metrics, error alerts
- `dj`: Mode changes, track selection, commentary generation

**Event Protocol:**
```json
{
  "stream": "voice",
  "event": "transcription_interim",
  "data": {
    "text": "Hey DJ R3X, play some cantina music",
    "confidence": 0.95,
    "timestamp": 1703123456789
  },
  "sequence": 1234
}
```

### 3.4 REST API Design

**Core Endpoints:**
- **System**: `/api/system/{status,mode,services,restart}`
- **Voice**: `/api/voice/{start,stop,status,text}`  
- **Music**: `/api/music/{library,current,play,stop,volume}`
- **DJ Mode**: `/api/dj/{status,start,stop,next,queue}`
- **Eyes/LEDs**: `/api/eyes/{patterns,pattern,test,status}`
- **Config**: `/api/config/{apis,hardware}`
- **Debug**: `/api/debug/{logs,level,events,command}`

## 4. User Interface Design

### 4.1 Main Dashboard Layout

**Desktop-First Design (1200px+):**
- Header: DJ R3X logo, global system status, connection indicator
- Tabbed Interface: MONITOR | VOICE | MUSIC | DJ MODE | SYSTEM
- Star Wars terminal aesthetic with blue accent colors
- Dark theme with holographic/data pad styling
- Optimized for single-user monitoring experience

### 4.2 Tabbed Interface Structure

**MONITOR Tab (Default)**
- System overview with service status grid
- Real-time audio visualization (spectrum analyzer)
- Live transcription feed from voice interactions
- Performance metrics (CPU, memory, events/sec)
- Quick status indicators for all major components

**VOICE Tab**
- Large "Start Voice Recording" trigger button
- Real-time transcription display with confidence scores
- Voice processing status and response generation
- AI response playback status and controls

**MUSIC Tab**
- Visual music library browser with search/filtering
- Current track display with metadata
- Playback controls with volume and ducking visualization
- Track queue and playlist management

**DJ MODE Tab**
- DJ mode activation/deactivation controls
- Auto-transition settings and status
- Commentary generation monitoring
- Track queue with upcoming selections

**SYSTEM Tab**
- Detailed service health with performance metrics
- Real-time event log viewer with filtering
- Service restart and debugging tools
- CantinaOS configuration monitoring

### 4.3 Advanced Features

**Audio Visualization**
- Real-time spectrum analyzer using Web Audio API
- Voice level meters during recording
- Music waveform display during playback
- Volume ducking visualization during speech

**Debug Console**
- Live event stream with filtering by category
- Command execution interface matching CLI functionality
- Service log viewer with real-time updates
- Performance profiling and bottleneck identification

## 5. Feature Requirements

### 5.1 Core Features (MVP)

**Voice Interaction**
- ✅ Voice recording start/stop controls
- ✅ Real-time transcription display
- ✅ AI response playback status
- ✅ Voice processing indicators

**Music Control**
- ✅ Music library browsing
- ✅ Track playback controls (play/pause/stop)
- ✅ Volume control with ducking display
- ✅ Current track information

**System Control**
- ✅ Mode switching (IDLE/AMBIENT/INTERACTIVE)
- ✅ Service status monitoring
- ✅ Basic system information

**LED Control**
- ✅ Pattern selection and preview
- ✅ Arduino connection status
- ✅ Basic pattern testing

### 5.2 Enhanced Features (V2)

**DJ Mode Management**
- ✅ DJ mode activation/deactivation
- ✅ Auto-transition controls
- ✅ Track queue management
- ✅ Commentary generation status

**Audio Visualization**
- ✅ Real-time spectrum analysis
- ✅ Voice level monitoring
- ✅ Music waveform display
- ✅ Volume ducking visualization

**Advanced System Control**
- ✅ Service restart capabilities
- ✅ Configuration management
- ✅ Performance monitoring
- ✅ Error alert system

### 5.3 Developer Features (V3)

**Debug Console**
- ✅ Live event stream monitoring
- ✅ Command execution interface
- ✅ Service log analysis
- ✅ Performance profiling

**Configuration Interface**
- ✅ API key management (masked display)
- ✅ Hardware configuration
- ✅ Performance tuning options
- ✅ System diagnostics

## 6. Technical Specifications

### 6.1 Performance Requirements

**Response Times**
- Voice control activation: < 100ms
- Music playback controls: < 200ms
- Real-time event updates: < 50ms
- Page load time: < 2s

**Resource Usage**
- Browser memory: < 100MB
- Network bandwidth: < 1MB/min (excluding audio)
- CPU usage: < 10% on modern devices
- Battery impact: Minimal on mobile devices

### 6.2 Browser Compatibility

**Supported Browsers:**
- Chrome 90+ (primary target for desktop development)
- Firefox 88+ (secondary support)
- Safari 14+ (macOS compatibility)
- Edge 90+ (Windows compatibility)

**Required APIs:**
- Socket.io WebSocket support
- Web Audio API (for spectrum visualization)
- Canvas API (for real-time graphics)
- Modern CSS (Grid/Flexbox for dashboard layout)

### 6.3 Security Requirements

**API Security**
- HTTPS required for production
- API key storage in secure configuration
- WebSocket connection authentication
- Rate limiting on API endpoints

**Data Privacy**
- Voice data processed locally (not stored)
- No persistent audio recording
- Configuration data encrypted at rest
- User session management

## 7. Implementation Plan

### 7.1 Development Phases

**Phase 1: Foundation (2-3 weeks)**
- Set up Next.js + TypeScript project structure
- Implement FastAPI bridge service with Socket.io
- Basic real-time communication between dashboard and CantinaOS
- Core tabbed interface shell with Star Wars styling

**Phase 2: Core Monitoring (2-3 weeks)** 
- Connect bridge service to CantinaOS event bus
- Implement MONITOR tab with system status and audio visualization
- VOICE tab with transcription display and recording triggers
- Real-time event streaming with performance optimization

**Phase 3: Interactive Controls (2-3 weeks)**
- MUSIC tab with library browser and playback controls
- DJ MODE tab with auto-transition management
- Voice interaction triggers and music control commands
- Audio visualization with spectrum analyzer

**Phase 4: Advanced Monitoring (2-3 weeks)**
- SYSTEM tab with detailed service monitoring and logs
- Performance metrics and debugging tools
- Star Wars aesthetic polish and terminal styling
- Testing and optimization for single-user operation

### 7.2 Technical Milestones

1. **WebSocket Bridge Working** - Events flowing from CantinaOS to web
2. **Voice Controls Functional** - Record/stop/transcription display
3. **Music Integration Complete** - Full playback control from web
4. **DJ Mode Operational** - Web control of automatic DJ features
5. **System Monitoring Active** - Service health and debugging tools
6. **Mobile Optimized** - Responsive design for all devices

### 7.3 Testing Strategy

**Unit Testing**
- Svelte component testing with Jest
- API endpoint testing with pytest
- WebSocket connection testing
- Event stream processing validation

**Integration Testing**
- End-to-end user flows with Playwright
- Real-time event processing validation
- Audio capture and playback testing
- Multi-browser compatibility testing

**Performance Testing**
- WebSocket throughput under load
- Memory usage with long-running sessions
- Mobile device performance validation
- Network latency handling

## 8. Success Metrics

### 8.1 User Experience Metrics

**Usability**
- Time to complete voice interaction: < 30s
- Music control task completion: < 10s
- Mode switching success rate: > 95%
- Mobile usability score: > 85%

**Engagement**
- Session duration increase: > 50% vs CLI
- Feature adoption rate: > 80% for core features
- User preference: > 70% prefer web over CLI
- Demo success rate: > 90% for presentations

### 8.2 Technical Performance Metrics

**Reliability**
- WebSocket connection uptime: > 99%
- Event delivery success rate: > 99.5%
- Service integration stability: < 1 failure/day
- Cross-browser compatibility: > 95%

**Performance**
- Real-time latency: < 100ms end-to-end
- Memory usage growth: < 10MB/hour
- CPU usage impact: < 15% average
- Network efficiency: < 500KB/session

## 9. Risk Assessment & Mitigation

### 9.1 Technical Risks

**WebSocket Connection Reliability**
- Risk: Connection drops causing lost events
- Mitigation: Automatic reconnection with exponential backoff
- Fallback: Event replay mechanism for missed events

**Browser Audio API Limitations**
- Risk: Inconsistent microphone access across browsers
- Mitigation: Progressive enhancement with fallback options
- Fallback: Text input mode when audio unavailable

**Real-time Performance at Scale**
- Risk: Event flooding causing UI lag
- Mitigation: Event throttling and selective subscriptions
- Fallback: Reduced refresh rate under high load

### 9.2 User Experience Risks

**Learning Curve for New Interface**
- Risk: Users preferring familiar CLI
- Mitigation: Progressive disclosure of advanced features
- Fallback: Maintain CLI alongside web interface

**Mobile Device Limitations**
- Risk: Poor performance on older mobile devices
- Mitigation: Responsive design with feature degradation
- Fallback: Simplified mobile-only interface

## 10. Future Enhancements

### 10.1 Advanced Integrations

**Voice Wake Word Detection**
- Browser-based wake word processing
- Always-listening mode with privacy controls
- Integration with speech synthesis for confirmations

**IoT Dashboard Expansion**
- Multiple DJ R3X unit management
- Network discovery and configuration
- Synchronized multi-room audio

### 10.2 AI-Powered Features

**Conversation Analytics**
- Sentiment analysis visualization
- Conversation topic tracking
- Personalized response patterns

**Predictive DJ Mode**
- Machine learning for music selection
- Mood-based playlist generation
- User preference learning

### 10.3 Community Features

**Shared Configurations**
- Export/import settings and playlists
- Community pattern sharing for LED animations
- Voice personality customization marketplace

## 11. Conclusion

The DJ R3X Web Interface represents a significant enhancement to the existing CantinaOS system, transforming a technical CLI tool into an accessible, visually engaging interface suitable for demonstrations, casual use, and advanced development. By leveraging modern web technologies and maintaining seamless integration with the existing event-driven architecture, this interface will greatly expand the reach and usability of the DJ R3X voice assistant system.

The phased implementation approach ensures rapid delivery of core functionality while providing a foundation for advanced features and future enhancements. The comprehensive technical architecture supports both simple user interactions and complex debugging scenarios, making it valuable for all user personas from casual users to advanced developers.

---

**Document Version**: 1.0  
**Last Updated**: 2025-06-06  
**Next Review**: Upon completion of Phase 1 implementation