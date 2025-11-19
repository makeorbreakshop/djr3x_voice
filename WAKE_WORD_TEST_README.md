# DJ Rex Wake Word Test

This is a standalone test for the Porcupine wake word detection using the custom "DJ Rex" keyword.

## Prerequisites

1. **Picovoice Access Key**: You need to get a free access key from Picovoice Console
   - Go to: https://console.picovoice.ai/
   - Sign up for a free account
   - Copy your Access Key

2. **Add the key to your environment**:
   ```bash
   # Add this line to your .env file:
   PICOVOICE_ACCESS_KEY=your_access_key_here
   ```

3. **Dependencies installed** (already done):
   - `pvporcupine` ✓ (Porcupine wake word engine)
   - `pyaudio` ✓ (Already installed for Deepgram mic input)

## Files

- `test_wake_word.py` - Standalone test script
- `cantina_os/wake_word/DJ-Rex_en_mac_v3_0_0.ppn` - Custom keyword file for "DJ Rex"

## Running the Test

```bash
# Make sure you're in the djr3x_voice directory
cd /Users/brandoncullum/djr3x_voice

# Run the test script
venv/bin/python test_wake_word.py
```

## What the Test Does

1. Loads the Picovoice access key from environment
2. Initializes Porcupine with the custom "DJ Rex" keyword file
3. Starts listening to your microphone
4. Prints a message whenever "DJ Rex" is detected
5. Runs until you press Ctrl+C

## Expected Output

```
Initializing Porcupine with keyword file: cantina_os/wake_word/DJ-Rex_en_mac_v3_0_0.ppn
Access key: AbCdEf1234...

Porcupine initialized successfully!
  - Sample rate: 16000 Hz
  - Frame length: 512 samples
  - Version: 3.0

Listening for wake word 'DJ Rex'...
(Press Ctrl+C to stop)

🎤 WAKE WORD DETECTED: DJ Rex!
    (Detection successful)
```

## Troubleshooting

**Error: PICOVOICE_ACCESS_KEY not found**
- Make sure you added the key to your `.env` file
- OR export it temporarily: `export PICOVOICE_ACCESS_KEY='your_key'`

**Error: Keyword file not found**
- Verify the file exists: `ls -l cantina_os/wake_word/DJ-Rex_en_mac_v3_0_0.ppn`

**No detection when saying "DJ Rex"**
- Speak clearly and at normal volume
- Try different pronunciations: "Dee-Jay Rex" vs "DJ Rex"
- Check microphone permissions in macOS System Preferences

## Next Steps

Once this test works, we can integrate it into CantinaOS as a service that:
1. Listens for "DJ Rex" wake word
2. Emits a `WAKE_WORD_DETECTED` event
3. Triggers the system to start listening for voice commands
