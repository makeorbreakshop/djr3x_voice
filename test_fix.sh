#!/bin/bash

# DJ-R3X Voice App - Audio Pipeline Fix Verification Script
# This script runs the test utility to verify the fix for the audio pipeline issue.

echo "🤖 DJ-R3X Voice App - Audio Pipeline Fix Verification"
echo "======================================================"
echo

# Make script executable
chmod +x find_subscription_issues.py

# Step 1: Check if there are similar issues in other services
echo "📊 Step 1: Scanning for similar issues in other services..."
echo
./find_subscription_issues.py --manual
echo

# Step 2: Run the test utility to verify the fix
echo "🧪 Step 2: Testing audio pipeline event flow..."
echo
echo "Running test without Deepgram API (mock mode)..."
python test_audio_pipeline.py
echo

# Step 3: If DEEPGRAM_API_KEY is set, run the test with real API
if [ -n "$DEEPGRAM_API_KEY" ]; then
  echo "🔊 Step 3: Testing with real Deepgram API..."
  echo
  python test_audio_pipeline.py --use-deepgram
else
  echo "ℹ️  Step 3: Skipping real Deepgram API test (no API key found)"
  echo "To run with Deepgram API, set the DEEPGRAM_API_KEY environment variable:"
  echo "export DEEPGRAM_API_KEY=your_api_key_here"
fi

echo
echo "✅ Verification complete!"
echo
echo "If the tests show that audio chunks are correctly being passed between services,"
echo "and no additional subscription issues were found, the fix has been successful."
echo
echo "Next Steps:"
echo "1. If any other services had similar issues, fix them using the same pattern"
echo "2. Run the main application to verify the entire voice pipeline works end-to-end"
echo "3. Document any additional findings in the development log" 