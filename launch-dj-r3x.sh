#!/bin/bash
# DJ R3X Clean Launcher
# Kills all old instances and starts fresh
#
# Usage:
#   dj-r3x              # Start with interim streaming enabled (default, fast)
#   dj-r3x --no-interim # Start without interim streaming (baseline, classic behavior)

echo "🤖 DJ R3X Clean Launcher"
echo "========================"

# Parse command line arguments
INTERIM_STREAMING="true"
if [ "$1" == "--no-interim" ]; then
    INTERIM_STREAMING="false"
    echo "⚠️  Running in BASELINE mode (no interim streaming)"
elif [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    echo "Usage: dj-r3x [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --no-interim   Disable interim transcription streaming (baseline mode for A/B testing)"
    echo "  --help, -h     Show this help message"
    echo ""
    exit 0
fi

# Kill all existing DJ R3X processes
echo "Killing old DJ R3X processes..."
pkill -9 -f "cantina_os.main" 2>/dev/null
sleep 1

# Verify they're dead
OLD_PROCS=$(ps aux | grep "cantina_os.main" | grep -v grep | wc -l | tr -d ' ')
if [ "$OLD_PROCS" -gt 0 ]; then
    echo "⚠️  Warning: $OLD_PROCS processes still running, force killing..."
    killall -9 Python 2>/dev/null
    sleep 1
fi

echo "✅ All old processes terminated"
echo ""
echo "Starting DJ R3X..."
echo "=================="
if [ "$INTERIM_STREAMING" == "true" ]; then
    echo "Mode: INTERIM STREAMING ENABLED (low latency)"
else
    echo "Mode: BASELINE (classic behavior, for A/B comparison)"
fi
echo ""

# Start DJ R3X with interim streaming flag
cd "/Users/brandoncullum/DJ-R3X Voice/cantina_os"
ENABLE_INTERIM_STREAMING="$INTERIM_STREAMING" ../venv/bin/python -m cantina_os.main
