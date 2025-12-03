# DJ R3X Dashboard Usage Guide

## 🎉 NEW: Dashboard with Command Input!

DJ R3X now has a **visual dashboard WITH command input**! You get the best of both worlds:
- ✅ Visual panels (service status, event log, music player)
- ✅ Command input box (type commands just like before)
- ✅ Color-coded responses (green ✓ for success, red ✗ for errors)

### Default Mode (Dashboard Mode)
- **What**: Full-screen Textual dashboard with command input box
- **Launch**: `dj-r3x` (your normal command now uses the dashboard!)
- **Features**:
  - Service status grid with live health monitoring
  - Scrolling event log with filtered important events
  - Music player display with current track
  - System mode indicator (IDLE/INTERACTIVE/DJ)
  - **Command input box** at the bottom for typing commands

### Legacy CLI Mode (Optional)
- **What**: Old text-only interface (no visual panels)
- **When**: If you prefer the old style or dashboard has issues
- **Launch**: Set `ENABLE_TUI_DASHBOARD=false` then run `dj-r3x`
- **Note**: The dashboard is now the default!

## Launching Dashboard Mode

### Option 1: Launch Script (Easiest)
```bash
cd /Users/brandoncullum/djr3x_voice/cantina_os
./launch_with_dashboard.sh
```

### Option 2: Environment Variable
```bash
cd /Users/brandoncullum/djr3x_voice/cantina_os/cantina_os
ENABLE_TUI_DASHBOARD=true ../../venv/bin/python -m cantina_os.main
```

### Option 3: Export Environment Variable
```bash
export ENABLE_TUI_DASHBOARD=true
dj-r3x  # Will now launch with dashboard
```

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
│ > play music 5                                             │
│ ✓ Now playing track 5: "Mad About Me"                     │
├───────────────────────────────────────────────────────────┤
│ Current Track:                                             │
│ ♫ "Mad About Me" - Hooverphonic                           │
├───────────────────────────────────────────────────────────┤
│ Command:                                                   │
│ engage_                                                     │  ← TYPE HERE!
│ ✓ Now playing track 5: "Mad About Me"                     │
└───────────────────────────────────────────────────────────┘
```

## Dashboard Controls

- **Type Commands**: Click in the command input box and type (e.g., "play music 1", "engage", "quit")
- **Submit Command**: Press `Enter` to execute
- **Quit Dashboard**: Type `quit` or press `Ctrl+C`
- **Scroll Event Log**: Use arrow keys or mouse wheel
- **View Services**: Services table updates automatically (10 Hz refresh)

**NEW**: You CAN type commands directly in the dashboard! The input box at the bottom works just like the old CLI.

## When to Use Each Mode

### Use CLI Mode When:
- You want to manually control DJ R3X with typed commands
- You're testing specific commands
- You need to see verbose log output
- You want command history and shortcuts

### Use Dashboard Mode When:
- You want to monitor system health visually
- DJ R3X is running autonomously (voice-only control)
- You prefer organized panels over log spam
- You're demonstrating the system to others

## Logging Behavior

Both modes preserve full logs:

### CLI Mode
- **Console**: INFO level (clean, minimal)
- **File**: DEBUG level (full detail in `logs/dj_r3x_TIMESTAMP.log`)

### Dashboard Mode
- **Console**: Replaced by TUI panels
- **File**: DEBUG level (same as CLI mode)
- **Event Log Panel**: INFO level (filtered for readability)

## Switching Modes

To switch from one mode to another:

1. **Stop current mode**: Press `q` or `Ctrl+C`
2. **Launch other mode**:
   - CLI: `dj-r3x`
   - Dashboard: `./cantina_os/launch_with_dashboard.sh`

## Configuration

Dashboard appearance can be customized via environment variables:

```bash
# Enable/disable dashboard
export ENABLE_TUI_DASHBOARD=true

# Set log filter level (DEBUG, INFO, WARNING, ERROR)
export TUI_LOG_LEVEL=INFO

# Then launch
dj-r3x
```

## Troubleshooting

### Dashboard not appearing
- Check that `ENABLE_TUI_DASHBOARD=true` is set
- Verify textual is installed: `venv/bin/pip list | grep textual`
- Check logs for errors: `tail -f logs/dj_r3x_*.log`

### Terminal garbled after dashboard exit
Run: `reset` to restore terminal

### Can't type commands in dashboard
This is expected - dashboard mode doesn't support command input. Use CLI mode instead.

## Summary

```bash
# Normal CLI mode (can type commands)
dj-r3x

# Dashboard mode (visual monitoring only)
./cantina_os/launch_with_dashboard.sh
```

Choose the mode that fits your current needs! 🎵🤖
