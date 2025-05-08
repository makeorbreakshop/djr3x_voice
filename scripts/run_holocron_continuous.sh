#!/bin/bash

while true; do
    python scripts/run_holocron_pipeline_v3.py --workers 10 --batch-size 1000 --requests-per-minute 3000
    sleep 5  # Small delay between runs to prevent hammering the system
done 