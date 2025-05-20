#!/bin/bash

echo "Fixing DJ-R3X command..."

# Create the dj-r3x script
cat > /tmp/dj-r3x << EOF
#!/bin/bash

# DJ R3X Voice launcher script
# This script launches the DJ R3X Voice program from any directory

# Store the actual path to the DJ-R3X Voice installation
DJ_R3X_PATH="/Users/brandoncullum/DJ-R3X Voice"

# Navigate to the installation directory
cd "\$DJ_R3X_PATH" || { echo "Error: Could not navigate to \$DJ_R3X_PATH"; exit 1; }

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Launch the program - use the cantina_os module
echo "Starting DJ R3X Voice..."
python -m cantina_os.cantina_os.main \$@

# This line will only be reached when the program exits
echo "DJ R3X Voice has been shut down."
EOF

# Make the temporary script executable
chmod +x /tmp/dj-r3x

# Copy it to /usr/local/bin
sudo cp /tmp/dj-r3x /usr/local/bin/dj-r3x
sudo chmod +x /usr/local/bin/dj-r3x

echo "DJ-R3X command fixed successfully! You can now run 'dj-r3x' from any terminal." 