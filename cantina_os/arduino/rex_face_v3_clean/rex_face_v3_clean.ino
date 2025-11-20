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
 *   T - Run full automated test sequence
 *   T1 - Test IDLE state only
 *   T2 - Test ENGAGED state only
 *   T3 - Test LISTENING state only
 *   T4 - Test THINKING state only
 *   T5 - Test SPEAKING state only
 *   T6 - Test FLASH only
 *   T7 - Test mouth amplitude staging only
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
const CRGB COLOR_IDLE_EYES_OUTER = CRGB(255, 60, 0);    // Reddish-orange outer ring
const CRGB COLOR_IDLE_EYES_CENTER = CRGB(255, 180, 80); // Light peachy-orange center pupils with more red
const CRGB COLOR_IDLE_MOUTH = CRGB(0, 50, 150);          // Subtle blue glow (tube amp aesthetic)
const CRGB COLOR_ENGAGED_EYES = CRGB(0, 35, 110);        // Darker blue outer ring (dimmer than center)
const CRGB COLOR_ENGAGED_CENTER = CRGB(100, 180, 255);   // Light blue center pupils for ENGAGED states
const CRGB COLOR_ENGAGED_MOUTH = CRGB(255, 60, 0);       // Reddish-orange (same as IDLE outer ring)
const CRGB COLOR_THINKING_DOT = CRGB(0, 255, 255);       // Cyan dots
const CRGB COLOR_FLASH = CRGB(0, 255, 0);                // Green

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

AnimationTimer breathingTimer = {0, 20, 0};    // 50 FPS - smoother breathing
AnimationTimer blinkTimer = {0, 10, 0};        // 100 FPS when active
AnimationTimer pulseTimer = {0, 20, 0};        // 50 FPS - smoother pulsing
AnimationTimer thinkingTimer = {0, 30, 0};     // ~33 FPS - smooth rotation
AnimationTimer mouthGlowTimer = {0, 16, 0};    // 60 FPS - buttery smooth IDLE mouth animation

// ============================================
// ANIMATION STATE
// ============================================

// Breathing effect
float breathingPhase = 0;
float mouthGlowPhase = 0;

// Mouth glow continuous variation (no interruptions)
float mouthBrightnessOffset = 0;  // Slowly drifting brightness offset
float mouthSpeedMultiplier = 1.0;  // Slowly drifting speed multiplier
float mouthBrightnessDrift = 0.002;  // How fast brightness offset changes
float mouthSpeedDrift = 0.001;  // How fast speed changes

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
  mouthGlowTimer.lastUpdate = now;
  nextBlinkTime = now + random(12000, 25000);  // 12-25 seconds - less frequent

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

    // Handle test commands
    if (c == 'T' && !readingCommand) {
      commandBuffer = "T";
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
      // Test commands (T, T1-T7)
      else if (commandBuffer[0] == 'T' && (commandBuffer.length() == 1 || commandBuffer.length() == 2)) {
        if (commandBuffer.length() == 1 || c == '\n' || c == '\r') {
          handleTestCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        } else if (commandBuffer.length() == 2 && c >= '1' && c <= '7') {
          handleTestCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        }
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
    else if (c == '\n' || c == '\r') {
      // Ignore newlines
    }
  }
}

void handleTestCommand(String cmd) {
  if (cmd == "T") {
    runTestSequence();
  } else if (cmd == "T1") {
    runTest1();
  } else if (cmd == "T2") {
    runTest2();
  } else if (cmd == "T3") {
    runTest3();
  } else if (cmd == "T4") {
    runTest4();
  } else if (cmd == "T5") {
    runTest5();
  } else if (cmd == "T6") {
    runTest6();
  } else if (cmd == "T7") {
    runTest7();
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
      setIdleEyes();
      nextBlinkTime = millis() + random(12000, 25000);  // 12-25 seconds - less frequent
      break;

    case STATE_ENGAGED:
      setEngagedEyes();  // Dark blue outer ring, light blue center
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

void setIdleEyes() {
  // Outer ring LEDs get reddish-orange
  for (int i = 0; i < LEDS_PER_EYE; i++) {
    if (i == 0) {
      // Center pupils are peachy-orange
      eyeLeds[LEFT_EYE_START + i] = COLOR_IDLE_EYES_CENTER;
      eyeLeds[RIGHT_EYE_START + i] = COLOR_IDLE_EYES_CENTER;
    } else {
      // Outer ring is reddish-orange
      eyeLeds[LEFT_EYE_START + i] = COLOR_IDLE_EYES_OUTER;
      eyeLeds[RIGHT_EYE_START + i] = COLOR_IDLE_EYES_OUTER;
    }
  }
}

void setEngagedEyes() {
  // Set outer ring to dark blue, center to light blue
  for (int i = 0; i < LEDS_PER_EYE; i++) {
    if (i == 0) {
      // Center pupils are light blue
      eyeLeds[LEFT_EYE_START + i] = COLOR_ENGAGED_CENTER;
      eyeLeds[RIGHT_EYE_START + i] = COLOR_ENGAGED_CENTER;
    } else {
      // Outer ring is dark blue
      eyeLeds[LEFT_EYE_START + i] = COLOR_ENGAGED_EYES;
      eyeLeds[RIGHT_EYE_START + i] = COLOR_ENGAGED_EYES;
    }
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

  // Dynamic heartbeat pulse (60 BPM at 50 FPS)
  int cycle = pulseTimer.step % 50;  // 1 second at 50 FPS

  float brightness;
  if (cycle < 10) {
    // Quick rise (200ms at 50 FPS)
    brightness = 0.3 + (cycle / 10.0) * 0.7;  // 30% to 100%
  } else {
    // Slow fall (800ms at 50 FPS)
    brightness = 1.0 - ((cycle - 10) / 40.0) * 0.7;  // 100% to 30%
  }

  // Apply pulse to both center (light blue) and outer (dark blue)
  for (int i = 0; i < LEDS_PER_EYE; i++) {
    CRGB baseColor = (i == 0) ? COLOR_ENGAGED_CENTER : COLOR_ENGAGED_EYES;
    CRGB pulseColor = baseColor;
    pulseColor.nscale8(brightness * 255);
    eyeLeds[LEFT_EYE_START + i] = pulseColor;
    eyeLeds[RIGHT_EYE_START + i] = pulseColor;
  }

  pulseTimer.step++;
}

void updateThinkingAnimation(unsigned long now) {
  if (now - thinkingTimer.lastUpdate < thinkingTimer.interval) return;
  thinkingTimer.lastUpdate = now;

  // Start with standard two-tone blue eyes (same as other states)
  setEngagedEyes();  // Light blue center + darker blue outer ring

  // TWO rotating dots (counter-rotating) - adjacent LEDs
  // Overlay cyan dots on top of the blue ring
  int leftPos1 = ((thinkingTimer.step / 2) % 6) + 1;
  int leftPos2 = (leftPos1 % 6) + 1;  // Next LED in ring
  int rightPos1 = 7 - ((thinkingTimer.step / 2) % 6);
  int rightPos2 = ((rightPos1 - 7) % 6) + 7;  // Previous LED in ring

  // Overlay cyan dots on the outer ring
  eyeLeds[LEFT_EYE_START + leftPos1] = COLOR_THINKING_DOT;
  eyeLeds[LEFT_EYE_START + leftPos2] = COLOR_THINKING_DOT;
  eyeLeds[RIGHT_EYE_START + rightPos1] = COLOR_THINKING_DOT;
  eyeLeds[RIGHT_EYE_START + rightPos2] = COLOR_THINKING_DOT;

  thinkingTimer.step++;
}

void updateSpeakingPulse(unsigned long now) {
  if (now - pulseTimer.lastUpdate < pulseTimer.interval) return;
  pulseTimer.lastUpdate = now;

  // Gentle pulse while speaking
  float pulse = (sin(pulseTimer.step * 0.1) + 1) / 2;
  float brightness = 0.7 + (pulse * 0.3);  // 70-100%

  // Apply pulse to both center (light blue) and outer (dark blue)
  for (int i = 0; i < LEDS_PER_EYE; i++) {
    CRGB baseColor = (i == 0) ? COLOR_ENGAGED_CENTER : COLOR_ENGAGED_EYES;
    CRGB pulseColor = baseColor;
    pulseColor.nscale8(brightness * 255);
    eyeLeds[LEFT_EYE_START + i] = pulseColor;
    eyeLeds[RIGHT_EYE_START + i] = pulseColor;
  }

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

  // Gentle breathing (3.5 second cycle at 50 FPS)
  breathingPhase += 0.036;  // Complete cycle in ~175 frames at 50 FPS = 3.5 seconds
  if (breathingPhase > TWO_PI) breathingPhase -= TWO_PI;

  float breathValue = (sin(breathingPhase) + 1) / 2;
  float brightness = 0.85 + (breathValue * 0.15);  // ±15% variation

  if (currentState == STATE_IDLE) {
    // IDLE: Apply breathing to both center (peachy-orange) and outer (reddish-orange)
    for (int i = 0; i < LEDS_PER_EYE; i++) {
      CRGB baseColor = (i == 0) ? COLOR_IDLE_EYES_CENTER : COLOR_IDLE_EYES_OUTER;
      CRGB breathColor = baseColor;
      breathColor.nscale8(brightness * 255);
      eyeLeds[LEFT_EYE_START + i] = breathColor;
      eyeLeds[RIGHT_EYE_START + i] = breathColor;
    }
  } else {
    // ENGAGED: Breathing with light blue center, dark blue outer
    for (int i = 0; i < LEDS_PER_EYE; i++) {
      CRGB baseColor = (i == 0) ? COLOR_ENGAGED_CENTER : COLOR_ENGAGED_EYES;
      CRGB breathColor = baseColor;
      breathColor.nscale8(brightness * 255);
      eyeLeds[LEFT_EYE_START + i] = breathColor;
      eyeLeds[RIGHT_EYE_START + i] = breathColor;
    }
  }
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

      // Apply blink to both center and outer
      for (int i = 0; i < LEDS_PER_EYE; i++) {
        CRGB baseColor = (i == 0) ? COLOR_IDLE_EYES_CENTER : COLOR_IDLE_EYES_OUTER;
        CRGB blinkColor = baseColor;
        blinkColor.nscale8(brightness * 255);
        eyeLeds[LEFT_EYE_START + i] = blinkColor;
        eyeLeds[RIGHT_EYE_START + i] = blinkColor;
      }

    } else if (blinkElapsed < 200) {
      // Opening (100ms)
      float progress = (blinkElapsed - 100) / 100.0;
      float brightness = 0.1 + (progress * 0.9);  // Return to 100%

      // Apply blink to both center and outer
      for (int i = 0; i < LEDS_PER_EYE; i++) {
        CRGB baseColor = (i == 0) ? COLOR_IDLE_EYES_CENTER : COLOR_IDLE_EYES_OUTER;
        CRGB blinkColor = baseColor;
        blinkColor.nscale8(brightness * 255);
        eyeLeds[LEFT_EYE_START + i] = blinkColor;
        eyeLeds[RIGHT_EYE_START + i] = blinkColor;
      }

    } else {
      // Blink complete
      isBlinking = false;
      nextBlinkTime = now + random(12000, 25000);  // 12-25 seconds - less frequent
    }
  }
}

// ============================================
// RESPONSE LAYER
// ============================================

void updateMouth() {
  // IDLE state: Organic tube amp glow with continuous smooth variation
  if (currentState == STATE_IDLE) {
    unsigned long now = millis();

    // Frame-rate limit at 60 FPS for buttery smoothness
    if (now - mouthGlowTimer.lastUpdate < mouthGlowTimer.interval) return;
    mouthGlowTimer.lastUpdate = now;

    // Clear all first
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);

    // Continuous smooth drifting (no sudden changes)
    // Brightness offset slowly oscillates
    mouthBrightnessOffset += mouthBrightnessDrift;
    if (mouthBrightnessOffset > TWO_PI) mouthBrightnessOffset -= TWO_PI;

    // Speed multiplier slowly oscillates between 0.7x and 1.3x
    mouthSpeedMultiplier = 1.0 + (sin(mouthBrightnessOffset * 0.3) * 0.3);

    // Main pulse: 20 second cycle (faster than before)
    mouthGlowPhase += 0.00523 * mouthSpeedMultiplier;  // ~1200 frames at 60 FPS = 20 seconds
    if (mouthGlowPhase > TWO_PI) mouthGlowPhase -= TWO_PI;

    float glowValue = (sin(mouthGlowPhase) + 1) / 2;  // 0.0 to 1.0

    // Brightness with continuous drift: 20%-38% base range
    float brightnessDrift = sin(mouthBrightnessOffset) * 0.05;  // ±5% smooth drift
    float brightness = 0.20 + (glowValue * 0.18) + brightnessDrift;
    brightness = constrain(brightness, 0.15, 0.42);

    // Start with base blue
    CRGB tubeGlow = COLOR_IDLE_MOUTH;

    // Add orange warmth as brightness increases (subtle, always present)
    // More orange at peaks, less at valleys - CONTINUOUS, not random
    if (glowValue > 0.5) {
      float warmth = (glowValue - 0.5) / 0.5;  // 0.0 to 1.0 in top 50%
      warmth = warmth * warmth;  // Square for more contrast at peak

      // Add red and green for orange/amber warmth
      int warmAdd = warmth * 35;  // Subtle orange glow
      tubeGlow.r = min(255, tubeGlow.r + warmAdd);
      tubeGlow.g = min(255, tubeGlow.g + (warmAdd * 0.4));
    }

    tubeGlow.nscale8(brightness * 255);

    // Only light the middle of the V (LEDs 1, 2, 5, 6 form the V shape)
    mouthLeds[1] = tubeGlow;  // Left inner
    mouthLeds[2] = tubeGlow;  // Left middle
    mouthLeds[5] = tubeGlow;  // Right middle
    mouthLeds[6] = tubeGlow;  // Right inner

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

  // Logarithmic scaling for better dynamics with good sensitivity
  float normalizedAmp = mouthAmplitude / 255.0;
  // Square root (x^0.5) - balanced compression for dynamic speech
  float scaledAmp = sqrt(normalizedAmp);

  // Apply ANOTHER square root for more aggressive compression (cube root-like)
  // This makes it MUCH harder to hit max brightness
  scaledAmp = sqrt(scaledAmp);  // Now effectively x^0.25 but applied post sqrt for smoothness

  // Clear mouth first
  fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);

  CRGB mouthColor = COLOR_ENGAGED_MOUTH;

  // Stage 1: TRUE MIDDLE (LEDs 1, 6 only) - always first to light
  // Starts very dim, grows slowly
  if (scaledAmp > 0.0) {
    float stage1Scale = min(1.0, scaledAmp / 0.4);  // 0-40% scales this stage 0-100%
    int middleBright = stage1Scale * stage1Scale * 180;  // Quadratic, max 180 (reduced from 255)
    CRGB middleColor = mouthColor;

    // ONLY add white if OVERALL amplitude is EXTREMELY loud (95%+)
    if (scaledAmp > 0.95) {
      float whiteness = (scaledAmp - 0.95) / 0.05;  // 0.0 to 1.0 in top 5% ONLY
      int whiteAdd = whiteness * whiteness * 40;  // Quadratic, max 40 white (very subtle)
      middleColor.r = min(255, middleColor.r + whiteAdd);
      middleColor.g = min(255, middleColor.g + whiteAdd);
      middleColor.b = min(255, middleColor.b + whiteAdd);
    }

    middleColor.nscale8(middleBright);
    mouthLeds[1] = middleColor;  // Inner top left
    mouthLeds[6] = middleColor;  // Inner top right
  }

  // Stage 2: Expand to middle sides (LEDs 2, 5) - NO WHITE
  if (scaledAmp > 0.30) {
    float stage2Scale = (scaledAmp - 0.30) / 0.30;  // 30-60% scales this stage 0-100%
    stage2Scale = min(1.0, stage2Scale);
    int sideBright = stage2Scale * stage2Scale * 160;  // Quadratic, max 160 (reduced from 245)
    CRGB sideColor = mouthColor;
    sideColor.nscale8(sideBright);
    mouthLeds[2] = sideColor;  // Middle left
    mouthLeds[5] = sideColor;  // Middle right
  }

  // Stage 3: Expand to corners (UP) - NO WHITE
  if (scaledAmp > 0.60) {
    float stage3Scale = (scaledAmp - 0.60) / 0.20;  // 60-80% scales this stage 0-100%
    stage3Scale = min(1.0, stage3Scale);
    int cornerBright = stage3Scale * stage3Scale * 140;  // Quadratic, max 140 (reduced from 230)
    CRGB cornerColor = mouthColor;
    cornerColor.nscale8(cornerBright);
    mouthLeds[0] = cornerColor;  // Top left corner
    mouthLeds[7] = cornerColor;  // Top right corner
  }

  // Stage 4: Expand to bottom (DOWN) - only at high volumes
  if (scaledAmp > 0.80) {
    float stage4Scale = (scaledAmp - 0.80) / 0.20;  // 80-100% scales this stage 0-100%
    stage4Scale = min(1.0, stage4Scale);
    int bottomBright = stage4Scale * stage4Scale * 200;  // Quadratic, max 200 (reduced from 240)
    CRGB bottomColor = mouthColor;
    bottomColor.nscale8(bottomBright);
    mouthLeds[3] = bottomColor;  // Bottom left
    mouthLeds[4] = bottomColor;  // Bottom right
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
  Serial.println("  SI - IDLE (white center, orange ring, dark blue mouth)");
  Serial.println("  SE - ENGAGED (cyan eyes, black mouth)");
  Serial.println("  SL - LISTENING (pulsing)");
  Serial.println("  ST - THINKING (rotating dots)");
  Serial.println("  SS - SPEAKING (pulsing + mouth)");
  Serial.println("  SF - FLASH (green confirmation)");
  Serial.println("\nMouth Commands:");
  Serial.println("  Mnnn - Mouth amplitude (0-255)");
  Serial.println("\nTest Commands:");
  Serial.println("  T   - Run full test sequence");
  Serial.println("  T1  - Test IDLE state");
  Serial.println("  T2  - Test ENGAGED state");
  Serial.println("  T3  - Test LISTENING state");
  Serial.println("  T4  - Test THINKING state");
  Serial.println("  T5  - Test SPEAKING state");
  Serial.println("  T6  - Test FLASH");
  Serial.println("  T7  - Test mouth amplitude staging");
  Serial.println("\nOther Commands:");
  Serial.println("  R - Reset");
  Serial.println("  ? - This help");
  Serial.println("========================\n");
}

void runTestSequence() {
  Serial.println("\n=== Starting Full Test Sequence ===");
  runTest1();
  delay(500);
  runTest2();
  delay(500);
  runTest3();
  delay(500);
  runTest4();
  delay(500);
  runTest5();
  delay(500);
  runTest6();
  delay(500);
  runTest7();
  Serial.println("\n=== Full Test Complete ===");
  Serial.println("Returning to IDLE state\n");
  setState(STATE_IDLE);
  mouthAmplitude = 0;
  updateBaseState();
  applyBreathingEffect();
  updateMouth();
  FastLED.show();
  Serial.println("+");
}

void runTest1() {
  Serial.println("\n[T1] IDLE State");
  Serial.println("  Expected: White center pupils, reddish-orange outer ring, dark blue mouth");
  setState(STATE_IDLE);
  updateBaseState();
  applyBreathingEffect();
  updateMouth();
  FastLED.show();
  delay(3000);
  Serial.println("+");
}

void runTest2() {
  Serial.println("\n[T2] ENGAGED State");
  Serial.println("  Expected: Cyan eyes, BLACK mouth");
  setState(STATE_ENGAGED);
  updateBaseState();
  applyBreathingEffect();
  updateMouth();
  FastLED.show();
  delay(3000);
  Serial.println("+");
}

void runTest3() {
  Serial.println("\n[T3] LISTENING State");
  Serial.println("  Expected: Pulsing cyan eyes");
  setState(STATE_LISTENING);
  for (int i = 0; i < 60; i++) {  // 3 seconds at 20 FPS
    updateBaseState();
    FastLED.show();
    delay(50);
  }
  Serial.println("+");
}

void runTest4() {
  Serial.println("\n[T4] THINKING State");
  Serial.println("  Expected: White pupils, rotating cyan dots");
  setState(STATE_THINKING);
  for (int i = 0; i < 60; i++) {  // 3 seconds
    updateBaseState();
    FastLED.show();
    delay(50);
  }
  Serial.println("+");
}

void runTest5() {
  Serial.println("\n[T5] SPEAKING State");
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
  Serial.println("+");
}

void runTest6() {
  Serial.println("\n[T6] FLASH");
  Serial.println("  Expected: Two green pulses, return to ENGAGED");
  triggerFlash();
  while (flashActive) {
    updateFlash();
    FastLED.show();
  }
  delay(1000);
  Serial.println("+");
}

void runTest7() {
  Serial.println("\n[T7] Mouth Amplitude Stages");
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
  Serial.println("+");
}

