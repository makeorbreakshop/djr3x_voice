/*
 * DJ R3X Face Controller V3 - Clean Layered Architecture
 *
 * Complete rewrite with proper state machine and layered animations
 * Each state owns its colors - Python only sends state changes
 *
 * Commands:
 *   SI - Set IDLE state
 *   SE - Set ENGAGED state
 *   SL - Set LISTENING state
 *   ST - Set THINKING state
 *   SS - Set SPEAKING state
 *   SF - Trigger FLASH
 *   Mnnn - Mouth amplitude (0-255)
 *   R - Reset system
 *   ? - Help
 *   T - Run automated test sequence
 */

// CRITICAL: Buffer size MUST be defined BEFORE including Arduino.h
// This is included automatically by FastLED.h, so define it first
#define SERIAL_RX_BUFFER_SIZE 256
#define SERIAL_TX_BUFFER_SIZE 256

#include <FastLED.h>

// ============================================
// HARDWARE CONFIGURATION
// ============================================

#define EYE_PIN 6
#define NUM_EYE_LEDS 14
#define LEDS_PER_EYE 7
#define LEFT_EYE_START 0
#define RIGHT_EYE_START 7

#define MOUTH_PIN 5
#define NUM_MOUTH_LEDS 8

CRGB eyeLeds[NUM_EYE_LEDS];
CRGB mouthLeds[NUM_MOUTH_LEDS];

// ============================================
// STATE DEFINITIONS
// ============================================

enum SystemState {
  STATE_IDLE,
  STATE_ENGAGED,
  STATE_LISTENING,
  STATE_THINKING,
  STATE_SPEAKING
};

// State colors (Arduino owns these!)
const CRGB COLOR_IDLE_EYES = CRGB(255, 120, 0);      // Warm orange
const CRGB COLOR_IDLE_MOUTH = CRGB(0, 30, 100);      // Very dark blue
const CRGB COLOR_ENGAGED_EYES = CRGB(0, 255, 255);   // Bright cyan
const CRGB COLOR_ENGAGED_MOUTH = CRGB(255, 200, 0);  // Golden (when speaking)
const CRGB COLOR_THINKING_DOT = CRGB(0, 255, 255);   // Cyan dots
const CRGB COLOR_FLASH = CRGB(0, 255, 0);            // Green

// ============================================
// STATE MACHINE
// ============================================

SystemState currentState = STATE_IDLE;
SystemState previousState = STATE_IDLE;  // For returning after flash

// ============================================
// ANIMATION TIMERS
// ============================================

struct AnimationTimer {
  unsigned long lastUpdate;
  unsigned long interval;
  int step;
};

AnimationTimer breathingTimer = {0, 50, 0};    // 20 FPS
AnimationTimer blinkTimer = {0, 10, 0};        // 100 FPS when active
AnimationTimer pulseTimer = {0, 50, 0};        // 20 FPS
AnimationTimer thinkingTimer = {0, 50, 0};     // 20 FPS

// ============================================
// ANIMATION STATE
// ============================================

// Breathing effect
float breathingPhase = 0;

// Blinking
bool isBlinking = false;
unsigned long nextBlinkTime = 0;
unsigned long blinkStartTime = 0;

// Flash
bool flashActive = false;
int flashStep = 0;

// Mouth
int mouthAmplitude = 0;

// ============================================
// SETUP
// ============================================

void setup() {
  Serial.begin(115200);

  // Initialize LEDs
  FastLED.addLeds<WS2812B, EYE_PIN, GRB>(eyeLeds, NUM_EYE_LEDS);
  FastLED.addLeds<WS2812B, MOUTH_PIN, GRB>(mouthLeds, NUM_MOUTH_LEDS);
  FastLED.setBrightness(128);

  // Clear all LEDs
  FastLED.clear(true);

  // Initialize timers
  unsigned long now = millis();
  breathingTimer.lastUpdate = now;
  blinkTimer.lastUpdate = now;
  pulseTimer.lastUpdate = now;
  thinkingTimer.lastUpdate = now;
  nextBlinkTime = now + random(8000, 15000);

  // Set initial state
  setState(STATE_IDLE);

  Serial.println("READY");
}

// ============================================
// MAIN LOOP
// ============================================

void loop() {
  // Process incoming commands
  processSerialCommands();

  // Update base state animation
  updateBaseState();

  // Apply effect layers
  applyBreathingEffect();
  applyBlinkingEffect();

  // Process responses
  updateMouth();
  updateFlash();

  // Single update point
  FastLED.show();
}

// ============================================
// COMMAND PROCESSING
// ============================================

void processSerialCommands() {
  static String commandBuffer = "";
  static bool readingCommand = false;

  while (Serial.available() > 0) {
    char c = Serial.read();

    // Handle single character state commands
    if (c == 'S' && !readingCommand) {
      commandBuffer = "S";
      readingCommand = true;
      continue;
    }

    // Handle mouth commands
    if (c == 'M' && !readingCommand) {
      commandBuffer = "M";
      readingCommand = true;
      continue;
    }

    // Building command
    if (readingCommand) {
      commandBuffer += c;

      // State commands (SI, SE, SL, ST, SS, SF)
      if (commandBuffer.length() == 2 && commandBuffer[0] == 'S') {
        handleStateCommand(commandBuffer[1]);
        commandBuffer = "";
        readingCommand = false;
      }
      // Mouth commands (Mnnn)
      else if (commandBuffer.length() == 4 && commandBuffer[0] == 'M') {
        int amplitude = commandBuffer.substring(1).toInt();
        mouthAmplitude = constrain(amplitude, 0, 255);
        // Fire and forget - no response
        commandBuffer = "";
        readingCommand = false;
      }
      // Invalid command
      else if (commandBuffer.length() > 4) {
        commandBuffer = "";
        readingCommand = false;
      }
      continue;
    }

    // Single character commands
    if (c == 'R') {
      resetSystem();
      Serial.println("+");
    }
    else if (c == '?') {
      printHelp();
    }
    else if (c == 'T') {
      runTestSequence();
    }
    else if (c == '\n' || c == '\r') {
      // Ignore newlines
    }
  }
}

void handleStateCommand(char stateChar) {
  switch(stateChar) {
    case 'I': setState(STATE_IDLE); break;
    case 'E': setState(STATE_ENGAGED); break;
    case 'L': setState(STATE_LISTENING); break;
    case 'T': setState(STATE_THINKING); break;
    case 'S': setState(STATE_SPEAKING); break;
    case 'F': triggerFlash(); break;
    default:
      Serial.println("-");
      return;
  }
  Serial.println("+");
}

// ============================================
// STATE MANAGEMENT
// ============================================

void setState(SystemState newState) {
  // Don't re-enter same state
  if (newState == currentState && !flashActive) {
    return;
  }

  previousState = currentState;
  currentState = newState;

  // Reset animation counters
  breathingTimer.step = 0;
  pulseTimer.step = 0;
  thinkingTimer.step = 0;

  // State-specific initialization
  switch(currentState) {
    case STATE_IDLE:
      fillEyes(COLOR_IDLE_EYES);
      nextBlinkTime = millis() + random(8000, 15000);
      break;

    case STATE_ENGAGED:
      fillEyes(COLOR_ENGAGED_EYES);
      // Mouth goes BLACK when engaged (until speaking)
      fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);
      break;

    case STATE_LISTENING:
      // Keep current eye color
      break;

    case STATE_THINKING:
      clearEyes();
      break;

    case STATE_SPEAKING:
      // Keep current eye color
      break;
  }
}

// ============================================
// BASE STATE ANIMATIONS
// ============================================

void updateBaseState() {
  unsigned long now = millis();

  switch(currentState) {
    case STATE_IDLE:
      // Base color set, effects handle animation
      break;

    case STATE_ENGAGED:
      // Base color set, effects handle breathing
      break;

    case STATE_LISTENING:
      updateListeningPulse(now);
      break;

    case STATE_THINKING:
      updateThinkingAnimation(now);
      break;

    case STATE_SPEAKING:
      updateSpeakingPulse(now);
      break;
  }
}

void updateListeningPulse(unsigned long now) {
  if (now - pulseTimer.lastUpdate < pulseTimer.interval) return;
  pulseTimer.lastUpdate = now;

  // Dynamic heartbeat pulse (60 BPM)
  int cycle = pulseTimer.step % 20;  // 1 second at 20 FPS

  float brightness;
  if (cycle < 4) {
    // Quick rise (200ms)
    brightness = 0.3 + (cycle / 4.0) * 0.7;  // 30% to 100%
  } else {
    // Slow fall (800ms)
    brightness = 1.0 - ((cycle - 4) / 16.0) * 0.7;  // 100% to 30%
  }

  CRGB pulseColor = COLOR_ENGAGED_EYES;
  pulseColor.nscale8(brightness * 255);
  fillEyes(pulseColor);

  pulseTimer.step++;
}

void updateThinkingAnimation(unsigned long now) {
  if (now - thinkingTimer.lastUpdate < thinkingTimer.interval) return;
  thinkingTimer.lastUpdate = now;

  clearEyes();

  // White center pupils
  eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);
  eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);

  // Rotating dots (counter-rotating)
  int leftPos = ((thinkingTimer.step / 2) % 6) + 1;
  int rightPos = 7 - ((thinkingTimer.step / 2) % 6);

  eyeLeds[LEFT_EYE_START + leftPos] = COLOR_THINKING_DOT;
  eyeLeds[RIGHT_EYE_START + rightPos] = COLOR_THINKING_DOT;

  thinkingTimer.step++;
}

void updateSpeakingPulse(unsigned long now) {
  if (now - pulseTimer.lastUpdate < pulseTimer.interval) return;
  pulseTimer.lastUpdate = now;

  // Gentle pulse while speaking
  float pulse = (sin(pulseTimer.step * 0.1) + 1) / 2;
  float brightness = 0.7 + (pulse * 0.3);  // 70-100%

  CRGB pulseColor = COLOR_ENGAGED_EYES;
  pulseColor.nscale8(brightness * 255);
  fillEyes(pulseColor);

  pulseTimer.step++;
}

// ============================================
// EFFECT LAYERS
// ============================================

void applyBreathingEffect() {
  if (currentState != STATE_IDLE && currentState != STATE_ENGAGED) return;

  unsigned long now = millis();
  if (now - breathingTimer.lastUpdate < breathingTimer.interval) return;
  breathingTimer.lastUpdate = now;

  // Gentle breathing (3.5 second cycle)
  breathingPhase += 0.0285;  // Complete cycle in 70 frames at 20 FPS
  if (breathingPhase > TWO_PI) breathingPhase -= TWO_PI;

  float breathValue = (sin(breathingPhase) + 1) / 2;
  float brightness = 0.85 + (breathValue * 0.15);  // ±15% variation

  CRGB baseColor = (currentState == STATE_IDLE) ? COLOR_IDLE_EYES : COLOR_ENGAGED_EYES;
  CRGB breathColor = baseColor;
  breathColor.nscale8(brightness * 255);

  fillEyes(breathColor);
}

void applyBlinkingEffect() {
  if (currentState != STATE_IDLE) return;

  unsigned long now = millis();

  // Check if it's time to blink
  if (!isBlinking && now >= nextBlinkTime) {
    isBlinking = true;
    blinkStartTime = now;
  }

  if (isBlinking) {
    unsigned long blinkElapsed = now - blinkStartTime;

    if (blinkElapsed < 100) {
      // Closing (100ms)
      float progress = blinkElapsed / 100.0;
      float brightness = 1.0 - (progress * 0.9);  // Go to 10% brightness

      CRGB blinkColor = COLOR_IDLE_EYES;
      blinkColor.nscale8(brightness * 255);
      fillEyes(blinkColor);

    } else if (blinkElapsed < 200) {
      // Opening (100ms)
      float progress = (blinkElapsed - 100) / 100.0;
      float brightness = 0.1 + (progress * 0.9);  // Return to 100%

      CRGB blinkColor = COLOR_IDLE_EYES;
      blinkColor.nscale8(brightness * 255);
      fillEyes(blinkColor);

    } else {
      // Blink complete
      isBlinking = false;
      nextBlinkTime = now + random(8000, 15000);  // 8-15 seconds
    }
  }
}

// ============================================
// RESPONSE LAYER
// ============================================

void updateMouth() {
  // IDLE state: Show very dark blue mouth (subtle glow)
  if (currentState == STATE_IDLE) {
    CRGB dimBlue = COLOR_IDLE_MOUTH;
    dimBlue.nscale8(30);  // 30/255 = ~12% brightness for subtle presence
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, dimBlue);
    return;
  }

  // Non-speaking states: mouth stays in previous state or off
  if (currentState != STATE_SPEAKING && currentState != STATE_ENGAGED) {
    return;
  }

  // ENGAGED or SPEAKING - respond to amplitude
  if (mouthAmplitude == 0) {
    // Complete BLACK when silent
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);
    return;
  }

  // Logarithmic scaling for better dynamics
  float normalizedAmp = mouthAmplitude / 255.0;
  float scaledAmp = sqrt(normalizedAmp);  // Square root for better response

  // Clear mouth first
  fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);

  CRGB mouthColor = COLOR_ENGAGED_MOUTH;

  // Stage 1: Center LEDs (always first to light)
  if (scaledAmp > 0.0) {
    int centerBright = scaledAmp * 255;
    CRGB centerColor = mouthColor;
    centerColor.nscale8(centerBright);
    mouthLeds[1] = centerColor;
    mouthLeds[6] = centerColor;
  }

  // Stage 2: Corner LEDs (at 20% threshold)
  if (scaledAmp > 0.2) {
    float cornerScale = (scaledAmp - 0.2) / 0.8;
    int cornerBright = cornerScale * 200;  // Max 200 for contrast
    CRGB cornerColor = mouthColor;
    cornerColor.nscale8(cornerBright);
    mouthLeds[0] = cornerColor;
    mouthLeds[7] = cornerColor;
  }

  // Stage 3: Lower-middle LEDs (at 40% threshold)
  if (scaledAmp > 0.4) {
    float lowerScale = (scaledAmp - 0.4) / 0.6;
    int lowerBright = lowerScale * 255;
    CRGB lowerColor = mouthColor;
    lowerColor.nscale8(lowerBright);
    mouthLeds[2] = lowerColor;
    mouthLeds[5] = lowerColor;
  }

  // Stage 4: Bottom LEDs (at 60% threshold)
  if (scaledAmp > 0.6) {
    float bottomScale = (scaledAmp - 0.6) / 0.4;
    int bottomBright = bottomScale * 255;
    CRGB bottomColor = mouthColor;
    bottomColor.nscale8(bottomBright);
    mouthLeds[3] = bottomColor;
    mouthLeds[4] = bottomColor;
  }
}

void updateFlash() {
  if (!flashActive) return;

  // Two green pulses over 300ms
  if (flashStep < 30) {  // 300ms at 100 FPS (10ms intervals)
    float brightness = 0;

    // First pulse (0-100ms)
    if (flashStep < 10) {
      if (flashStep < 3) brightness = flashStep / 3.0;
      else if (flashStep < 5) brightness = 1.0;
      else brightness = 1.0 - ((flashStep - 5) / 5.0);
    }
    // Gap (100-150ms)
    // Second pulse (150-250ms)
    else if (flashStep >= 15 && flashStep < 25) {
      int localStep = flashStep - 15;
      if (localStep < 3) brightness = localStep / 3.0;
      else if (localStep < 5) brightness = 1.0;
      else brightness = 1.0 - ((localStep - 5) / 5.0);
    }

    CRGB flashColor = COLOR_FLASH;
    flashColor.nscale8(brightness * 255);
    fillEyes(flashColor);

    flashStep++;
    delay(10);  // Flash runs at 100 FPS
  } else {
    // Flash complete, return to previous state
    flashActive = false;
    flashStep = 0;
    setState(STATE_ENGAGED);  // Always return to ENGAGED after flash
  }
}

void triggerFlash() {
  flashActive = true;
  flashStep = 0;
}

// ============================================
// UTILITY FUNCTIONS
// ============================================

void fillEyes(CRGB color) {
  fill_solid(eyeLeds, NUM_EYE_LEDS, color);
}

void clearEyes() {
  fill_solid(eyeLeds, NUM_EYE_LEDS, CRGB::Black);
}

void resetSystem() {
  mouthAmplitude = 0;
  flashActive = false;
  flashStep = 0;
  setState(STATE_IDLE);
}

void printHelp() {
  Serial.println("\n=== DJ R3X V3 Commands ===");
  Serial.println("State Commands:");
  Serial.println("  SI - IDLE (orange eyes, dark blue mouth)");
  Serial.println("  SE - ENGAGED (cyan eyes, black mouth)");
  Serial.println("  SL - LISTENING (pulsing)");
  Serial.println("  ST - THINKING (rotating dots)");
  Serial.println("  SS - SPEAKING (pulsing + mouth)");
  Serial.println("  SF - FLASH (green confirmation)");
  Serial.println("\nOther Commands:");
  Serial.println("  Mnnn - Mouth amplitude (0-255)");
  Serial.println("  R - Reset");
  Serial.println("  T - Run test sequence");
  Serial.println("  ? - This help");
  Serial.println("========================\n");
}

void runTestSequence() {
  Serial.println("\n=== Starting Test Sequence ===");

  // Disable normal loop updates during test
  bool savedFlash = flashActive;
  flashActive = false;

  // Test 1: IDLE state
  Serial.println("\nTest 1: IDLE State");
  Serial.println("  Expected: Orange eyes, dark blue mouth");
  setState(STATE_IDLE);
  updateBaseState();
  applyBreathingEffect();
  updateMouth();
  FastLED.show();
  delay(3000);

  // Test 2: ENGAGED state
  Serial.println("\nTest 2: ENGAGED State");
  Serial.println("  Expected: Cyan eyes, BLACK mouth");
  setState(STATE_ENGAGED);
  updateBaseState();
  applyBreathingEffect();
  updateMouth();
  FastLED.show();
  delay(3000);

  // Test 3: LISTENING state
  Serial.println("\nTest 3: LISTENING State");
  Serial.println("  Expected: Pulsing cyan eyes");
  setState(STATE_LISTENING);
  for (int i = 0; i < 60; i++) {  // 3 seconds at 20 FPS
    updateBaseState();
    FastLED.show();
    delay(50);
  }

  // Test 4: THINKING state
  Serial.println("\nTest 4: THINKING State");
  Serial.println("  Expected: White pupils, rotating cyan dots");
  setState(STATE_THINKING);
  for (int i = 0; i < 60; i++) {  // 3 seconds
    updateBaseState();
    FastLED.show();
    delay(50);
  }

  // Test 5: SPEAKING state with mouth amplitude
  Serial.println("\nTest 5: SPEAKING State");
  Serial.println("  Expected: Gentle pulse, mouth responds to amplitude");
  setState(STATE_SPEAKING);

  // Ramp up amplitude
  Serial.println("  Ramping amplitude 0 -> 255");
  for (int amp = 0; amp <= 255; amp += 5) {
    mouthAmplitude = amp;
    updateBaseState();
    updateMouth();
    FastLED.show();
    delay(30);
  }

  // Hold at max
  delay(500);

  // Ramp down amplitude
  Serial.println("  Ramping amplitude 255 -> 0");
  for (int amp = 255; amp >= 0; amp -= 5) {
    mouthAmplitude = amp;
    updateBaseState();
    updateMouth();
    FastLED.show();
    delay(30);
  }

  // Test 6: FLASH
  Serial.println("\nTest 6: FLASH");
  Serial.println("  Expected: Two green pulses, return to ENGAGED");
  triggerFlash();
  while (flashActive) {
    updateFlash();
    FastLED.show();
  }
  delay(1000);

  // Test 7: Mouth amplitude staging
  Serial.println("\nTest 7: Mouth Amplitude Stages");
  setState(STATE_SPEAKING);

  int testAmplitudes[] = {0, 25, 75, 125, 175, 225, 255};
  for (int i = 0; i < 7; i++) {
    mouthAmplitude = testAmplitudes[i];
    Serial.print("  Amplitude: ");
    Serial.println(testAmplitudes[i]);
    updateMouth();
    FastLED.show();
    delay(1000);
  }

  // Return to IDLE
  Serial.println("\n=== Test Complete ===");
  Serial.println("Returning to IDLE state\n");
  setState(STATE_IDLE);
  mouthAmplitude = 0;
  updateBaseState();
  applyBreathingEffect();
  updateMouth();
  FastLED.show();

  flashActive = savedFlash;
  Serial.println("+");
}