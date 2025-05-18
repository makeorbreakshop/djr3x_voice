#!/bin/bash
# Run optimized vector creation script with controlled rate limits

# Create logs directory if it doesn't exist
mkdir -p logs

# Run the script with optimized parameters
python scripts/create_vectors_optimized.py \
  --input-dir data/processed_articles \
  --output-dir data/vectors_optimized \
  --optimized-dir data/vectors_optimized \
  --batch-size 50 \
  --concurrent-requests 10 \
  --embedding-batch-size 50 \
  --max-tokens-per-minute 800000 \
  --rate-limit-delay 0.1 \
  | tee logs/vector_creation_$(date +%Y%m%d%H%M%S).log

echo "Vector creation process completed!" 