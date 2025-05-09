#!/bin/bash

# DJ R3X Voice launcher script
# This script launches the DJ R3X Voice program from any directory

# Store the actual path to the DJ-R3X Voice installation
DJ_R3X_PATH="/Users/brandoncullum/DJ-R3X Voice"

# Navigate to the installation directory
cd "$DJ_R3X_PATH" || { echo "Error: Could not navigate to $DJ_R3X_PATH"; exit 1; }

# Launch the program
echo "Starting DJ R3X Voice..."
python -m cantina_os.cantina_os.main

# This line will only be reached when the program exits
echo "DJ R3X Voice has been shut down." 