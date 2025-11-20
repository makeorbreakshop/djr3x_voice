# MEGA 2560 Serial Buffer Upgrade Instructions

## You're using a MEGA 2560 with 8KB RAM but still have 64-byte buffers!

### Quick Fix (Add to your sketch):

Add this at the very TOP of your `rex_face_ws2812b.ino` file, BEFORE any includes:

```cpp
// MEGA 2560 Buffer Size Override
#define SERIAL_RX_BUFFER_SIZE 256
#define SERIAL_TX_BUFFER_SIZE 256
```

### Permanent Fix (Better):

1. Find your Arduino installation folder
2. Navigate to: `hardware/arduino/avr/cores/arduino/`
3. Edit `HardwareSerial.h`
4. Find these lines (around line 60-70):

```cpp
#if ((RAMEND - RAMSTART) < 1023)
#define SERIAL_TX_BUFFER_SIZE 16
#else
#define SERIAL_TX_BUFFER_SIZE 64  // <- Change to 256
#endif
```

Change to:

```cpp
#if ((RAMEND - RAMSTART) < 1023)
#define SERIAL_TX_BUFFER_SIZE 16
#elif defined(__AVR_ATmega2560__)
#define SERIAL_TX_BUFFER_SIZE 256  // MEGA gets 256!
#define SERIAL_RX_BUFFER_SIZE 256
#else
#define SERIAL_TX_BUFFER_SIZE 64
#endif
```

### Why This Works:

- MEGA 2560 has 8KB SRAM (vs 2KB on Uno)
- Can easily handle 256-byte buffers (only uses 512 bytes total)
- Still leaves 7.5KB for your program!
- 4x larger buffer = way less overflow

### Also Consider:

Since you have 4 serial ports on the MEGA:
- Use Serial (Serial0) for debugging
- Use Serial1 for Python commands
- Keep them separate to avoid conflicts!

In your Arduino code:
```cpp
void setup() {
  Serial.begin(115200);   // USB debugging
  Serial1.begin(115200);  // Python commands (pins 18-19)
}

void loop() {
  if (Serial1.available()) {
    // Read Python commands from Serial1
  }
}
```

In Python, connect to the right port (will show as same USB device).