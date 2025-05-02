# DJ-R3X Voice Assistant

A voice-activated assistant with the personality of DJ R3X, the droid DJ from Star Wars. This Python-based application listens to your voice, processes your requests through OpenAI, and responds with DJ R3X's quirky, upbeat personality using ElevenLabs' text-to-speech technology.

## Features

- **Voice Recognition**: Listens to your voice commands using the SpeechRecognition library
- **AI Processing**: Uses OpenAI's GPT-4o model to generate DJ R3X-style responses
- **Text-to-Speech**: Converts responses to lifelike speech using ElevenLabs' voice synthesis API
- **Interactive Experience**: Press ENTER to start talking, then listen to DJ R3X's response

## Requirements

- Python 3.7+
- OpenAI API key
- ElevenLabs API key
- Microphone for voice input

## Installation

1. Clone this repository or download the source files

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

   Dependencies include:
   - SpeechRecognition
   - pyaudio
   - openai
   - elevenlabs
   - python-dotenv
   - colorama
   - pygame

3. Create an `.env` file by copying the template from `env.example`:
   ```bash
   cp env.example .env
   ```

4. Update the `.env` file with your own API keys:
   ```
   # API Keys
   OPENAI_API_KEY=your_openai_api_key
   ELEVENLABS_API_KEY=your_elevenlabs_api_key

   # ElevenLabs Voice ID
   ELEVENLABS_VOICE_ID=P9l1opNa5pWou2X5MwfB

   # OpenAI Model Configuration
   OPENAI_MODEL=gpt-4o

   # Personality Configuration
   DJ_R3X_PERSONA="You are DJ R3X, a droid DJ from Star Wars. You have an upbeat, quirky personality. You occasionally use sound effect words like 'BZZZT!' and 'WOOP!' You like to keep responses brief and entertaining. You love music and Star Wars."
   ```

## Configuration

### API Keys

1. **OpenAI API Key**:
   - Sign up at [OpenAI](https://platform.openai.com/)
   - Create an API key in your account
   - Add it to the `.env` file

2. **ElevenLabs API Key**:
   - Sign up at [ElevenLabs](https://elevenlabs.io/)
   - Go to your profile settings
   - Generate an API key (starts with `sk_`)
   - Add it to the `.env` file

### Voice Configuration

The default configuration uses a pre-selected ElevenLabs voice ID for DJ R3X. You can customize this by:

1. Creating your own voice in the ElevenLabs platform
2. Replacing the `ELEVENLABS_VOICE_ID` in the `.env` file

## Usage

Run the assistant using:

```bash
python run_rex.py
```

Instructions:
1. The assistant will start and prompt you to press ENTER to begin speaking
2. After pressing ENTER, start speaking your question or request
3. The assistant will process your input and respond as DJ R3X
4. Audio will play with DJ R3X's voice
5. Press ENTER again to speak more

## Troubleshooting

### API Key Issues

- **ElevenLabs API Key**: Make sure your ElevenLabs API key starts with `sk_` and matches the exact format from your ElevenLabs dashboard
- **OpenAI API Key**: Ensure your OpenAI API key has sufficient credits and permissions

### Audio Issues

- Make sure your microphone is properly connected and has permission to be accessed
- Check that your speakers or headphones are working correctly
- Install additional codecs if audio playback is not working

### Voice Recognition Problems

- Speak clearly and at a moderate pace
- Reduce background noise when using the assistant
- Make sure the microphone is not muted

## Testing

The repository includes two test scripts:

1. `test_elevenlabs.py`: Tests the ElevenLabs Python SDK integration
2. `test_elevenlabs_rest.py`: Tests direct REST API calls to ElevenLabs

Run these to verify your API keys and configuration:

```bash
python test_elevenlabs_rest.py
```

## Project Structure

- `run_rex.py`: Wrapper script that sets the API key and launches the main application
- `rex_talk.py`: Main application with speech recognition, AI processing, and voice synthesis
- `.env`: Configuration file for API keys and parameters
- `test_elevenlabs.py`: Test script for the ElevenLabs SDK
- `test_elevenlabs_rest.py`: Test script for the ElevenLabs REST API
- `get_new_elevenlabs_key.py`: Utility script to help set up a new ElevenLabs API key

## License

This project is for personal use and entertainment purposes. Star Wars and DJ R3X are trademarks of Disney/Lucasfilm. 