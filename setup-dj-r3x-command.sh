#!/bin/bash

# DJ R3X Voice Command Setup Script
# This script creates a portable 'dj-r3x' command that works from any directory

set -e  # Exit on any error

echo "🤖 Setting up DJ-R3X command..."

# Get the absolute path of the project directory (where this script is located)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo "📂 Project directory: $SCRIPT_DIR"

# Check if we're in the right directory (should have cantina_os folder)
if [ ! -d "$SCRIPT_DIR/cantina_os" ]; then
    echo "❌ Error: cantina_os directory not found in $SCRIPT_DIR"
    echo "   Make sure you're running this script from the djr3x_voice project root."
    exit 1
fi

# Detect the correct Python command
PYTHON_CMD=""
if command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
else
    echo "❌ Error: No Python interpreter found!"
    echo "   Please install Python first."
    exit 1
fi

echo "🐍 Found Python: $PYTHON_CMD"

# Check if virtual environment exists
VENV_PATH=""
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "✅ Virtual environment found at $SCRIPT_DIR/venv"
    VENV_PATH="$SCRIPT_DIR/venv"
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "✅ Virtual environment found at $SCRIPT_DIR/.venv"
    VENV_PATH="$SCRIPT_DIR/.venv"
else
    echo "⚠️  No virtual environment found."
    read -p "   Create a virtual environment? [Y/n]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        echo "🔨 Creating virtual environment..."
        $PYTHON_CMD -m venv "$SCRIPT_DIR/venv"
        VENV_PATH="$SCRIPT_DIR/venv"
        echo "✅ Virtual environment created at $SCRIPT_DIR/venv"
        
        # Install dependencies
        echo "📦 Installing dependencies..."
        source "$VENV_PATH/bin/activate"
        pip install --upgrade pip
        
        if [ -f "$SCRIPT_DIR/cantina_os/requirements.txt" ]; then
            echo "   Installing from cantina_os/requirements.txt..."
            pip install -r "$SCRIPT_DIR/cantina_os/requirements.txt"
        elif [ -f "$SCRIPT_DIR/requirements.txt" ]; then
            echo "   Installing from requirements.txt..."
            pip install -r "$SCRIPT_DIR/requirements.txt"
        else
            echo "⚠️  No requirements.txt found, skipping dependency installation"
        fi
        echo "✅ Dependencies installed"
    else
        echo "⚠️  Proceeding without virtual environment (may use system Python)"
    fi
fi

# Create the portable dj-r3x launcher script
cat > /tmp/dj-r3x << EOF
#!/bin/bash

# DJ R3X Voice launcher script (Auto-generated)
# This script launches the DJ R3X Voice program from any directory
# Generated on: $(date)
# Project path: $SCRIPT_DIR

# Store the actual path to the DJ-R3X Voice installation
DJ_R3X_PATH="$SCRIPT_DIR"

# Navigate to the cantina_os directory
cd "\$DJ_R3X_PATH/cantina_os" || {
    echo "❌ Error: Could not navigate to \$DJ_R3X_PATH/cantina_os"
    echo "   The DJ R3X project may have been moved or deleted."
    exit 1
}

# Activate virtual environment if it exists
if [ -n "$VENV_PATH" ] && [ -d "$VENV_PATH" ]; then
    echo "🔄 Activating virtual environment..."
    source "$VENV_PATH/bin/activate"
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

echo "🐍 Using Python: \$PYTHON_CMD"

# Launch the program
echo "🚀 Starting DJ R3X Voice..."
\$PYTHON_CMD -m cantina_os.main "\$@"

# This line will only be reached when the program exits
echo "👋 DJ R3X Voice has been shut down."
EOF

# Make the script executable
chmod +x /tmp/dj-r3x

# Try different installation methods based on system and permissions
echo ""
echo "🔧 Installing dj-r3x command..."

# Method 1: Try /usr/local/bin (requires sudo)
if command -v sudo >/dev/null 2>&1; then
    echo "📋 Option 1: Install to /usr/local/bin (system-wide, requires password)"
    read -p "   Install system-wide? [Y/n]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
        echo "🔑 Installing to /usr/local/bin (requires password)..."
        if sudo mv /tmp/dj-r3x /usr/local/bin/dj-r3x; then
            echo "✅ DJ-R3X command installed to /usr/local/bin/dj-r3x"
            echo "   You can now run 'dj-r3x' from any terminal!"
            exit 0
        else
            echo "❌ Failed to install to /usr/local/bin"
        fi
    fi
fi

# Method 2: Try user's local bin directory
mkdir -p "$HOME/.local/bin"
if cp /tmp/dj-r3x "$HOME/.local/bin/dj-r3x"; then
    echo "✅ DJ-R3X command installed to $HOME/.local/bin/dj-r3x"
    
    # Check if ~/.local/bin is in PATH
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo ""
        echo "⚠️  Note: $HOME/.local/bin is not in your PATH"
        echo "   Add this line to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
        echo "   export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo ""
        echo "   Or run this command now:"
        echo "   echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.$(basename $SHELL)rc"
    else
        echo "   You can now run 'dj-r3x' from any terminal!"
    fi
    rm -f /tmp/dj-r3x
    exit 0
fi

# Method 3: Create shell alias
echo ""
echo "📝 Alternative: Creating shell alias..."
SHELL_RC=""
if [ "$SHELL" = "/bin/zsh" ] || [ "$SHELL" = "/usr/bin/zsh" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ "$SHELL" = "/bin/bash" ] || [ "$SHELL" = "/usr/bin/bash" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ]; then
    echo "alias dj-r3x='$SCRIPT_DIR/launch-dj-r3x.sh'" >> "$SHELL_RC"
    echo "✅ Alias added to $SHELL_RC"
    echo "   Restart your terminal or run: source $SHELL_RC"
    echo "   Then you can run 'dj-r3x' from any terminal!"
else
    echo "❌ Could not determine shell configuration file"
    echo "   Manual setup: Add this alias to your shell profile:"
    echo "   alias dj-r3x='$SCRIPT_DIR/launch-dj-r3x.sh'"
fi

# Cleanup
rm -f /tmp/dj-r3x

echo ""
echo "🎉 Setup complete! DJ R3X command is ready to use." 