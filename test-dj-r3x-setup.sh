#!/bin/bash

# Test script to verify DJ R3X setup is working
echo "🧪 Testing DJ R3X setup..."

# Test 1: Check if dj-r3x command exists
echo ""
echo "Test 1: Checking if 'dj-r3x' command is available..."
if command -v dj-r3x >/dev/null 2>&1; then
    echo "✅ 'dj-r3x' command found"
    
    # Show where it's installed
    DJR3X_LOCATION=$(which dj-r3x)
    echo "   Located at: $DJR3X_LOCATION"
    
    # Test 2: Check if the script is executable
    if [ -x "$DJR3X_LOCATION" ]; then
        echo "✅ Script is executable"
    else
        echo "❌ Script is not executable"
    fi
    
    # Test 3: Quick dry run (show help or version)
    echo ""
    echo "Test 3: Testing command execution..."
    echo "   Running: dj-r3x --help"
    dj-r3x --help 2>/dev/null || {
        echo "   (Note: --help may not be implemented, this is normal)"
    }
    
else
    echo "❌ 'dj-r3x' command not found"
    echo ""
    echo "Troubleshooting:"
    echo "1. Run the setup script: ./setup-dj-r3x-command.sh"
    echo "2. If installed to ~/.local/bin, make sure it's in your PATH:"
    echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "3. If using alias method, restart your terminal or source your shell config"
fi

# Test 4: Check project structure
echo ""
echo "Test 4: Checking project structure..."
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if [ -d "$SCRIPT_DIR/cantina_os" ]; then
    echo "✅ cantina_os directory found"
else
    echo "❌ cantina_os directory missing"
fi

if [ -f "$SCRIPT_DIR/launch-dj-r3x.sh" ]; then
    echo "✅ launch-dj-r3x.sh found"
else
    echo "❌ launch-dj-r3x.sh missing"
fi

# Test 5: Check virtual environment
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "✅ Virtual environment found at ./venv"
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "✅ Virtual environment found at ./.venv"
else
    echo "⚠️  No virtual environment found (will use system Python)"
fi

echo ""
echo "🎯 Setup test complete!" 