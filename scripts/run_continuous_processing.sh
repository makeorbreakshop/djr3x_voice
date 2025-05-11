#!/bin/bash

# Holocron Continuous Processing Script
# This script continuously runs both the local processor and pinecone uploader
# until all URLs are processed and uploaded.

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to get current timestamp in milliseconds
get_time_ms() {
  echo $(($(date +%s%N)/1000000))
}

# Function to format time difference in a human-readable format
format_time() {
  local ms=$1
  local s=$((ms/1000))
  local m=$((s/60))
  local s=$((s%60))
  local ms=$((ms%1000))
  
  if [ $m -gt 0 ]; then
    echo "${m}m ${s}s ${ms}ms"
  else
    echo "${s}s ${ms}ms"
  fi
}

echo -e "${YELLOW}=============================================${NC}"
echo -e "${YELLOW}    HOLOCRON CONTINUOUS PROCESSING SCRIPT    ${NC}"
echo -e "${YELLOW}=============================================${NC}"

# Check if required environment variables are set
if [ -z "$OPENAI_API_KEY" ]; then
  echo -e "${RED}Error: OPENAI_API_KEY is not set${NC}"
  echo "Please set your OpenAI API key in the .env file or environment"
  exit 1
fi

if [ -z "$PINECONE_API_KEY" ]; then
  echo -e "${RED}Error: PINECONE_API_KEY is not set${NC}"
  echo "Please set your Pinecone API key in the .env file or environment"
  exit 1
fi

# Create required directories
mkdir -p logs
mkdir -p data/vectors
mkdir -p data/checkpoints

# Function to check if there are unprocessed URLs
function check_unprocessed_urls() {
  local start_time=$(get_time_ms)
  local unprocessed=$(python -c "import pandas as pd; df = pd.read_csv('data/processing_status.csv'); print(len(df) - df['is_processed'].sum())")
  local end_time=$(get_time_ms)
  local duration=$((end_time - start_time))
  echo -e "${BLUE}Time to check unprocessed URLs: $(format_time $duration)${NC}" >&2
  echo $unprocessed
}

# Function to check if there are unprocessed vectors
function check_unprocessed_vectors() {
  local start_time=$(get_time_ms)
  cd scripts
  local unprocessed=$(python -c "import glob; import json; import os; status_file = '../data/pinecone_upload_status.json'; all_files = glob.glob('../data/vectors/*.parquet'); status = json.load(open(status_file)) if os.path.exists(status_file) else {}; print(len([f for f in all_files if f not in status]))")
  cd ..
  local end_time=$(get_time_ms)
  local duration=$((end_time - start_time))
  echo -e "${BLUE}Time to check unprocessed vectors: $(format_time $duration)${NC}" >&2
  echo $unprocessed
}

# Process in batches - Optimized for maximum throughput based on dev log history
BATCH_SIZE=100
WORKERS=10
RPM=2500  # 50 requests per second = 3000 per minute (MediaWiki limit according to dev log)

echo -e "\n${GREEN}Starting continuous processing loop...${NC}"
echo "Press Ctrl+C to stop the process at any time"

ITERATION=1

while true; do
  echo -e "\n${YELLOW}============= ITERATION $ITERATION =============${NC}"
  LOOP_START_TIME=$(get_time_ms)
  
  # Check if there are unprocessed URLs
  echo -e "${BLUE}Checking for unprocessed URLs...${NC}"
  UNPROCESSED_URLS=$(check_unprocessed_urls)
  
  if [ "$UNPROCESSED_URLS" -gt 0 ]; then
    echo -e "\n${YELLOW}$(date)${NC} - $UNPROCESSED_URLS unprocessed URLs remaining"
    echo -e "${GREEN}Running local processor...${NC}"
    
    PROCESSOR_START_TIME=$(get_time_ms)
    python scripts/holocron_local_processor.py --limit $BATCH_SIZE --workers $WORKERS --requests-per-minute $RPM "$@"
    PROCESSOR_END_TIME=$(get_time_ms)
    PROCESSOR_DURATION=$((PROCESSOR_END_TIME - PROCESSOR_START_TIME))
    echo -e "${BLUE}Local processor execution time: $(format_time $PROCESSOR_DURATION)${NC}"
    
    # Small pause to let files be written completely
    echo -e "${BLUE}Pausing for 5 seconds to allow file writes to complete...${NC}"
    sleep 5
  else
    echo -e "\n${GREEN}All URLs have been processed!${NC}"
  fi
  
  # Always run the uploader to process any new vectors
  echo -e "${BLUE}Checking for unprocessed vectors...${NC}"
  UNPROCESSED_VECTORS=$(check_unprocessed_vectors)
  
  if [ "$UNPROCESSED_VECTORS" -gt 0 ]; then
    echo -e "\n${YELLOW}$(date)${NC} - $UNPROCESSED_VECTORS vector files to upload"
    echo -e "${GREEN}Running Pinecone uploader...${NC}"
    
    UPLOADER_START_TIME=$(get_time_ms)
    cd scripts
    python upload_to_pinecone.py
    cd ..
    UPLOADER_END_TIME=$(get_time_ms)
    UPLOADER_DURATION=$((UPLOADER_END_TIME - UPLOADER_START_TIME))
    echo -e "${BLUE}Pinecone uploader execution time: $(format_time $UPLOADER_DURATION)${NC}"
    
    # Small pause before next iteration
    echo -e "${BLUE}Pausing for 5 seconds after upload...${NC}"
    sleep 5
  else
    echo -e "\n${GREEN}All vectors have been uploaded!${NC}"
  fi
  
  # If both processing and uploading are complete, exit the loop
  if [ "$UNPROCESSED_URLS" -eq 0 ] && [ "$UNPROCESSED_VECTORS" -eq 0 ]; then
    echo -e "\n${GREEN}Processing completed! All URLs processed and vectors uploaded.${NC}"
    break
  fi
  
  # Small pause before next iteration
  echo -e "${YELLOW}Pausing for 10 seconds before next iteration...${NC}"
  sleep 10
  
  LOOP_END_TIME=$(get_time_ms)
  LOOP_DURATION=$((LOOP_END_TIME - LOOP_START_TIME))
  echo -e "${BLUE}Total time for iteration $ITERATION: $(format_time $LOOP_DURATION)${NC}"
  
  ITERATION=$((ITERATION + 1))
done

echo -e "\n${GREEN}Holocron knowledge processing complete!${NC}"

STATS_START_TIME=$(get_time_ms)
TOTAL_VECTORS=$(python -c "from pinecone import Pinecone; import os; from dotenv import load_dotenv; load_dotenv(); pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY')); index = pc.Index('holocron-knowledge'); print(index.describe_index_stats().total_vector_count)")
STATS_END_TIME=$(get_time_ms)
STATS_DURATION=$((STATS_END_TIME - STATS_START_TIME))

echo -e "${BLUE}Time to get Pinecone stats: $(format_time $STATS_DURATION)${NC}"
echo "Total vectors in Pinecone: $TOTAL_VECTORS" 