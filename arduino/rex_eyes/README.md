# DJ R3X Eyes Controller

Arduino sketch for controlling DJ R3X's LED matrix eyes. This controller receives commands over serial and drives two 8x8 LED matrix displays for the eyes.

## Hardware Requirements

- Arduino board (tested with Arduino Mega)
- 2x 8x8 LED Matrix displays with MAX7219 driver
- USB cable for programming and serial communication

## Wiring

Connect the LED matrices to:
- DIN (Data In): Pin 51
- CLK (Clock): Pin 52
- CS (Chip Select): Pin 53

The two matrices should be daisy-chained, with the first matrix being the left eye (device 0) and the second matrix being the right eye (device 1).

## Dependencies

Required Arduino libraries:
- LedControl (for MAX7219 LED matrix control)
- ArduinoJson (version 6.x)

Install these through the Arduino Library Manager.

## Protocol

The controller accepts JSON commands over serial at 115200 baud. Command format:

```json
{
  "pattern": "string",  // Animation pattern name
  "emotion": "string"   // Optional: Emotion type
}
```

### Animation Patterns

- `idle`: Full 3x3 grid
- `listening`: Full 3x3 grid with animation
- `processing`: Rotating center column
- `speaking`: Vertical bounce animation
- `startup`: Expanding animation
- `shutdown`: Contracting animation
- `error`: Blinking X pattern

### Display Area

Each eye uses a 3x3 grid centered at position (3,3) in the 8x8 matrix. The animations are designed to work within this visible area.

## Example Commands

```json
{"pattern": "idle"}
{"pattern": "speaking"}
{"pattern": "error"}
```

## Serial Protocol

The controller responds to commands with acknowledgments in the format:
```
ACK:pattern_name
```

On startup, the controller sends:
```
Arduino Eyes Starting...
READY
``` 