#!/bin/bash

echo "Setting up DJ-R3X command..."

# Get the current directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Create the dj-r3x script in /usr/local/bin (requires sudo)
cat > /tmp/dj-r3x << EOF
#!/bin/bash

# DJ R3X Voice launcher script
# This script launches the DJ R3X Voice program from any directory

# Store the actual path to the DJ-R3X Voice installation
DJ_R3X_PATH="$SCRIPT_DIR"

# Navigate to the installation directory
cd "\$DJ_R3X_PATH" || { echo "Error: Could not navigate to \$DJ_R3X_PATH"; exit 1; }

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Launch the program
echo "Starting DJ R3X Voice..."
python -m cantina_os.cantina_os.main

# This line will only be reached when the program exits
echo "DJ R3X Voice has been shut down."
EOF

# Make the temporary script executable
chmod +x /tmp/dj-r3x

# Use sudo to copy it to /usr/local/bin
echo "The following step requires your password to copy the script to /usr/local/bin"
sudo mv /tmp/dj-r3x /usr/local/bin/dj-r3x

echo "DJ-R3X command installed successfully! You can now run 'dj-r3x' from any terminal." 