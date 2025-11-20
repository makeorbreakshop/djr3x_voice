#!/bin/bash
# Webcam Selection Utility Launcher
# Launches the interactive webcam selector with proper Python environment

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_ROOT/../venv/bin/python"

echo "Launching CantinaOS Webcam Selector..."
echo ""

# Run the selector
"$VENV_PYTHON" "$PROJECT_ROOT/select_webcam.py"
