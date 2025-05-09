# DJ R3X Ultra-Simple Eyes Controller

This is an ultra-simple implementation of the DJ R3X LED eye animation system designed for maximum reliability with the Arduino Mega hardware.

## Overview

The system uses:
- **Arduino Mega 2560** with MAX7219 LED matrices for the eyes
- **Single-character command protocol** for reliable communication
- **Minimal memory footprint** with no dependencies beyond the LedControl library
- **Simple animations** for key emotional states

## Directory Structure

```
cantina_os/
├── arduino/
│   └── rex_eyes_v2/
│       └── rex_eyes_v2.ino       # Arduino sketch
└── python/
    ├── simple_eyes_control.py    # Main control library
    └── test_eyes_patterns.py     # Pattern test script
```

## Hardware Setup

1. Connect your Arduino Mega to two MAX7219 8x8 LED matrices:
   - **DIN** = Pin 51
   - **CLK** = Pin 52
   - **CS** = Pin 53
   - First matrix is the left eye (device 0)
   - Second matrix is the right eye (device 1)

2. Upload the `rex_eyes_v2.ino` sketch to your Arduino Mega

## Command Protocol

The communication protocol is designed to be extremely simple and reliable:

### Commands (sent from Python to Arduino):
- **I** = IDLE pattern (3x3 grid)
- **S** = SPEAKING pattern (vertical animation)
- **T** = THINKING pattern (rotating dot)
- **L** = LISTENING pattern (pulsing animation)
- **H** = HAPPY pattern (upward curves)
- **D** = SAD pattern (downward curves)
- **A** = ANGRY pattern (angled lines)
- **R** = RESET
- **0-9** = Brightness levels

### Responses (sent from Arduino to Python):
- **+** = Success
- **-** = Error

## Python Usage

### Basic Usage

```python
# Import the controller
from simple_eyes_control import SimpleEyesController

# Connect to the Arduino
controller = SimpleEyesController('/dev/ttyACM0')  # Replace with your port
controller.connect()

# Set patterns
controller.set_pattern('idle')
controller.set_pattern('speaking')
controller.set_pattern('thinking')
controller.set_pattern('listening')
controller.set_pattern('happy')
controller.set_pattern('sad')
controller.set_pattern('angry')

# Set brightness (0-9)
controller.set_brightness(5)

# Reset eyes
controller.set_pattern('reset')

# Disconnect when done
controller.disconnect()
```

### Command Line Interface

```bash
# Interactive mode
python simple_eyes_control.py -p /dev/ttyACM0 --interactive

# Set a specific pattern
python simple_eyes_control.py -p /dev/ttyACM0 --pattern listening

# Set brightness
python simple_eyes_control.py -p /dev/ttyACM0 --brightness 5

# Test all patterns
python test_eyes_patterns.py -p /dev/ttyACM0
```

## Integration with CantinaOS

To integrate with the CantinaOS system:

1. Use the SimpleEyesController class within your EyeLightControllerService
2. Map system states to eye patterns:
   - IDLE → 'idle'
   - LISTENING → 'listening'
   - PROCESSING → 'thinking'
   - SPEAKING → 'speaking'
   - etc.
3. Add proper connection management and error recovery

## Troubleshooting

If you encounter issues:

1. **Connection problems**:
   - Check that the port is correct
   - Make sure the Arduino is powered up and the sketch is uploaded
   - Try resetting the Arduino

2. **Command failures**:
   - Ensure proper wiring between Arduino and LED matrices
   - Check that commands are sent as single characters
   - Verify the serial baud rate is set to 115200

3. **Animation issues**:
   - Reset the eyes using the 'R' command
   - Set brightness to a higher value if LEDs are too dim

## Custom Modifications

To add new patterns:
1. Add a new case in the `setPattern()` function in the Arduino sketch
2. Add a corresponding entry in the pattern_map in simple_eyes_control.py
3. Update any animation logic in `updateEyeAnimation()` if needed 