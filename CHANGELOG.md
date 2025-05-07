# Changelog

All notable changes to the DJ-R3X Voice Assistant will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Complete redesign with new event-driven MVP architecture
  - Implemented EventBus system for inter-component communication
  - Created dedicated manager classes for voice, LED, and music functionality
  - Added proper resource management and cleanup
  - Improved error handling and event propagation
- Enhanced README documentation with clear architecture explanation
  - Added detailed component descriptions
  - Included event system overview
  - Expanded feature documentation
- Added new command-line options for improved usability
  - Test mode that works without API keys
  - Demo mode with predefined interactions
  - Music playback options
- Implemented robust event system with specialized event types
  - Voice events (listening, processing, speaking)
  - Music events (track started, volume changes)
  - System events (ready, error, shutdown)
- Debug output showing current voice settings in use
- Improved visibility of which voice configuration values are active
- Local Whisper speech recognition implementation
  - Replaced Google Speech-to-Text with offline Whisper model
  - Added WhisperManager class for handling transcription
  - Added performance metrics and timing for speech processing
- Improved push-to-talk functionality
  - Added proper space bar toggle mechanism
  - Implemented recording state tracking
  - Added 30-second recording timeout protection
  - Updated user feedback messages for clarity
  - Fixed issues with unintended recording loops
- Holocron RAG system for Star Wars knowledge base
  - Created comprehensive Product Requirements Document
  - Implemented Supabase vector database with pgvector
  - Added configuration in app_settings.py and .env 
  - Created scripts for setup and connection testing
  - Designed schema optimized for semantic search
  - Added similarity search function with threshold filtering

### Changed
- Restructured codebase with modular architecture
  - Separated concerns into dedicated manager classes
  - Moved from monolithic design to event-driven pattern
  - Improved maintainability with clear component boundaries
- Enhanced LED animation system
  - Added more animation patterns
  - Improved synchronization with audio
  - Better handling of Arduino connection issues
- Improved music management
  - Added automatic ducking during speech
  - Enhanced playlist and random track selection
  - Added smooth volume transitions
- Fixed voice configuration import structure
  - Now properly using settings from `voice_settings.py` instead of defaults from `voice_config.py`
  - Separated environment variable loading from voice configuration
  - Clarified relationship between `voice_settings.py` (user settings) and `voice_config.py` (implementation)
- Switched to CPU-only Whisper processing due to MPS limitations
  - Removed attempted GPU acceleration due to sparse tensor operation incompatibility
  - Optimized for CPU performance with base model
- Updated push-to-talk interface from hold-to-talk to toggle mode
  - Changed from Enter key to Space bar for better usability
  - Improved state management for recording sessions

### Fixed
- Implemented proper resource cleanup for all components
  - Serial port connections for LEDs
  - Audio playback resources
  - Background tasks and threads
  - Event handlers and listeners
- Environment variables now loaded correctly in main script instead of through configuration
- Removed redundant API key imports from voice configuration
- Audio processing flag now imported separately from voice settings
- Fixed push-to-talk recording loop issues
  - Resolved unintended continuous recording
  - Added proper state cleanup after recording
  - Implemented safeguards against recording deadlocks

### Performance
- Optimized event processing with efficient event bus implementation
- Improved component initialization and shutdown procedures
- Added graceful degradation for missing hardware or API keys
- Optimized audio playback when processing is disabled
  - Removed unnecessary file operations
  - Implemented direct streaming from ElevenLabs to playback
- Speech recognition improvements:
  - Eliminated network latency from speech recognition
  - Consistent ~0.78s transcription time
  - One-time model load of ~0.58s
  - Removed dependency on internet connectivity for speech recognition 