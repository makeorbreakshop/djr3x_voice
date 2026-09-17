# Textual Dashboard Service

A terminal-based TUI (Text User Interface) dashboard for real-time monitoring of DJ R3X system state.

## Overview

The TextualDashboardService provides an organized, visual alternative to raw log output. It displays service health, event logs, music player state, and system mode in a clean dashboard layout.

## Features

- **Service Status Panel**: Real-time grid showing all service health (✓ running, ✗ error, ○ other)
- **Event Log**: Scrolling log with filtered events (configurable minimum level)
- **Music Player**: Current track display with artist and title
- **System Mode Indicator**: Shows current operational mode (IDLE, INTERACTIVE, DJ, etc.)
- **State Tracking**: Captures events even when TUI is disabled (for testing/background monitoring)

## Architecture

- **Follows CantinaOS Patterns**: Inherits from `BaseService`, uses event bus exclusively
- **Event-Driven**: Subscribes to 8 key event topics for comprehensive system monitoring
- **Optional**: Can be disabled via configuration without affecting core functionality
- **No Overhead**: Direct event bus access (no WebSocket bridge needed)
- **Async**: Runs in background asyncio task, non-blocking

## Configuration

```python
{
    "enable_dashboard": True,      # Master switch (default: True)
    "log_filter_level": "INFO",    # Minimum log level to display (default: INFO)
    "refresh_rate_hz": 10,         # Dashboard update frequency in Hz (default: 10)
    "max_log_lines": 100          # Event log buffer size (default: 100)
}
```

## Event Subscriptions

The service subscribes to the following event topics:

| Event Topic | Purpose |
|------------|---------|
| `SERVICE_STATUS_UPDATE` | Track service health states |
| `TRANSCRIPTION_FINAL` | Display user speech recognition results |
| `MUSIC_PLAYBACK_STARTED` | Show when music playback begins |
| `MUSIC_PLAYBACK_STOPPED` | Show when music playback stops |
| `TRACK_PLAYING` | Update current track metadata |
| `SYSTEM_MODE_CHANGED` | Show mode transitions (IDLE/INTERACTIVE/DJ) |
| `LLM_RESPONSE_TEXT` | Display Claude/GPT responses |
| `SPEECH_SYNTHESIS_STARTED` | Show TTS generation events |

## Usage

### Enabling the Dashboard

The dashboard is registered in `main.py` and will start automatically when CantinaOS launches.

To enable/disable, set the `enable_dashboard` config flag:

```python
# In main.py service configuration
"textual_dashboard": {
    "enable_dashboard": True  # or False to disable
}
```

### Testing

Run the standalone test script to verify functionality:

```bash
cd cantina_os
../venv/bin/python test_textual_dashboard.py
```

This tests:
- Service initialization
- Event handling and state tracking
- Graceful start/stop

## Dashboard Layout

```
┌─ DJ R3X System Dashboard ────────────────────────────────┐
│ System Mode: INTERACTIVE                                   │
├───────────────────────────────────────────────────────────┤
│ Services Status:                                           │
│  Service          Status         Message                   │
│  ✓ mic            RUNNING        Listening for audio...    │
│  ✓ claude         RUNNING        LLM ready                │
│  ✓ tts            RUNNING        Speech synthesis ready   │
│  ⚠ vision         WARNING        Camera not found         │
├───────────────────────────────────────────────────────────┤
│ Event Log:                                                 │
│ 14:23:45 [brain] DJ mode activated                         │
│ 14:23:46 [music] Playback started: "Cantina Band"         │
│ 14:23:47 [tts] Speech synthesis started (2.3s duration)   │
├───────────────────────────────────────────────────────────┤
│ Current Track:                                             │
│ ♫ "Cantina Band" - John Williams                          │
└───────────────────────────────────────────────────────────┘
```

## Development Notes

- **State Tracking**: Service tracks state internally even when TUI is disabled
- **Thread-Safe**: Uses event loop reference for safe updates
- **Graceful Degradation**: If Textual fails to start, service logs warning but continues
- **Log Preservation**: Full logs still written to file (DEBUG level) for debugging

## Future Enhancements

- Keyboard shortcuts for live log level adjustment
- DJ mode transition visualization
- Service restart buttons (interactive mode)
- Track progress bar with time remaining
- Volume level meters

## Files

- `textual_dashboard_service.py` - Main service implementation (550+ lines)
- `__init__.py` - Module exports
- `README.md` - This file
- `test_textual_dashboard.py` - Test suite (in parent directory)

## Dependencies

- `textual` - TUI framework (installed via pip)
- `rich` - Terminal formatting (installed as textual dependency)
- `pydantic` - Configuration validation (already in CantinaOS)

## Integration

The service is registered in `main.py`:

1. Import: `from .services.textual_dashboard_service import TextualDashboardService`
2. Service map: `"textual_dashboard": TextualDashboardService`
3. Service order: Added before `cli` service
