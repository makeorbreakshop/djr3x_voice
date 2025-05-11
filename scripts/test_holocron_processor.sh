#!/bin/bash

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=============================================${NC}"
echo -e "${YELLOW}       HOLOCRON TEST PROCESSING SCRIPT       ${NC}"
echo -e "${YELLOW}=============================================${NC}"

# Check if required environment variables are set
if [ -z "$OPENAI_API_KEY" ]; then
  echo -e "${RED}Error: OPENAI_API_KEY is not set${NC}"
  echo "Please set your OpenAI API key in the .env file or environment"
  exit 1
fi

# Create required directories
mkdir -p logs
mkdir -p data/vectors
mkdir -p data/checkpoints

echo -e "\n${GREEN}Step 1: Running local processor test${NC}"
python scripts/holocron_local_processor.py --test --workers 3 --requests-per-minute 60 --batch-size 1

# Check if the test was successful
if [ $? -ne 0 ]; then
  echo -e "${RED}Error: Local processor test failed${NC}"
  exit 1
fi

# Check if Pinecone upload should be tested
if [ -n "$PINECONE_API_KEY" ]; then
  echo -e "\n${GREEN}Step 2: Testing Pinecone upload${NC}"
  python scripts/upload_to_pinecone.py --test
  
  if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Pinecone upload test failed${NC}"
    exit 1
  fi
else
  echo -e "\n${YELLOW}Skipping Pinecone upload test - PINECONE_API_KEY not set${NC}"
fi

echo -e "\n${GREEN}Test completed successfully!${NC}"
echo -e "${GREEN}You can now run the full processing with:${NC}"
echo -e "  python scripts/holocron_local_processor.py --workers 5 --requests-per-minute 60 --batch-size 10 --limit 100"
echo -e "${GREEN}And upload to Pinecone with:${NC}"
echo -e "  python scripts/upload_to_pinecone.py --batch-size 100"

exit 0 