# DJ-R3X Voice Assistant

A voice-first "mini-assistant" that listens, thinks, and speaks back in a DJ R3X-inspired voice from Star Wars.

## Setup Instructions

### 1. Install Dependencies
Install all required Python packages:
```bash
python3 -m pip install -r requirements.txt
python3 -m pip install pygame
```

If you encounter issues with PyAudio installation, you may need to install PortAudio first:
```bash
brew install portaudio  # For macOS
```

### 2. API Keys Setup
You need to set up the following API keys:

#### ElevenLabs API Key
1. Go to https://elevenlabs.io and create an account
2. Go to https://elevenlabs.io/app
3. Click on your profile icon in the top right
4. Select 'Profile' or 'API Key'
5. Copy one of your API keys

You can also use the helper script to test your ElevenLabs API key:
```bash
python3 get_new_elevenlabs_key.py
```

#### ElevenLabs Voice ID
1. After getting your API key, go to https://elevenlabs.io/voice-library
2. Find or create a voice you want to use
3. Click on the voice
4. Copy the Voice ID from the URL (the string after /voice-lab/)

#### OpenAI API Key
1. Go to https://platform.openai.com/api-keys
2. Create a new API key
3. Copy the API key

### 3. Create a .env File
Create a file named `.env` in the same directory with the following contents:

```
# API Keys
OPENAI_API_KEY=your_openai_api_key
ELEVENLABS_API_KEY=your_elevenlabs_api_key

# ElevenLabs Voice ID
ELEVENLABS_VOICE_ID=your_elevenlabs_voice_id_here

# OpenAI Model Configuration
OPENAI_MODEL=gpt-4o

# Operation Modes
TEXT_ONLY_MODE=false
DISABLE_AUDIO_PROCESSING=false

# Personality Configuration
DJ_R3X_PERSONA="You are DJ R3X, a droid DJ from Star Wars. You have an upbeat, quirky personality. You occasionally use sound effect words like 'BZZZT!' and 'WOOP!' You like to keep responses brief and entertaining. You love music and Star Wars."
```

Replace the placeholder values with your actual API keys and voice ID.

### 4. Test ElevenLabs API
Before running the main program, you can test if your ElevenLabs API connection is working:

```bash
python3 test_elevenlabs_rest.py
```

### 5. Run the Program
Run the program using:

```bash
python3 run_rex.py
```

The program will:
1. Listen to your voice
2. Convert speech to text
3. Generate a response using OpenAI
4. Convert the response to speech using ElevenLabs
5. Play the audio response

## Troubleshooting

- If you see an error about `ELEVENLABS_API_KEY not found in .env file`, make sure your .env file exists and contains the correct API key.
- If PyAudio installation fails, make sure you have installed PortAudio first.
- If you have issues with audio playback, make sure pygame is installed correctly.
- Check that your microphone is working properly for speech recognition.

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

   # Operation Modes
   TEXT_ONLY_MODE=false
   DISABLE_AUDIO_PROCESSING=false

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

### Operation Modes

You can configure the following operation modes in your `.env` file:

1. **TEXT_ONLY_MODE**: Set to `true` to disable voice output and only show text responses
   ```
   TEXT_ONLY_MODE=true
   ```

2. **DISABLE_AUDIO_PROCESSING**: Set to `true` to disable the audio effects processing
   ```
   DISABLE_AUDIO_PROCESSING=true
   ```
   When this option is enabled, the app will use the raw ElevenLabs audio without applying the robotic voice effects. This is useful for debugging or when you prefer the unmodified ElevenLabs voice.

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