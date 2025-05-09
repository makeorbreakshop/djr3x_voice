/*
 * DJ R3X Eyes Controller - Ultra-Simple Protocol
 * Controls LED animations for eyes using MAX7219 LED matrices
 * Single-character commands for maximum reliability
 */

#include <LedControl.h>

// LED Configuration
#define LEFT_EYE_DEVICE  0  // First MAX7219 in chain
#define RIGHT_EYE_DEVICE 1  // Second MAX7219 in chain
#define DEBUG_MODE false    // Set to true for debug output

// DIN = 51, CLK = 52, CS = 53, 2 devices (left eye = 0, right eye = 1)
LedControl lc = LedControl(51, 52, 53, 2);

// Constants for visible area
const int CENTER = 3;  // Center position (3,3) for each eye
const int DEFAULT_BRIGHTNESS = 8;  // Default brightness (0-15)

// Current state
char currentPattern = 'I';  // Default to IDLE
int currentBrightness = DEFAULT_BRIGHTNESS;
unsigned long lastUpdate = 0;
int animationStep = 0;

void setup() {
  // Start serial communication
  Serial.begin(115200);
  
  // Initialize LED matrices
  for (int device = 0; device < 2; device++) {
    lc.shutdown(device, false);       // Wake up displays
    lc.setIntensity(device, currentBrightness);
    lc.clearDisplay(device);
  }
  
  // Set initial pattern (IDLE)
  setPattern('I');
  
  // Send ready message
  Serial.println("+");
  Serial.flush();
}

void loop() {
  // Process serial commands
  if (Serial.available() > 0) {
    char cmd = (char)Serial.read();
    
    // Process the command (ignore newlines and carriage returns)
    if (cmd != '\n' && cmd != '\r') {
      processCommand(cmd);
    }
  }
  
  // Update animations
  updateEyeAnimation();
  delay(20);  // Small delay for stability
}

void processCommand(char cmd) {
  bool success = true;
  
  switch (cmd) {
    // Pattern commands
    case 'I': // IDLE
    case 'S': // SPEAKING
    case 'T': // THINKING
    case 'L': // LISTENING
    case 'H': // HAPPY
    case 'D': // SAD (Down)
    case 'A': // ANGRY
      clearEyes();
      setPattern(cmd);
      break;
      
    // Reset command
    case 'R':
      resetEyes();
      break;
      
    // Brightness commands (B0-B9)
    case '0':
    case '1':
    case '2':
    case '3':
    case '4':
    case '5':
    case '6':
    case '7':
    case '8':
    case '9':
      setBrightness(cmd - '0');
      break;
      
    default:
      success = false;
      break;
  }
  
  // Send response
  if (success) {
    Serial.println("+");
  } else {
    Serial.println("-");
  }
  Serial.flush();
}

void setPattern(char pattern) {
  // Update the current pattern
  currentPattern = pattern;
  
  // Reset animation
  animationStep = 0;
  lastUpdate = millis();
  
  switch (pattern) {
    case 'I': // IDLE pattern
      // Full 3x3 grid
      for (int device = 0; device < 2; device++) {
        for (int row = CENTER-1; row <= CENTER+1; row++) {
          for (int col = CENTER-1; col <= CENTER+1; col++) {
            lc.setLed(device, row, col, true);
          }
        }
      }
      break;
      
    case 'S': // SPEAKING pattern
      // Vertical line
      for (int device = 0; device < 2; device++) {
        lc.setLed(device, CENTER-1, CENTER, true);
        lc.setLed(device, CENTER, CENTER, true);
        lc.setLed(device, CENTER+1, CENTER, true);
      }
      break;
      
    case 'T': // THINKING pattern
      // Center dot (animation will handle the rest)
      for (int device = 0; device < 2; device++) {
        lc.setLed(device, CENTER, CENTER, true);
      }
      break;
      
    case 'L': // LISTENING pattern
      // Full 3x3 grid (animation will pulse)
      for (int device = 0; device < 2; device++) {
        for (int row = CENTER-1; row <= CENTER+1; row++) {
          for (int col = CENTER-1; col <= CENTER+1; col++) {
            lc.setLed(device, row, col, true);
          }
        }
      }
      break;
      
    case 'H': // HAPPY pattern
      // Upward curves
      for (int device = 0; device < 2; device++) {
        lc.setLed(device, CENTER-1, CENTER-1, true);
        lc.setLed(device, CENTER-1, CENTER+1, true);
        lc.setLed(device, CENTER, CENTER, true);
      }
      break;
      
    case 'D': // SAD pattern (Down)
      // Downward curves
      for (int device = 0; device < 2; device++) {
        lc.setLed(device, CENTER+1, CENTER-1, true);
        lc.setLed(device, CENTER+1, CENTER+1, true);
        lc.setLed(device, CENTER, CENTER, true);
      }
      break;
      
    case 'A': // ANGRY pattern
      // Angled lines
      for (int device = 0; device < 2; device++) {
        lc.setLed(device, CENTER-1, CENTER-1, true);
        lc.setLed(device, CENTER, CENTER, true);
        lc.setLed(device, CENTER+1, CENTER+1, true);
      }
      break;
      
    default:
      // Default to IDLE if pattern unknown
      currentPattern = 'I';
      for (int device = 0; device < 2; device++) {
        for (int row = CENTER-1; row <= CENTER+1; row++) {
          for (int col = CENTER-1; col <= CENTER+1; col++) {
            lc.setLed(device, row, col, true);
          }
        }
      }
      break;
  }
}

void updateEyeAnimation() {
  // Only update every 100ms
  if (millis() - lastUpdate < 100) {
    return;
  }
  
  lastUpdate = millis();
  
  // Update animation based on the current pattern
  switch (currentPattern) {
    case 'T': // THINKING animation
      // Rotating dot pattern
      clearEyes();
      int positions[4][2] = {
        {-1, 0}, {0, 1}, {1, 0}, {0, -1}
      };
      for (int device = 0; device < 2; device++) {
        int pos = animationStep % 4;
        lc.setLed(device, CENTER + positions[pos][0], 
                       CENTER + positions[pos][1], true);
        lc.setLed(device, CENTER, CENTER, true);
      }
      animationStep = (animationStep + 1) % 4;
      break;
      
    case 'S': // SPEAKING animation
      // Pulse up and down
      clearEyes();
      for (int device = 0; device < 2; device++) {
        // Always show center
        lc.setLed(device, CENTER, CENTER, true);
        
        // Show additional LEDs based on animation step
        if (animationStep < 2) {
          lc.setLed(device, CENTER-1, CENTER, true);
        }
        if (animationStep >= 1) {
          lc.setLed(device, CENTER+1, CENTER, true); 
        }
      }
      animationStep = (animationStep + 1) % 3;
      break;
      
    case 'L': // LISTENING animation
      // Pulsing pattern
      int brightness = (animationStep % 2) ? currentBrightness : currentBrightness / 2;
      for (int device = 0; device < 2; device++) {
        lc.setIntensity(device, brightness);
      }
      animationStep = (animationStep + 1) % 2;
      break;
  }
}

void setBrightness(int brightness) {
  // Ensure brightness is in valid range (0-15)
  currentBrightness = max(0, min(15, brightness));
  
  for (int device = 0; device < 2; device++) {
    lc.setIntensity(device, currentBrightness);
  }
}

void resetEyes() {
  for (int device = 0; device < 2; device++) {
    lc.shutdown(device, false);
    lc.setIntensity(device, DEFAULT_BRIGHTNESS);
    lc.clearDisplay(device);
  }
  
  currentBrightness = DEFAULT_BRIGHTNESS;
  animationStep = 0;
  
  // Set to idle pattern
  setPattern('I');
}

void clearEyes() {
  for (int device = 0; device < 2; device++) {
    lc.clearDisplay(device);
  }
} 