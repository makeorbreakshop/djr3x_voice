# DJ-R3X Voice Assistant

A voice-first "mini-assistant" that listens, thinks, and speaks back in a DJ R3X-inspired voice from Star Wars.

## Overview

DJ-R3X Voice Assistant is a Python application that creates an interactive Star Wars droid DJ experience with:

- **Voice Recognition**: Listens for commands using SpeechRecognition and Whisper
- **AI Processing**: Generates DJ R3X-style responses with OpenAI's GPT-4o
- **Text-to-Speech**: Converts responses to lifelike speech using ElevenLabs
- **LED Animation**: Synchronizes eye/mouth animations with speech via Arduino
- **Music Management**: Plays background music that automatically ducks during speech

The project uses a modern event-driven CantinaOS architecture for reliable, scalable voice assistant functionality.

## 🎛️ Web Dashboard

**Quick Start Dashboard:**
```bash
./start-dashboard.sh    # Start everything
./stop-dashboard.sh     # Stop everything
```

**Dashboard URL:** http://localhost:3000

The dashboard provides real-time monitoring and control with:
- **MONITOR**: Service status, audio spectrum, transcription feed
- **VOICE**: Recording controls, processing pipeline status
- **MUSIC**: Library browser, playback controls, queue management  
- **DJ MODE**: Auto-transitions, commentary monitoring, crossfade control
- **SYSTEM**: Service health, event logs, performance metrics

See [DASHBOARD_SETUP.md](DASHBOARD_SETUP.md) for detailed setup instructions.

## Hardware Configuration

### Components

The DJ-R3X Voice Assistant utilizes the following hardware components:

1. **Arduino Mega 2560 R3**:
   - Main microcontroller for LED matrix control
   - Connected via USB to the host computer
   - Serial communication at 115200 baud rate

2. **MAX7219 LED Matrix Modules** (2):
   - Single color (typically red) 8x8 LED matrix modules
   - Connected to Arduino for eyes visualization
   - Wiring:
     - VCC: 5V from Arduino
     - GND: Ground from Arduino
     - DIN: Pin 51 on Arduino
     - CS: Pin 53 on Arduino
     - CLK: Pin 52 on Arduino

3. **Computer with Microphone**:
   - For voice input and processing
   - Required for running the Python software
   - Connects to Arduino via USB

4. **Speaker System**:
   - For voice output and music playback
   - Connected to the computer

### Wiring Diagram

```
Arduino Mega 2560 R3       MAX7219 LED Matrices (2)
+----------------+         +------------------+
|                |         |                  |
| Pin 51 (DIN) --|---------|DIN               |
| Pin 52 (CLK) --|---------|CLK               |
| Pin 53 (CS)  --|---------|CS                |
| 5V           --|---------|VCC               |
| GND          --|---------|GND               |
|                |         |                  |
+----------------+         +------------------+
       |
       | USB
       |
+----------------+
|    Computer    |
+----------------+
```

The two MAX7219 modules are daisy-chained together, with the first module controlling the left eye and the second module controlling the right eye.

### LED Matrix Configuration

Each 8x8 LED matrix represents one eye of DJ-R3X:
- Center position is at (3,3) for each matrix
- Animations typically use a 3x3 grid centered at this position
- Different animation patterns represent different states (idle, listening, speaking, etc.)
- LED intensity is configurable in the Arduino code

## Setup Instructions

### 1. Install Dependencies
Install all required Python packages:
```bash
python3 -m pip install -r requirements.txt
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

### 5. Arduino Setup

1. Connect the Arduino Mega 2560 to your computer via USB
2. Connect the MAX7219 LED matrix modules following the wiring diagram
3. Upload the `arduino/rex_eyes/rex_eyes.ino` sketch to the Arduino using the Arduino IDE
4. Verify the serial connection is working (Arduino will output "READY" when initialized)
5. Note the serial port being used (typically something like `/dev/ttyACM0` on Linux, `/dev/tty.usbmodem*` on macOS, or `COM*` on Windows)
6. Update the `.env` file with your Arduino serial port:
   ```
   LED_SERIAL_PORT=/dev/ttyACM0  # Replace with your actual port
   LED_BAUD_RATE=115200
   ```

### 6. Run the Program

#### Legacy Version
Run the original monolithic version using:

```bash
python3 run_rex.py
```

#### MVP Architecture Version
Run the modern event-driven MVP architecture version (recommended):

```bash
python3 run_r3x_mvp.py
```

You can also use these additional options:
```bash
# Run in demo mode with predefined interactions
python3 run_r3x_mvp.py --demo

# Play background music during operation
python3 run_r3x_mvp.py --music path/to/music.mp3

# Run in test mode (no API keys required)
python3 run_r3x_mvp.py --test
```

### Test Mode
The application includes a test mode that allows you to run the system without requiring API keys or external hardware. This is useful for development, testing, and demonstration purposes.

In test mode:
- Voice responses use pre-defined test responses instead of calling OpenAI
- Speech synthesis generates simple audio patterns instead of using ElevenLabs
- LED control gracefully handles missing Arduino connections
- Music playback works if VLC is installed, but is optional
- On macOS, VLC must be installed via `brew install --cask vlc` for proper integration

To run in test mode:
```bash
python3 run_r3x_mvp.py --test
```

You can combine test mode with other flags:
```bash
# Run demo sequence in test mode
python3 run_r3x_mvp.py --test --demo
```

## MVP Architecture

The MVP architecture implements an event-driven design with the following components:

1. **Event Bus** (`src/bus.py`) - Core communication system that enables all components to interact via events
2. **Voice Manager** (`src/voice_manager.py`) - Manages voice interaction pipeline:
   - Speech recognition (using SpeechRecognition and WhisperManager)
   - Text processing (via OpenAI API)
   - Speech synthesis (via ElevenLabs API)
3. **LED Manager** (`src/led_manager.py`) - Controls Arduino-connected LED matrices:
   - Updates animations based on system state
   - Synchronizes mouth movement with speech amplitude
   - Provides visual feedback during different interaction phases
4. **Music Manager** (`src/music_manager.py`) - Handles background music features:
   - Plays background tracks with VLC
   - Implements auto-ducking (lowering volume) during speech
   - Manages music transitions and playlist features

### Events System
The system uses these key events for inter-component communication:
- `voice.listening_started/stopped` - Speech recording state
- `voice.processing_started` - Voice is being transcribed/processed
- `voice.speaking_started` - Speech synthesis begins
- `voice.beat` - Emitted ~50 times per second with amplitude data during speech
- `voice.speaking_finished` - Speech completes
- `music.track_started` - New background music track begins
- `music.volume_ducked/restored` - Volume state changes
- `system.error` - Error handling events

### Resource Management
The MVP architecture provides proper resource cleanup, ensuring all components are gracefully shut down when the application exits, addressing these key areas:
- Serial port connections for LED control
- Audio playback resources
- Background tasks
- Event handlers

## Troubleshooting

- If you see an error about `ELEVENLABS_API_KEY not found in .env file`, make sure your .env file exists and contains the correct API key.
- If PyAudio installation fails, make sure you have installed PortAudio first.
- If you have issues with audio playback, make sure pygame is installed correctly.
- Check that your microphone is working properly for speech recognition.
- For Arduino LED connection issues, verify the correct serial port in the error messages.
- If the Arduino code fails to compile, ensure you have the LedControl library installed. You can install it through Arduino IDE's Library Manager.

## Features

### Voice Recognition
- Uses offline Whisper model for speech-to-text (no internet required for transcription)
- Supports push-to-talk mode (toggle with spacebar) or automatic voice detection
- Provides visual and audio feedback during listening state

### AI Processing
- Processes speech input using OpenAI's GPT-4o model
- Maintains DJ R3X character personality across interactions
- Provides context-aware responses in DJ R3X's distinctive style

### Speech Synthesis
- Converts responses to lifelike speech using ElevenLabs
- Supports customized voice settings via configuration
- Optional audio processing for enhanced output quality

### LED Animation
- Synchronizes LED eye/mouth animations with speech
- Different patterns for idle, listening, processing, and speaking states
- Visual feedback coordinated with audio through event system

### Music Management
- Background music playback with automatic ducking during speech
- Support for playlists and random track selection
- Smooth volume transitions during speech interactions

## Usage

### Interactive Mode
Run the MVP version and interact through the command line:

```bash
python3 run_r3x_mvp.py
```

Available commands:
- Type any text to have DJ R3X respond to it
- `speak <text>` - Generate and speak a response to text
- `music <file>` - Play a background music file
- `stop` - Stop music playback
- `duck` - Duck music volume manually
- `restore` - Restore music volume manually
- `exit` or `quit` - Exit the program
- `help` - Show command help

### Demo Mode
Run a demonstration with predefined interactions:

```bash
python3 run_r3x_mvp.py --demo
```

## Project Structure

### Core Files
- `run_r3x_mvp.py`: Launcher for the MVP architecture
- `src/main.py`: Main application entry point and component coordinator
- `src/bus.py`: Event bus implementation for inter-component communication
- `src/voice_manager.py`: Speech processing and synthesis pipeline
- `src/led_manager.py`: LED animation control and visual feedback
- `src/music_manager.py`: Music playback and volume management 
- `whisper_manager.py`: Local speech-to-text processing
- `audio_processor.py`: Audio processing utilities

### Legacy Files
- `run_rex.py`: Wrapper for the original monolithic application
- `rex_talk.py`: Original application with all functionality in one file

### Configuration
- `config/`: Configuration files for various components
- `env.example`: Template for creating your `.env` file

### Testing
- Various test scripts for different components

## License

This project is for personal use and entertainment purposes. Star Wars and DJ R3X are trademarks of Disney/Lucasfilm. 