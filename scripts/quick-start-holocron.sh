#!/bin/bash
# Quick start script for Holocron pipeline

# Navigate to the project directory
cd ~/djr3x_voice || {
    echo "Error: Could not navigate to ~/djr3x_voice"
    exit 1
}

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Error: No virtual environment found (tried venv and .venv)"
    exit 1
fi

# Kill any existing holocron_pipeline screen sessions (cleanup)
screen -X -S holocron_pipeline quit >/dev/null 2>&1

# Run the pipeline directly (not in screen)
echo "Starting Holocron pipeline..."
echo "Press Ctrl+C to stop"
echo "-------------------"
./scripts/run_holocron_continuously.sh 