#!/bin/bash
# DJ R3X Clean Launcher
# Kills all old instances and starts fresh

echo "🤖 DJ R3X Clean Launcher"
echo "========================"

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

# Start DJ R3X
cd "/Users/brandoncullum/DJ-R3X Voice/cantina_os"
../venv/bin/python -m cantina_os.main
