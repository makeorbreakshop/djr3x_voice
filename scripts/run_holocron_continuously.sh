#!/bin/bash
# Continuous Holocron Pipeline Runner
# This script runs the Holocron pipeline continuously until all URLs are processed

echo "Starting continuous Holocron pipeline processing..."
echo "Press Ctrl+C to stop at any time"

# Directory for continuous logs
mkdir -p logs/continuous

# Log file for this continuous run
LOG_FILE="logs/continuous/holocron_continuous_$(date +%Y%m%d_%H%M%S).log"
echo "Logging continuous process to $LOG_FILE"

# Set optimized parameters based on previous successful runs
LIMIT=100         # URLs per batch (increased from 50)
WORKERS=5         # Number of concurrent workers (increased from 3)
BATCH_SIZE=100    # URLs processed before checkpointing (increased from 10)
RATE_LIMIT=1000   # Requests per minute (increased from 30, MediaWiki allows 3000/min)
PAUSE_SECONDS=2   # Time to pause between runs in seconds (reduced from 5)

# Add the root directory to PYTHONPATH to ensure patches are imported
export PYTHONPATH="$PYTHONPATH:$(pwd)"
echo "Setting PYTHONPATH to include current directory: $PYTHONPATH" | tee -a "$LOG_FILE"

# Run continuously
while true; do
    echo "$(date): Starting pipeline run..." | tee -a "$LOG_FILE"
    
    # Run the Python script
    python scripts/run_holocron_pipeline_v3.py \
        --limit $LIMIT \
        --workers $WORKERS \
        --batch-size $BATCH_SIZE \
        --requests-per-minute $RATE_LIMIT
    
    # Capture exit status
    STATUS=$?
    
    if [ $STATUS -eq 0 ]; then
        echo "$(date): Pipeline run completed successfully" | tee -a "$LOG_FILE"
    else
        echo "$(date): Pipeline run failed with status $STATUS" | tee -a "$LOG_FILE"
        # Optional: Uncomment to stop on failure
        # echo "Stopping continuous run due to error"
        # break
    fi
    
    echo "$(date): Pausing for $PAUSE_SECONDS seconds before next run..." | tee -a "$LOG_FILE"
    sleep $PAUSE_SECONDS
done 