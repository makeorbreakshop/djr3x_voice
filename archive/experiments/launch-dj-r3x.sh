#!/bin/bash

# DJ R3X Voice launcher script
# This script launches the DJ R3X Voice program from any directory

# Auto-detect the project directory (where this script is located)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Navigate to the cantina_os directory
cd "$SCRIPT_DIR/cantina_os" || { 
    echo "❌ Error: Could not navigate to $SCRIPT_DIR/cantina_os"
    echo "   Make sure you're running this from the djr3x_voice project directory."
    exit 1
}

# Activate virtual environment if it exists
# Check multiple possible venv locations
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "🔄 Activating virtual environment..."
    source "$SCRIPT_DIR/venv/bin/activate"
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "🔄 Activating virtual environment..."
    source "$SCRIPT_DIR/.venv/bin/activate"
else
    echo "⚠️  No virtual environment found, using system Python"
fi

# Detect the correct Python command
PYTHON_CMD=""
if command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
else
    echo "❌ Error: No Python interpreter found!"
    echo "   Please install Python or activate a virtual environment."
    exit 1
fi

echo "🐍 Using Python: $PYTHON_CMD"

# Set VLC environment variables for macOS VLC.app installation
export VLC_PLUGIN_PATH="/Applications/VLC.app/Contents/MacOS/plugins"
export DYLD_LIBRARY_PATH="/Applications/VLC.app/Contents/MacOS/lib:$DYLD_LIBRARY_PATH"

# Launch the program - use the cantina_os module
echo "🚀 Starting DJ R3X Voice..."
$PYTHON_CMD -m cantina_os.main "$@"

# This line will only be reached when the program exits
echo "👋 DJ R3X Voice has been shut down."
