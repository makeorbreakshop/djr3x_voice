/*
 * DJ R3X Eyes Controller
 * Controls LED animations for eyes using MAX7219 LED matrices
 */

#include <LedControl.h>

// LED Configuration
#define LEFT_EYE_DEVICE  0  // First MAX7219 in chain
#define RIGHT_EYE_DEVICE 1  // Second MAX7219 in chain

// DIN = 51, CLK = 52, CS = 53, 2 devices (left eye = 0, right eye = 1)
LedControl lc = LedControl(51, 52, 53, 2);

// Constants for visible area
const int CENTER = 3;  // Center position (3,3) for each eye
const int BRIGHTNESS = 8;  // Default brightness (0-15)

// Current state
String currentState = "IDLE";
bool animationActive = true;

// Animation variables
unsigned long lastUpdate = 0;
int animationStep = 0;
int speakingPattern = 0;  // Track different speaking patterns

// Current pattern and emotion state
String currentPattern = "idle";
String currentEmotion = "neutral";

void setup() {
  // Start serial communication
  Serial.begin(115200);
  Serial.println("Arduino Eyes Starting...");

  // Initialize LED matrices
  for (int device = 0; device < 2; device++) {
    lc.shutdown(device, false);     // Wake up displays
    lc.setIntensity(device, BRIGHTNESS);
    lc.clearDisplay(device);
  }

  // Signal we're ready
  Serial.println("READY");
  
  // Start in IDLE state
  setEyeState("IDLE");
}

void loop() {
  // Check for commands
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();  // Remove any whitespace
    command.toUpperCase();  // Convert to uppercase for consistency
    
    Serial.print("Received: ");
    Serial.println(command);
    
    // Handle the command
    if (command.startsWith("STATE:")) {
      // Extract state from STATE:xyz command
      String state = command.substring(6);
      setEyeState(state);
      Serial.print("ACK:");
      Serial.println(state);
    }
    else if (command.startsWith("SPEAKING:")) {
      // Extract pattern number (SPEAKING:1, SPEAKING:2, etc.)
      speakingPattern = command.substring(9).toInt();
      setEyeState("SPEAKING");
      Serial.println("ACK:SPEAKING");
    }
    else if (command == "TEST") {
      setEyeState("TEST");
      Serial.println("ACK:TEST");
    }
    else if (command == "CLEAR") {
      clearEyes();
      Serial.println("ACK:CLEAR");
    }
    else {
      setEyeState(command);
      Serial.print("ACK:");
      Serial.println(command);
    }
  }
  
  // Update animations based on current state
  updateEyeAnimation();
  
  delay(50); // Small delay to prevent overwhelming the serial port
}

void setEyeState(String state) {
  currentState = state;
  
  // Clear both eyes
  clearEyes();
  
  // Initial setup for the new state
  if (state == "IDLE") {
    // Full 3x3 grid
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
  }
  else if (state == "LISTENING") {
    // All center 3x3 LEDs
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
  }
  else if (state == "PROCESSING") {
    // Start with center column
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        lc.setLed(device, row, CENTER, true);
      }
    }
  }
  else if (state == "SPEAKING") {
    // Initial speaking state will be set in updateEyeAnimation
    animationStep = 0;
  }
  else if (state == "ERROR") {
    // X pattern
    for (int device = 0; device < 2; device++) {
      lc.setLed(device, CENTER-1, CENTER-1, true);
      lc.setLed(device, CENTER+1, CENTER+1, true);
      lc.setLed(device, CENTER-1, CENTER+1, true);
      lc.setLed(device, CENTER+1, CENTER-1, true);
    }
  }
  else if (state == "TEST") {
    // Turn on all LEDs in both matrices
    for (int device = 0; device < 2; device++) {
      for (int row = 0; row < 8; row++) {
        for (int col = 0; col < 8; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
    Serial.println("All LEDs turned ON for testing");
  }
}

void updateEyeAnimation() {
  // Only update every 100ms
  if (millis() - lastUpdate < 100) {
    return;
  }
  
  lastUpdate = millis();
  
  if (currentState == "PROCESSING") {
    // Rotate the center column
    clearEyes();
    for (int device = 0; device < 2; device++) {
      switch(animationStep) {
        case 0:
          lc.setLed(device, CENTER-1, CENTER, true);
          break;
        case 1:
          lc.setLed(device, CENTER, CENTER, true);
          break;
        case 2:
          lc.setLed(device, CENTER+1, CENTER, true);
          break;
      }
    }
    animationStep = (animationStep + 1) % 3;
  }
  else if (currentState == "SPEAKING") {
    clearEyes();
    for (int device = 0; device < 2; device++) {
      switch(speakingPattern) {
        case 1: // Pulsing pattern
          if (animationStep < 2) {
            // Full brightness
            for (int row = CENTER-1; row <= CENTER+1; row++) {
              for (int col = CENTER-1; col <= CENTER+1; col++) {
                lc.setLed(device, row, col, true);
              }
            }
          } else {
            // Dimmed (just center column)
            for (int row = CENTER-1; row <= CENTER+1; row++) {
              lc.setLed(device, row, CENTER, true);
            }
          }
          break;
          
        case 2: // Expanding square
          if (animationStep == 0) {
            // Just center
            lc.setLed(device, CENTER, CENTER, true);
          } else if (animationStep == 1) {
            // Cross pattern
            for (int i = -1; i <= 1; i++) {
              lc.setLed(device, CENTER + i, CENTER, true);
              lc.setLed(device, CENTER, CENTER + i, true);
            }
          } else {
            // Full square
            for (int row = CENTER-1; row <= CENTER+1; row++) {
              for (int col = CENTER-1; col <= CENTER+1; col++) {
                lc.setLed(device, row, col, true);
              }
            }
          }
          break;
          
        case 3: // Wave pattern
          for (int row = CENTER-1; row <= CENTER+1; row++) {
            int offset = (row - (CENTER-1) + animationStep) % 3;
            lc.setLed(device, row, CENTER + offset - 1, true);
          }
          break;
          
        default: // Default speaking animation (vertical bounce)
          switch(animationStep) {
            case 0:
              lc.setLed(device, CENTER-1, CENTER, true);
              lc.setLed(device, CENTER, CENTER, true);
              break;
            case 1:
              lc.setLed(device, CENTER, CENTER, true);
              lc.setLed(device, CENTER+1, CENTER, true);
              break;
            case 2:
              lc.setLed(device, CENTER-1, CENTER, true);
              lc.setLed(device, CENTER, CENTER, true);
              lc.setLed(device, CENTER+1, CENTER, true);
              break;
          }
      }
    }
    animationStep = (animationStep + 1) % 4;
  }
  else if (currentState == "ERROR") {
    // Blink the X pattern
    for (int device = 0; device < 2; device++) {
      lc.setIntensity(device, animationStep == 0 ? BRIGHTNESS : 0);
    }
    animationStep = (animationStep + 1) % 2;
  }
}

void clearEyes() {
  for (int device = 0; device < 2; device++) {
    lc.clearDisplay(device);
  }
}

void setPattern(String pattern) {
  // Clear both eyes
  clearEyes();
  
  // Initial setup for the new pattern
  if (pattern == "idle") {
    // Full 3x3 grid
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
  }
  else if (pattern == "listening") {
    // All center 3x3 LEDs
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
  }
  else if (pattern == "processing") {
    // Start with center column
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        lc.setLed(device, row, CENTER, true);
      }
    }
  }
  else if (pattern == "speaking") {
    // Initial speaking state
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
  }
  else if (pattern == "startup") {
    // Expanding rings
    for (int device = 0; device < 2; device++) {
      lc.setLed(device, CENTER, CENTER, true);
    }
  }
  else if (pattern == "shutdown") {
    // Start with full grid
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
  }
  else if (pattern == "error") {
    // X pattern
    for (int device = 0; device < 2; device++) {
      lc.setLed(device, CENTER-1, CENTER-1, true);
      lc.setLed(device, CENTER+1, CENTER+1, true);
      lc.setLed(device, CENTER-1, CENTER+1, true);
      lc.setLed(device, CENTER+1, CENTER-1, true);
    }
  }
} 