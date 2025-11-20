/*
 * DJ R3X Face Controller V2 - REFACTORED ARCHITECTURE
 *
 * Clean separation of:
 * - PATTERNS: State definitions (IDLE, ENGAGED, LISTENING, etc.)
 * - ANIMATIONS: Visual behaviors (breathing, pulsing, rotating)
 * - EFFECTS: Overlays (blinking, amplitude modulation)
 *
 * Hardware:
 * - Eyes: Pin 6, 14 LEDs (2 rings of 7)
 * - Mouth: Pin 5, 8 LEDs (V-shaped)
 * - Board: MEGA 2560 (256-byte buffer configured)
 */

// CRITICAL: Buffer size BEFORE Arduino.h
#define SERIAL_RX_BUFFER_SIZE 256
#define SERIAL_TX_BUFFER_SIZE 256

#include <FastLED.h>

// ============================================
// CONFIGURATION
// ============================================

// Eye LED Configuration
#define EYE_PIN 6
#define NUM_EYE_LEDS 14
#define LEDS_PER_EYE 7
#define LEFT_EYE_START 0
#define RIGHT_EYE_START 7

// Mouth LED Configuration
#define MOUTH_PIN 5
#define NUM_MOUTH_LEDS 8

// LED Arrays
CRGB eyeLeds[NUM_EYE_LEDS];
CRGB mouthLeds[NUM_MOUTH_LEDS];

// ============================================
// STATE VARIABLES
// ============================================

// Pattern State
enum Pattern {
  PATTERN_IDLE,
  PATTERN_ENGAGED,
  PATTERN_LISTENING,
  PATTERN_THINKING,
  PATTERN_SPEAKING,
  PATTERN_FLASH,
  PATTERN_HAPPY,
  PATTERN_SAD,
  PATTERN_ANGRY
};

Pattern currentPattern = PATTERN_IDLE;
Pattern previousPattern = PATTERN_IDLE;  // For returning after flash

// Color State
CRGB currentEyeColor = CRGB(255, 120, 0);  // Default orange
CRGB previousEyeColor = CRGB(255, 120, 0);
CRGB currentMouthColor = CRGB(0, 100, 255);  // Default blue

// Animation State
unsigned long lastAnimationUpdate = 0;
unsigned long animationStep = 0;
int currentBrightness = 128;

// Mouth State
int mouthAmplitude = 0;  // 0-255

// Blink State (only for IDLE)
bool blinkingEnabled = false;
unsigned long nextBlinkTime = 0;
unsigned long blinkStartTime = 0;
bool isBlinking = false;

// Command Buffer
String commandBuffer = "";
bool readingCommand = false;

// ============================================
// SETUP
// ============================================

void setup() {
  Serial.begin(115200);

  // Initialize LEDs
  FastLED.addLeds<WS2812B, EYE_PIN, GRB>(eyeLeds, NUM_EYE_LEDS);
  FastLED.addLeds<WS2812B, MOUTH_PIN, GRB>(mouthLeds, NUM_MOUTH_LEDS);
  FastLED.setBrightness(currentBrightness);

  // Clear all LEDs
  FastLED.clear(true);

  // Set initial state
  setPattern(PATTERN_IDLE);

  // Signal ready
  Serial.println("READY");
}

// ============================================
// MAIN LOOP
// ============================================

void loop() {
  // Process serial commands
  processSerialCommands();

  // Update animations
  updateAnimation();

  // Update mouth
  updateMouth();

  // Show changes
  FastLED.show();
}

// ============================================
// COMMAND PROCESSING
// ============================================

void processSerialCommands() {
  while (Serial.available() > 0) {
    char inChar = Serial.read();

    // Multi-character commands
    if (inChar == 'C' || inChar == 'B' || inChar == 'M' || readingCommand) {
      if (!readingCommand) {
        commandBuffer = "";
        readingCommand = true;
      }

      commandBuffer += inChar;

      // Check for complete commands
      if (commandBuffer.startsWith("C") && commandBuffer.length() == 7) {
        // Color: CRRGGBB
        processColorCommand(commandBuffer);
        commandBuffer = "";
        readingCommand = false;
      }
      else if (commandBuffer.startsWith("B") && commandBuffer.length() == 4) {
        // Brightness: Bnnn
        processBrightnessCommand(commandBuffer);
        commandBuffer = "";
        readingCommand = false;
      }
      else if (commandBuffer.startsWith("M") && commandBuffer.length() == 4) {
        // Mouth: Mnnn
        processMouthCommand(commandBuffer);
        commandBuffer = "";
        readingCommand = false;
      }

      continue;
    }

    // Single character pattern commands
    if (inChar != '\n' && inChar != '\r') {
      processPatternCommand(inChar);
    }
  }
}

void processPatternCommand(char cmd) {
  bool success = true;

  switch(cmd) {
    case 'I': setPattern(PATTERN_IDLE); break;
    case 'E': setPattern(PATTERN_ENGAGED); break;
    case 'L': setPattern(PATTERN_LISTENING); break;
    case 'T': setPattern(PATTERN_THINKING); break;
    case 'S': setPattern(PATTERN_SPEAKING); break;
    case 'F': setPattern(PATTERN_FLASH); break;
    case 'H': setPattern(PATTERN_HAPPY); break;
    case 'D': setPattern(PATTERN_SAD); break;
    case 'A': setPattern(PATTERN_ANGRY); break;
    case 'R': resetSystem(); break;
    default: success = false; break;
  }

  Serial.println(success ? "+" : "-");
  Serial.flush();
}

void processColorCommand(String cmd) {
  // Parse CRRGGBB
  int r = hexToDec(cmd.substring(1, 3));
  int g = hexToDec(cmd.substring(3, 5));
  int b = hexToDec(cmd.substring(5, 7));

  currentEyeColor = CRGB(r, g, b);

  Serial.println("+");
  Serial.flush();
}

void processBrightnessCommand(String cmd) {
  int brightness = cmd.substring(1).toInt();
  currentBrightness = constrain(brightness, 0, 255);
  FastLED.setBrightness(currentBrightness);

  Serial.println("+");
  Serial.flush();
}

void processMouthCommand(String cmd) {
  // Fire-and-forget - no response wait
  mouthAmplitude = constrain(cmd.substring(1).toInt(), 0, 255);
  // No serial response for mouth commands (fire-and-forget)
}

// ============================================
// PATTERN MANAGEMENT
// ============================================

void setPattern(Pattern pattern) {
  // Save previous state for flash return
  if (pattern == PATTERN_FLASH) {
    previousPattern = currentPattern;
    previousEyeColor = currentEyeColor;
  }

  currentPattern = pattern;
  animationStep = 0;
  lastAnimationUpdate = millis();

  // Set pattern-specific properties
  switch(pattern) {
    case PATTERN_IDLE:
      currentMouthColor = CRGB(0, 100, 255);  // Blue
      blinkingEnabled = true;
      nextBlinkTime = millis() + random(3000, 7000);
      break;

    case PATTERN_ENGAGED:
      currentMouthColor = CRGB(255, 200, 0);  // Golden
      blinkingEnabled = false;
      break;

    case PATTERN_LISTENING:
      currentMouthColor = CRGB(0, 50, 200);  // Dark blue
      blinkingEnabled = false;
      break;

    case PATTERN_THINKING:
      currentMouthColor = CRGB(128, 0, 255);  // Purple
      blinkingEnabled = false;
      break;

    case PATTERN_SPEAKING:
      // Keep current mouth color
      blinkingEnabled = false;
      break;

    case PATTERN_FLASH:
      // Keep current mouth color
      blinkingEnabled = false;
      break;

    default:
      blinkingEnabled = false;
      break;
  }
}

// ============================================
// ANIMATION ENGINE
// ============================================

void updateAnimation() {
  unsigned long currentTime = millis();

  // Different update rates for different patterns
  unsigned long updateInterval = 50;  // Default 20fps

  if (currentPattern == PATTERN_FLASH) {
    updateInterval = 10;  // 100fps for smooth flash
  }
  else if (currentPattern == PATTERN_IDLE && isBlinking) {
    updateInterval = 10;  // 100fps for smooth blinks
  }

  if (currentTime - lastAnimationUpdate < updateInterval) {
    return;
  }

  lastAnimationUpdate = currentTime;

  // Execute pattern-specific animation
  switch(currentPattern) {
    case PATTERN_IDLE:
      animateIdle();
      break;

    case PATTERN_ENGAGED:
      animateEngaged();
      break;

    case PATTERN_LISTENING:
      animateListening();
      break;

    case PATTERN_THINKING:
      animateThinking();
      break;

    case PATTERN_SPEAKING:
      animateSpeaking();
      break;

    case PATTERN_FLASH:
      animateFlash();
      break;

    case PATTERN_HAPPY:
      animateHappy();
      break;

    case PATTERN_SAD:
      animateSad();
      break;

    case PATTERN_ANGRY:
      animateAngry();
      break;

    default:
      // Just show solid color
      fillEyes(currentEyeColor);
      break;
  }

  animationStep++;
}

// ============================================
// ANIMATION PATTERNS
// ============================================

void animateIdle() {
  // Breathing effect (always active)
  float breathCycle = sin(animationStep * 0.025) * 0.15;
  float breathMultiplier = 1.0 + breathCycle;

  // Apply breathing to color
  CRGB breathingColor = currentEyeColor;
  breathingColor.nscale8(breathMultiplier * 255);

  // Check for blinking
  if (blinkingEnabled) {
    unsigned long currentTime = millis();

    if (!isBlinking && currentTime >= nextBlinkTime) {
      // Start blink
      isBlinking = true;
      blinkStartTime = currentTime;
    }

    if (isBlinking) {
      unsigned long blinkElapsed = currentTime - blinkStartTime;

      if (blinkElapsed < 150) {
        // Closing
        float progress = blinkElapsed / 150.0;
        breathingColor.nscale8((1.0 - progress) * 255);
      }
      else if (blinkElapsed < 300) {
        // Opening
        float progress = (blinkElapsed - 150) / 150.0;
        breathingColor.nscale8(progress * 255);
      }
      else {
        // Blink complete
        isBlinking = false;
        nextBlinkTime = currentTime + random(3000, 7000);
      }
    }
  }

  fillEyes(breathingColor);
}

void animateEngaged() {
  // Simple breathing, no blinking
  float breathCycle = (animationStep % 70) / 70.0;
  float breathValue = (sin(breathCycle * 2 * PI) + 1) / 2;
  float brightnessMod = 0.85 + (breathValue * 0.3);

  CRGB breathingColor = currentEyeColor;
  breathingColor.nscale8(brightnessMod * 255);

  fillEyes(breathingColor);
}

void animateListening() {
  // Pulsing animation
  float pulse = (sin(animationStep * 0.1) + 1) / 2;

  CRGB pulsingColor = currentEyeColor;
  pulsingColor.nscale8(128 + (pulse * 127));

  fillEyes(pulsingColor);
}

void animateThinking() {
  // Rotating dot
  clearEyes();
  int dotPosition = (animationStep / 5) % LEDS_PER_EYE;

  eyeLeds[LEFT_EYE_START + dotPosition] = currentEyeColor;
  eyeLeds[RIGHT_EYE_START + dotPosition] = currentEyeColor;
}

void animateSpeaking() {
  // Gentle pulse
  float pulse = (sin(animationStep * 0.05) + 1) / 2;

  CRGB pulsingColor = currentEyeColor;
  pulsingColor.nscale8(200 + (pulse * 55));

  fillEyes(pulsingColor);
}

void animateFlash() {
  // Two green flashes then return
  if (animationStep < 30) {
    float brightness = 0.0;

    if (animationStep < 12) {
      // First flash
      if (animationStep < 4) brightness = animationStep / 4.0;
      else if (animationStep < 6) brightness = 1.0;
      else brightness = 1.0 - ((animationStep - 6) / 6.0);
    }
    else if (animationStep >= 16 && animationStep < 28) {
      // Second flash
      int localStep = animationStep - 16;
      if (localStep < 4) brightness = localStep / 4.0;
      else if (localStep < 6) brightness = 1.0;
      else brightness = 1.0 - ((localStep - 6) / 6.0);
    }

    CRGB flashColor = CRGB(0, 255, 0);
    flashColor.nscale8(brightness * 255);
    fillEyes(flashColor);
  }
  else {
    // Return to previous pattern
    currentEyeColor = previousEyeColor;
    setPattern(previousPattern);
  }
}

void animateHappy() {
  fillEyes(CRGB::Green);
  if (random(10) < 3) {
    eyeLeds[random(NUM_EYE_LEDS)] = CRGB::Yellow;
  }
}

void animateSad() {
  float breath = 64 + (sin(animationStep * 0.05) * 64);
  CRGB sadColor = CRGB::Blue;
  sadColor.nscale8(breath);
  fillEyes(sadColor);
}

void animateAngry() {
  int pulse = (animationStep % 10 < 5) ? 255 : 64;
  CRGB angryColor = CRGB::Red;
  angryColor.nscale8(pulse);
  fillEyes(angryColor);
}

// ============================================
// MOUTH CONTROL
// ============================================

void updateMouth() {
  // Special case: ENGAGED with no amplitude = black
  if (currentPattern == PATTERN_ENGAGED && mouthAmplitude == 0) {
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);
    return;
  }

  // Calculate baseline brightness
  int baseBrightness = 0;
  if (currentPattern != PATTERN_ENGAGED && mouthAmplitude == 0) {
    baseBrightness = 20;  // Small glow for non-engaged patterns
  }

  // Center-out bloom based on amplitude
  float openness = mouthAmplitude / 255.0;

  // Center LEDs (1, 6)
  int centerBright = baseBrightness + (openness * (255 - baseBrightness));
  mouthLeds[1] = currentMouthColor;
  mouthLeds[1].nscale8(centerBright);
  mouthLeds[6] = currentMouthColor;
  mouthLeds[6].nscale8(centerBright);

  // Corner LEDs (0, 7) - bloom at 30%
  float cornerOpen = max(0.0f, (openness - 0.3f) * 1.43f);
  int cornerBright = baseBrightness + (cornerOpen * (200 - baseBrightness));
  mouthLeds[0] = currentMouthColor;
  mouthLeds[0].nscale8(cornerBright);
  mouthLeds[7] = currentMouthColor;
  mouthLeds[7].nscale8(cornerBright);

  // Lower-middle LEDs (2, 5) - bloom at 25%
  float lowerMidOpen = max(0.0f, (openness - 0.25f) * 1.33f);
  int lowerMidBright = baseBrightness + (lowerMidOpen * (255 - baseBrightness));
  mouthLeds[2] = currentMouthColor;
  mouthLeds[2].nscale8(lowerMidBright);
  mouthLeds[5] = currentMouthColor;
  mouthLeds[5].nscale8(lowerMidBright);

  // Bottom LEDs (3, 4) - bloom at 50%
  float bottomOpen = max(0.0f, (openness - 0.5f) * 2.0f);
  int bottomBright = baseBrightness + (bottomOpen * (255 - baseBrightness));
  mouthLeds[3] = currentMouthColor;
  mouthLeds[3].nscale8(bottomBright);
  mouthLeds[4] = currentMouthColor;
  mouthLeds[4].nscale8(bottomBright);
}

// ============================================
// HELPER FUNCTIONS
// ============================================

void fillEyes(CRGB color) {
  fill_solid(eyeLeds, NUM_EYE_LEDS, color);
}

void clearEyes() {
  fill_solid(eyeLeds, NUM_EYE_LEDS, CRGB::Black);
}

void resetSystem() {
  currentPattern = PATTERN_IDLE;
  currentEyeColor = CRGB(255, 120, 0);
  currentMouthColor = CRGB(0, 100, 255);
  currentBrightness = 128;
  mouthAmplitude = 0;
  FastLED.setBrightness(currentBrightness);
  setPattern(PATTERN_IDLE);
}

int hexToDec(String hex) {
  int result = 0;
  for (int i = 0; i < hex.length(); i++) {
    char c = hex.charAt(i);
    int digit = (c >= '0' && c <= '9') ? (c - '0') :
                (c >= 'A' && c <= 'F') ? (c - 'A' + 10) :
                (c >= 'a' && c <= 'f') ? (c - 'a' + 10) : 0;
    result = result * 16 + digit;
  }
  return result;
}