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
    case 'E': // ENGAGED
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
      // Initial wave pattern (animation will cycle through patterns)
      for (int device = 0; device < 2; device++) {
        // Start with vertical line pattern
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
      // Full 3x3 grid with pulsing brightness (animation will handle the rest)
      for (int device = 0; device < 2; device++) {
        // Show all LEDs in the 3x3 grid
        for (int row = CENTER-1; row <= CENTER+1; row++) {
          for (int col = CENTER-1; col <= CENTER+1; col++) {
            lc.setLed(device, row, col, true);
          }
        }
        // Start with higher brightness
        lc.setIntensity(device, min(15, currentBrightness + 2));
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
      
    case 'E': // ENGAGED pattern
      // Start with a gentle diamond pattern
      for (int device = 0; device < 2; device++) {
        lc.setLed(device, CENTER-1, CENTER, true);  // Top
        lc.setLed(device, CENTER, CENTER-1, true);  // Left
        lc.setLed(device, CENTER, CENTER, true);    // Center
        lc.setLed(device, CENTER, CENTER+1, true);  // Right
        lc.setLed(device, CENTER+1, CENTER, true);  // Bottom
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
  // Only update every 80ms (slightly faster than original 100ms for smoother animations)
  if (millis() - lastUpdate < 80) {
    return;
  }
  
  lastUpdate = millis();
  
  // Update animation based on the current pattern
  switch (currentPattern) {
    case 'T': // THINKING animation
      {
        // Rotating dot pattern (keeping original as it works well)
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
      }
      break;
      
    case 'S': // SPEAKING animation - Enhanced wave pattern
      {
        // Create a more dynamic speaking animation that suggests audio waveforms
        clearEyes();
        
        // 8 animation steps for a more fluid looking waveform
        int speakingPatterns[8][9] = {
          // Format: top-left, top-center, top-right, mid-left, mid-center, mid-right, bottom-left, bottom-center, bottom-right
          {0, 1, 0, 0, 1, 0, 0, 1, 0}, // Vertical line
          {0, 0, 0, 1, 1, 1, 0, 0, 0}, // Horizontal line
          {0, 1, 0, 1, 1, 1, 0, 1, 0}, // Plus shape
          {1, 0, 1, 0, 1, 0, 1, 0, 1}, // X shape
          {0, 1, 0, 0, 1, 0, 0, 1, 0}, // Vertical line (repeat)
          {0, 0, 0, 1, 1, 1, 0, 0, 0}, // Horizontal line (repeat)
          {1, 1, 1, 0, 0, 0, 1, 1, 1}, // Top and bottom rows
          {1, 0, 1, 1, 0, 1, 1, 0, 1}  // Checkerboard
        };
        
        int patternIndex = animationStep % 8;
        int ledIndex = 0;
        
        for (int device = 0; device < 2; device++) {
          for (int row = CENTER-1; row <= CENTER+1; row++) {
            for (int col = CENTER-1; col <= CENTER+1; col++) {
              if (speakingPatterns[patternIndex][ledIndex]) {
                lc.setLed(device, row, col, true);
              }
              ledIndex++;
            }
          }
          ledIndex = 0; // Reset for the second eye
        }
        
        animationStep = (animationStep + 1) % 8;
      }
      break;
      
    case 'L': // LISTENING animation - Pulsing wipe effect
      {
        // Create a pulsing wipe effect that moves across the grid
        clearEyes();
        
        // Always show the full 3x3 grid but vary brightness in a wave pattern
        for (int device = 0; device < 2; device++) {
          for (int row = CENTER-1; row <= CENTER+1; row++) {
            for (int col = CENTER-1; col <= CENTER+1; col++) {
              lc.setLed(device, row, col, true);
            }
          }
        }
        
        // Create a wave-like brightness effect with 6 states
        // Calculate base brightness level for the animation step
        int baseIntensity = currentBrightness - 2; // Base brightness level
        
        // We'll have 6 different animation frames for the pulsing effect
        int pulseStep = animationStep % 6;
        
        for (int device = 0; device < 2; device++) {
          // Different brightness levels for each column based on animation step
          // This creates a wave of brightness moving across the eyes
          switch (pulseStep) {
            case 0: // Bright left column, medium center, dim right
              lc.setIntensity(device, min(15, baseIntensity + 4));
              break;
            case 1: // Medium left, bright center, medium right
              lc.setIntensity(device, min(15, baseIntensity + 3));
              break;
            case 2: // Dim left, medium center, bright right
              lc.setIntensity(device, min(15, baseIntensity + 2));
              break;
            case 3: // Medium left, dim center, medium right
              lc.setIntensity(device, max(0, baseIntensity));
              break;
            case 4: // Bright left, medium center, dim right (repeat with less intensity)
              lc.setIntensity(device, min(15, baseIntensity + 1));
              break;
            case 5: // All columns at medium brightness (resting state)
              lc.setIntensity(device, min(15, baseIntensity + 2));
              break;
          }
        }
        
        animationStep = (animationStep + 1) % 6;
      }
      break;
      
    case 'E': // ENGAGED animation - Gentle breathing diamond
      {
        clearEyes();
        
        // Create a gentle breathing effect with the diamond pattern
        // 8 steps of animation for smooth transition
        int breathStep = animationStep % 8;
        int baseIntensity = currentBrightness - 3; // Start a bit dimmer
        
        // Calculate brightness based on breath step (sine wave pattern)
        int breathIntensity = baseIntensity + (breathStep < 4 ? breathStep : 7 - breathStep);
        
        // Set the brightness for this frame
        for (int device = 0; device < 2; device++) {
          lc.setIntensity(device, min(15, max(0, breathIntensity)));
          
          // Draw the diamond pattern
          lc.setLed(device, CENTER-1, CENTER, true);  // Top
          lc.setLed(device, CENTER, CENTER-1, true);  // Left
          lc.setLed(device, CENTER, CENTER, true);    // Center
          lc.setLed(device, CENTER, CENTER+1, true);  // Right
          lc.setLed(device, CENTER+1, CENTER, true);  // Bottom
        }
        
        animationStep = (animationStep + 1) % 8;
      }
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