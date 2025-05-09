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
#define JSON_BUFFER_SIZE 512  // Increased for more complex commands
#define DEBUG_MODE true  // Set to true for verbose debug output

// DIN = 51, CLK = 52, CS = 53, 2 devices (left eye = 0, right eye = 1)
LedControl lc = LedControl(51, 52, 53, 2);

// Constants for visible area
const int CENTER = 3;  // Center position (3,3) for each eye
const int DEFAULT_BRIGHTNESS = 8;  // Default brightness (0-15)

// Current state
String currentPattern = "idle";
String currentEmotion = "neutral";
int currentBrightness = DEFAULT_BRIGHTNESS;
bool animationActive = true;

// Animation variables
unsigned long lastUpdate = 0;
int animationStep = 0;
int mouthLevel = 0;  // For mouth animation level (0-255)

// Command handling
enum class Command {
  SET_PATTERN,
  SET_COLOR,
  SET_BRIGHTNESS,
  RESET,
  TEST,
  STATUS,
  UNKNOWN
};

Command getCommandFromString(const char* cmd) {
  if (strcmp(cmd, "set_pattern") == 0) return Command::SET_PATTERN;
  if (strcmp(cmd, "set_color") == 0) return Command::SET_COLOR;
  if (strcmp(cmd, "set_brightness") == 0) return Command::SET_BRIGHTNESS;
  if (strcmp(cmd, "reset") == 0) return Command::RESET;
  if (strcmp(cmd, "test") == 0) return Command::TEST;
  if (strcmp(cmd, "status") == 0) return Command::STATUS;
  return Command::UNKNOWN;
}

void setup() {
  // Start serial communication
  Serial.begin(115200);
  
  // Initialize LED matrices
  for (int device = 0; device < 2; device++) {
    lc.shutdown(device, false);     // Wake up displays
    lc.setIntensity(device, currentBrightness);
    lc.clearDisplay(device);
  }

  // Send ready status
  StaticJsonDocument<200> response;
  response["status"] = "ready";
  serializeJson(response, Serial);
  Serial.println();
  
  // Start in idle pattern
  setPattern("idle");
}

void loop() {
  // Check for commands
  if (Serial.available() > 0) {
    String rawData = Serial.readStringUntil('\n');
    
    // Parse JSON command
    StaticJsonDocument<JSON_BUFFER_SIZE> doc;
    DeserializationError error = deserializeJson(doc, rawData);
    
    if (error) {
      StaticJsonDocument<200> response;
      response["error"] = error.c_str();
      serializeJson(response, Serial);
      Serial.println();
      return;
    }
    
    // Process command
    const char* cmdStr = doc["command"];
    Command cmd = getCommandFromString(cmdStr);
    
    bool success = false;
    
    switch (cmd) {
      case Command::SET_PATTERN:
        success = handleSetPattern(doc["params"]);
        break;
      case Command::SET_COLOR:
        success = handleSetColor(doc["params"]);
        break;
      case Command::SET_BRIGHTNESS:
        success = handleSetBrightness(doc["params"]);
        break;
      case Command::RESET:
        success = handleReset();
        break;
      case Command::TEST:
        success = handleTest();
        break;
      case Command::STATUS:
        success = handleStatus();
        break;
      default:
        StaticJsonDocument<200> response;
        response["error"] = "Unknown command";
        serializeJson(response, Serial);
        Serial.println();
        return;
    }
    
    // Send acknowledgment
    StaticJsonDocument<200> response;
    response["ack"] = success;
    serializeJson(response, Serial);
    Serial.println();
  }
  
  // Update animations
  updateEyeAnimation();
  delay(50);
}

bool handleSetPattern(JsonVariant params) {
  const char* pattern = params["pattern"];
  if (!pattern) return false;
  
  currentPattern = pattern;
  
  // Handle optional parameters
  if (params.containsKey("color")) {
    // Color handling would go here if RGB LEDs were available
    // For now, we just acknowledge it
  }
  
  if (params.containsKey("brightness")) {
    float brightness = params["brightness"];
    currentBrightness = map(brightness * 100, 0, 100, 0, 15);
    for (int device = 0; device < 2; device++) {
      lc.setIntensity(device, currentBrightness);
    }
  }
  
  // Reset animation state
  animationStep = 0;
  clearEyes();
  
  return true;
}

bool handleSetColor(JsonVariant params) {
  // Color handling would go here if RGB LEDs were available
  // For now, we just acknowledge it
  return true;
}

bool handleSetBrightness(JsonVariant params) {
  if (!params.containsKey("brightness")) return false;
  
  float brightness = params["brightness"];
  currentBrightness = map(brightness * 100, 0, 100, 0, 15);
  
  for (int device = 0; device < 2; device++) {
    lc.setIntensity(device, currentBrightness);
  }
  
  return true;
}

bool handleReset() {
  for (int device = 0; device < 2; device++) {
    lc.shutdown(device, false);
    lc.setIntensity(device, DEFAULT_BRIGHTNESS);
    lc.clearDisplay(device);
  }
  currentPattern = "idle";
  currentBrightness = DEFAULT_BRIGHTNESS;
  animationStep = 0;
  return true;
}

bool handleTest() {
  // Simple test pattern
  for (int device = 0; device < 2; device++) {
    lc.setLed(device, CENTER, CENTER, true);
  }
  delay(500);
  clearEyes();
  return true;
}

bool handleStatus() {
  StaticJsonDocument<200> status;
  status["pattern"] = currentPattern;
  status["brightness"] = currentBrightness;
  status["animation_step"] = animationStep;
  serializeJson(status, Serial);
  Serial.println();
  return true;
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
      lc.setIntensity(device, animationStep == 0 ? currentBrightness : 0);
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