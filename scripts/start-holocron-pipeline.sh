#!/bin/bash
# Start the Holocron pipeline in a detached screen session

# Kill any existing holocron_pipeline screen sessions
screen -X -S holocron_pipeline quit >/dev/null 2>&1

# Make scripts executable
chmod +x scripts/run_holocron_continuously.sh

# Create a new named screen session for the Holocron pipeline
screen -dmS holocron_pipeline bash -c "cd $(pwd) && ./scripts/run_holocron_continuously.sh"

# Only print status if the screen session was created successfully
if screen -ls | grep -q "holocron_pipeline"; then
    echo "Holocron pipeline started successfully"
fi

echo "To view progress: screen -r holocron_pipeline"
echo "To detach from session (keep it running): Ctrl+A then D"
echo "To stop the pipeline: screen -X -S holocron_pipeline quit" 