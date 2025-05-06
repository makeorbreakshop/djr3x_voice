/*
 * DJ R3X Eyes Controller
 * Controls LED animations for eyes using MAX7219 LED matrices
 * 
 * Dependencies:
 * - LedControl library for MAX7219 LED matrix control
 * - ArduinoJson by Benoit Blanchon (v7.4.1) for JSON communication
 */

#include <LedControl.h>
#include <ArduinoJson.h>

// LED Configuration
#define LEFT_EYE_DEVICE  0  // First MAX7219 in chain
#define RIGHT_EYE_DEVICE 1  // Second MAX7219 in chain
#define JSON_BUFFER_SIZE 256  // Increased for v7 compatibility
#define DEBUG_MODE false  // Set to true for verbose debug output

// DIN = 51, CLK = 52, CS = 53, 2 devices (left eye = 0, right eye = 1)
LedControl lc = LedControl(51, 52, 53, 2);

// Constants for visible area
const int CENTER = 3;  // Center position (3,3) for each eye
const int BRIGHTNESS = 8;  // Default brightness (0-15)

// Current state
String currentPattern = "idle";
String currentEmotion = "neutral";
bool animationActive = true;

// Animation variables
unsigned long lastUpdate = 0;
int animationStep = 0;
int mouthLevel = 0;  // For mouth animation level (0-255)

void setup() {
  // Start serial communication
  Serial.begin(115200);
  
  // Create response JSON
  JsonDocument response;
  response["status"] = "starting";
  serializeJson(response, Serial);
  Serial.println();

  // Initialize LED matrices
  for (int device = 0; device < 2; device++) {
    lc.shutdown(device, false);     // Wake up displays
    lc.setIntensity(device, BRIGHTNESS);
    lc.clearDisplay(device);
  }

  // Signal we're ready
  response.clear();
  response["status"] = "ready";
  serializeJson(response, Serial);
  Serial.println();
  
  // Start in idle pattern
  setPattern("idle");
}

void loop() {
  // Check for commands
  if (Serial.available() > 0) {
    // Read the JSON from Serial
    String rawData = Serial.readStringUntil('\n');
    
    // Echo received data if in debug mode
    if (DEBUG_MODE) {
      JsonDocument response;
      response["received"] = rawData;
      serializeJson(response, Serial);
      Serial.println();
    }
    
    // Parse incoming JSON
    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, rawData);
    
    if (error) {
      JsonDocument response;
      response["error"] = error.c_str();
      serializeJson(response, Serial);
      Serial.println();
      return;
    }
    
    // Extract command components
    const char* pattern = doc["pattern"] | "idle";
    const char* emotion = doc["emotion"] | "neutral";
    int level = doc["level"] | 0;  // For mouth animation
    
    // Echo parsed values if in debug mode
    if (DEBUG_MODE) {
      JsonDocument response;
      response["parsed"]["pattern"] = pattern;
      response["parsed"]["emotion"] = emotion;
      response["parsed"]["level"] = level;
      serializeJson(response, Serial);
      Serial.println();
    }
    
    // Update state
    if (pattern) {
      currentPattern = pattern;
      currentEmotion = emotion;
      mouthLevel = level;
      
      // Apply the pattern
      setPattern(currentPattern);
      
      // Send acknowledgment
      JsonDocument response;
      response["ack"] = pattern;
      serializeJson(response, Serial);
      Serial.println();
    }
  }
  
  // Update animations based on current pattern
  updateEyeAnimation();
  
  delay(50); // Small delay to prevent overwhelming the serial port
}

void setPattern(String pattern) {
  // Clear both eyes
  clearEyes();
  
  // Reset animation step
  animationStep = 0;
  
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
  else if (pattern == "ambient") {
    // Initialize the ambient pattern with a cross pattern
    // This will be animated in updateEyeAnimation
    for (int device = 0; device < 2; device++) {
      // Vertical line
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        lc.setLed(device, row, CENTER, true);
      }
      // Horizontal line
      for (int col = CENTER-1; col <= CENTER+1; col++) {
        lc.setLed(device, CENTER, col, true);
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
    // Initial speaking state will be set in updateEyeAnimation
    animationStep = 0;
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
  else {
    // Default to idle pattern for any unrecognized pattern
    // Full 3x3 grid (same as idle)
    for (int device = 0; device < 2; device++) {
      for (int row = CENTER-1; row <= CENTER+1; row++) {
        for (int col = CENTER-1; col <= CENTER+1; col++) {
          lc.setLed(device, row, col, true);
        }
      }
    }
    
    // Notify about the unrecognized pattern if in debug mode
    if (DEBUG_MODE) {
      JsonDocument response;
      response["warning"] = "Unrecognized pattern";
      response["fallback"] = "idle";
      response["received"] = pattern;
      serializeJson(response, Serial);
      Serial.println();
    }
    
    // Set current pattern to idle for animation updates
    currentPattern = "idle";
  }
}

void updateEyeAnimation() {
  // Only update every 100ms
  if (millis() - lastUpdate < 100) {
    return;
  }
  
  lastUpdate = millis();
  
  if (currentPattern == "ambient") {
    // Create an animated pattern for ambient mode
    // This will cycle through different patterns
    clearEyes();
    
    for (int device = 0; device < 2; device++) {
      switch(animationStep) {
        case 0:
          // Cross pattern (+ shape)
          for (int row = CENTER-1; row <= CENTER+1; row++) {
            lc.setLed(device, row, CENTER, true);
          }
          for (int col = CENTER-1; col <= CENTER+1; col++) {
            lc.setLed(device, CENTER, col, true);
          }
          break;
        case 1:
          // X pattern
          lc.setLed(device, CENTER-1, CENTER-1, true);
          lc.setLed(device, CENTER-1, CENTER+1, true);
          lc.setLed(device, CENTER+1, CENTER-1, true);
          lc.setLed(device, CENTER+1, CENTER+1, true);
          lc.setLed(device, CENTER, CENTER, true);
          break;
        case 2:
          // Circle/square pattern (outline)
          lc.setLed(device, CENTER-1, CENTER-1, true);
          lc.setLed(device, CENTER-1, CENTER, true);
          lc.setLed(device, CENTER-1, CENTER+1, true);
          lc.setLed(device, CENTER, CENTER-1, true);
          lc.setLed(device, CENTER, CENTER+1, true);
          lc.setLed(device, CENTER+1, CENTER-1, true);
          lc.setLed(device, CENTER+1, CENTER, true);
          lc.setLed(device, CENTER+1, CENTER+1, true);
          break;
        case 3:
          // Center only
          lc.setLed(device, CENTER, CENTER, true);
          break;
      }
    }
    
    // Cycle through 4 animation steps
    animationStep = (animationStep + 1) % 4;
  }
  else if (currentPattern == "processing") {
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
  else if (currentPattern == "speaking") {
    clearEyes();
    for (int device = 0; device < 2; device++) {
      // Map mouthLevel (0-255) to number of LEDs lit (0-3)
      int ledsToLight = map(mouthLevel, 0, 255, 0, 3);
      
      // Always light center LED when speaking
      lc.setLed(device, CENTER, CENTER, true);
      
      // Add top LED if level is high enough
      if (ledsToLight > 1) {
        lc.setLed(device, CENTER-1, CENTER, true);
      }
      
      // Add bottom LED if level is at max
      if (ledsToLight > 2) {
        lc.setLed(device, CENTER+1, CENTER, true);
      }
    }
  }
  else if (currentPattern == "error") {
    // Blink the X pattern
    for (int device = 0; device < 2; device++) {
      lc.setIntensity(device, animationStep == 0 ? BRIGHTNESS : 0);
    }
    animationStep = (animationStep + 1) % 2;
  }
  else if (currentPattern == "startup") {
    // Expanding animation
    clearEyes();
    for (int device = 0; device < 2; device++) {
      switch(animationStep) {
        case 0:
          lc.setLed(device, CENTER, CENTER, true);
          break;
        case 1:
          for (int i = -1; i <= 1; i++) {
            lc.setLed(device, CENTER + i, CENTER, true);
            lc.setLed(device, CENTER, CENTER + i, true);
          }
          break;
        case 2:
          for (int row = CENTER-1; row <= CENTER+1; row++) {
            for (int col = CENTER-1; col <= CENTER+1; col++) {
              lc.setLed(device, row, col, true);
            }
          }
          break;
      }
    }
    if (animationStep < 2) {
      animationStep++;
    }
  }
  else if (currentPattern == "shutdown") {
    // Contracting animation
    clearEyes();
    for (int device = 0; device < 2; device++) {
      switch(animationStep) {
        case 0:
          for (int row = CENTER-1; row <= CENTER+1; row++) {
            for (int col = CENTER-1; col <= CENTER+1; col++) {
              lc.setLed(device, row, col, true);
            }
          }
          break;
        case 1:
          for (int i = -1; i <= 1; i++) {
            lc.setLed(device, CENTER + i, CENTER, true);
            lc.setLed(device, CENTER, CENTER + i, true);
          }
          break;
        case 2:
          lc.setLed(device, CENTER, CENTER, true);
          break;
      }
    }
    if (animationStep < 2) {
      animationStep++;
    }
  }
}

void clearEyes() {
  for (int device = 0; device < 2; device++) {
    lc.clearDisplay(device);
  }
} 