/*
 * DJ R3X Face Controller - FIXED VERSION
 * Fixes color changes, thinking animation, mouth reset, and pulsing issues
 *
 * Hardware:
 * - Eyes: Pin 6, 14 LEDs (2 rings of 7)
 * - Mouth: Pin 5, 8 LEDs (V-shaped)
 * - Board: MEGA 2560 (256-byte buffer required)
 *
 * FIXES APPLIED:
 * - Proper color updates when patterns change
 * - Brighter thinking animation with correct colors
 * - Mouth properly resets to 0 after speech
 * - Better amplitude scaling for mouth dynamics
 * - Cleaner ENGAGED breathing without conflicts
 */

// CRITICAL: Increase serial buffer for MEGA 2560
#ifdef __AVR_ATmega2560__
  #define SERIAL_RX_BUFFER_SIZE 256
  #define SERIAL_TX_BUFFER_SIZE 256
#endif

#include <FastLED.h>

// LED Configuration
#define EYE_PIN 6
#define NUM_EYE_LEDS 14
#define LEDS_PER_EYE 7
#define LEFT_EYE_START 0
#define RIGHT_EYE_START 7

#define MOUTH_PIN 5
#define NUM_MOUTH_LEDS 8

// LED Arrays
CRGB eyeLeds[NUM_EYE_LEDS];
CRGB mouthLeds[NUM_MOUTH_LEDS];

// State Variables
char currentPattern = 'I';
char previousPattern = 'I';
CRGB currentColor = CRGB(255, 120, 0);  // Orange default
CRGB previousColor = CRGB(255, 120, 0);
int currentBrightness = 128;

// Mouth state
int mouthAmplitude = 0;
CRGB mouthColor = CRGB(0, 100, 255);  // Blue default

// Animation state
unsigned long lastUpdate = 0;
int animationStep = 0;

// Command parsing
String commandBuffer = "";
bool readingCommand = false;

// Blink state for IDLE
unsigned long nextBlinkTime = 0;
unsigned long blinkStartTime = 0;
bool isBlinking = false;

void setup() {
  Serial.begin(115200);

  // Initialize FastLED
  FastLED.addLeds<WS2812B, EYE_PIN, GRB>(eyeLeds, NUM_EYE_LEDS);
  FastLED.addLeds<WS2812B, MOUTH_PIN, GRB>(mouthLeds, NUM_MOUTH_LEDS);
  FastLED.setBrightness(currentBrightness);

  // Clear all LEDs
  FastLED.clear(true);

  // Set initial pattern
  setPattern('I');

  // Send ready signal
  Serial.println("+");
  Serial.flush();
}

void loop() {
  // Process serial commands
  while (Serial.available() > 0) {
    char inChar = Serial.read();

    // Ignore newlines/returns when not reading command
    if (!readingCommand && (inChar == '\n' || inChar == '\r')) {
      continue;
    }

    // Multi-character command starts
    if (!readingCommand) {
      if (inChar == 'C' || inChar == 'B' || inChar == 'M' || inChar == 'T') {
        commandBuffer = String(inChar);
        readingCommand = true;
        continue;
      }
    }

    // Building multi-character command
    if (readingCommand) {
      if (inChar == '\n' || inChar == '\r') {
        processMultiCharCommand(commandBuffer);
        commandBuffer = "";
        readingCommand = false;
      } else {
        commandBuffer += inChar;

        // Check for complete commands
        if (commandBuffer.startsWith("C") && commandBuffer.length() == 7) {
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        } else if (commandBuffer.startsWith("B") && commandBuffer.length() == 4) {
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        } else if (commandBuffer.startsWith("M") && commandBuffer.length() == 4) {
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        } else if (commandBuffer == "TALK") {
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        }
      }
      continue;
    }

    // Single character command
    if (inChar != '\n' && inChar != '\r') {
      processSingleCharCommand(inChar);
    }
  }

  // Update animations
  updateEyeAnimation();
  updateMouth();

  // Single show for both eyes and mouth
  FastLED.show();
}

void processSingleCharCommand(char cmd) {
  bool success = true;

  switch(cmd) {
    case 'I': // IDLE
    case 'E': // ENGAGED
    case 'L': // LISTENING
    case 'T': // THINKING
    case 'S': // SPEAKING
    case 'F': // FLASH
    case 'H': // HAPPY
    case 'D': // SAD
    case 'A': // ANGRY
      setPattern(cmd);
      break;

    case 'R': // Reset
      resetSystem();
      break;

    // TEST COMMANDS
    case '1': // Test all patterns sequentially
      runPatternTest();
      break;

    case '2': // Test mouth amplitude sweep
      runMouthTest();
      break;

    case '3': // Test color transitions
      runColorTest();
      break;

    case '4': // Test interactive sequence (engage -> listen -> think -> speak)
      runInteractiveTest();
      break;

    case '5': // Test mouth with speech simulation
      runSpeechTest();
      break;

    case '?': // Print help menu
      printHelp();
      break;

    default:
      success = false;
      break;
  }

  Serial.println(success ? "+" : "-");
  Serial.flush();
}

void processMultiCharCommand(String cmd) {
  bool success = true;

  if (cmd.startsWith("C") && cmd.length() == 7) {
    // Color command: CRRGGBB
    String hexColor = cmd.substring(1);
    long colorValue = strtol(hexColor.c_str(), NULL, 16);

    int r = (colorValue >> 16) & 0xFF;
    int g = (colorValue >> 8) & 0xFF;
    int b = colorValue & 0xFF;

    setColor(r, g, b);

  } else if (cmd.startsWith("B") && cmd.length() == 4) {
    // Brightness: Bnnn
    int brightness = cmd.substring(1).toInt();
    setBrightness(brightness);

  } else if (cmd.startsWith("M") && cmd.length() == 4) {
    // Mouth amplitude: Mnnn (fire-and-forget, no response)
    mouthAmplitude = constrain(cmd.substring(1).toInt(), 0, 255);
    return;  // No response for mouth commands

  } else if (cmd == "TALK") {
    // Test talking animation
    runTalkTest();
    success = true;

  } else {
    success = false;
  }

  Serial.println(success ? "+" : "-");
  Serial.flush();
}

void setPattern(char pattern) {
  // Save previous for flash return
  if (pattern == 'F') {
    previousPattern = currentPattern;
    previousColor = currentColor;
  }

  currentPattern = pattern;
  animationStep = 0;
  lastUpdate = millis();

  // Update colors based on pattern
  switch(pattern) {
    case 'I': // IDLE - Orange eyes, blue mouth
      currentColor = CRGB(255, 120, 0);
      mouthColor = CRGB(0, 100, 255);
      fillEyes(currentColor);
      break;

    case 'E': // ENGAGED - Cyan eyes, golden mouth
      currentColor = CRGB(0, 255, 255);
      mouthColor = CRGB(255, 200, 0);
      fillEyes(currentColor);
      // Force an immediate update to make sure cyan shows
      FastLED.show();
      break;

    case 'L': // LISTENING - Keep current color, dark blue mouth
      mouthColor = CRGB(0, 50, 200);
      fillEyes(currentColor);
      break;

    case 'T': // THINKING - Keep current color, purple mouth
      mouthColor = CRGB(128, 0, 255);
      clearEyes();
      break;

    case 'S': // SPEAKING - Keep current color and mouth color
      fillEyes(currentColor);
      break;

    case 'F': // FLASH - Will be animated
      break;

    case 'H': // HAPPY - Green
      currentColor = CRGB(0, 255, 0);
      fillEyes(currentColor);
      break;

    case 'D': // SAD - Blue
      currentColor = CRGB(0, 0, 255);
      fillEyes(currentColor);
      break;

    case 'A': // ANGRY - Red
      currentColor = CRGB(255, 0, 0);
      fillEyes(currentColor);
      break;
  }
}

void setColor(int r, int g, int b) {
  // Store the new color
  currentColor = CRGB(r, g, b);

  // Don't override pattern-specific colors for certain patterns
  // Only apply custom colors if we're in a pattern that uses currentColor
  if (currentPattern == 'L' || currentPattern == 'S') {
    // These patterns use the currentColor variable
    fillEyes(currentColor);
  }
  // For other patterns, don't immediately apply - let the pattern handle it
}

void setBrightness(int brightness) {
  currentBrightness = constrain(brightness, 0, 255);
  FastLED.setBrightness(currentBrightness);
}

void updateEyeAnimation() {
  unsigned long currentTime = millis();

  // Update rates based on pattern
  unsigned long updateInterval = 50;  // Default 20fps

  if (currentPattern == 'F' || (currentPattern == 'I' && isBlinking)) {
    updateInterval = 10;  // 100fps for flash and blinks
  }

  if (currentTime - lastUpdate < updateInterval) {
    return;
  }

  lastUpdate = currentTime;

  switch(currentPattern) {
    case 'I': // IDLE - Breathing with blinks
      animateIdle();
      break;

    case 'E': // ENGAGED - Simple breathing
      animateEngaged();
      break;

    case 'L': // LISTENING - Heartbeat pulse
      animateListening();
      break;

    case 'T': // THINKING - Rotating dots
      animateThinking();
      break;

    case 'S': // SPEAKING - Gentle pulse
      animateSpeaking();
      break;

    case 'F': // FLASH - Green pulses
      animateFlash();
      break;

    case 'H': // HAPPY - Sparkle
      animateHappy();
      break;

    case 'D': // SAD - Slow breathing
      animateSad();
      break;

    case 'A': // ANGRY - Fast pulse
      animateAngry();
      break;
  }

  animationStep++;
}

void animateIdle() {
  // Breathing effect
  float breathCycle = sin(animationStep * 0.025) * 0.15;
  float breathMultiplier = 1.0 + breathCycle;

  CRGB breathingColor = currentColor;
  breathingColor.nscale8(breathMultiplier * 255);

  // Handle blinking
  unsigned long currentTime = millis();

  if (nextBlinkTime == 0) {
    nextBlinkTime = currentTime + random(3000, 7000);
  }

  if (!isBlinking && currentTime >= nextBlinkTime) {
    isBlinking = true;
    blinkStartTime = currentTime;
  }

  if (isBlinking) {
    unsigned long blinkElapsed = currentTime - blinkStartTime;

    if (blinkElapsed < 100) {
      // Closing
      float progress = blinkElapsed / 100.0;
      breathingColor.nscale8((1.0 - progress * 0.9) * 255);
    } else if (blinkElapsed < 200) {
      // Opening
      float progress = (blinkElapsed - 100) / 100.0;
      breathingColor.nscale8((0.1 + progress * 0.9) * 255);
    } else {
      // Blink complete
      isBlinking = false;
      nextBlinkTime = currentTime + random(3000, 7000);
    }
  }

  fillEyes(breathingColor);
}

void animateEngaged() {
  // Very subtle breathing - almost imperceptible
  float breathCycle = (animationStep % 70) / 70.0;  // Slower cycle
  float breathValue = (sin(breathCycle * TWO_PI) + 1) / 2;

  // MUCH subtler modulation: 90% to 100% brightness
  float brightnessMod = 0.9 + (breathValue * 0.1);

  CRGB breathingColor = currentColor;
  breathingColor.nscale8(brightnessMod * 255);

  fillEyes(breathingColor);
}

void animateListening() {
  // Heartbeat pulse
  int cycle = animationStep % 20;

  float pulseBrightness;
  if (cycle < 5) {
    pulseBrightness = cycle / 5.0;
  } else {
    pulseBrightness = 1.0 - ((cycle - 5) / 15.0);
  }

  pulseBrightness = 0.5 + (pulseBrightness * 0.5);

  CRGB pulsingColor = currentColor;
  pulsingColor.nscale8(pulseBrightness * 255);

  fillEyes(pulsingColor);
}

void animateThinking() {
  // Clear eyes first
  clearEyes();

  // Bright white center pupils - ALWAYS visible
  eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);
  eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);

  // Rotating dots on outer ring - use BRIGHT color
  int leftPos = ((animationStep / 3) % 6) + 1;
  int rightPos = 6 - ((animationStep / 3) % 6) + 1;

  // Use bright cyan for the rotating dots so they're VERY visible
  CRGB dotColor = CRGB(0, 255, 255);  // Bright cyan

  // Set the rotating dots
  eyeLeds[LEFT_EYE_START + leftPos] = dotColor;
  eyeLeds[RIGHT_EYE_START + rightPos] = dotColor;

  // Add trailing dots for better visibility (dimmer)
  int leftTrail = ((leftPos - 2 + 6) % 6) + 1;
  int rightTrail = ((rightPos + 2 - 1) % 6) + 1;

  CRGB trailColor = CRGB(0, 128, 128);  // Dimmer cyan
  eyeLeds[LEFT_EYE_START + leftTrail] = trailColor;
  eyeLeds[RIGHT_EYE_START + rightTrail] = trailColor;
}

void animateSpeaking() {
  // Simple pulse
  float pulse = (sin(animationStep * 0.1) + 1) / 2;
  float brightnessMod = 0.7 + (pulse * 0.3);

  CRGB pulsingColor = currentColor;
  pulsingColor.nscale8(brightnessMod * 255);

  fillEyes(pulsingColor);
}

void animateFlash() {
  // Two green flashes then return
  if (animationStep < 30) {
    float brightness = 0.0;

    if (animationStep < 10) {
      // First flash
      if (animationStep < 3) brightness = animationStep / 3.0;
      else if (animationStep < 5) brightness = 1.0;
      else brightness = 1.0 - ((animationStep - 5) / 5.0);
    } else if (animationStep >= 15 && animationStep < 25) {
      // Second flash
      int localStep = animationStep - 15;
      if (localStep < 3) brightness = localStep / 3.0;
      else if (localStep < 5) brightness = 1.0;
      else brightness = 1.0 - ((localStep - 5) / 5.0);
    }

    CRGB flashColor = CRGB(0, 255, 0);
    flashColor.nscale8(brightness * 255);
    fillEyes(flashColor);
  } else {
    // Return to previous
    currentColor = previousColor;
    setPattern(previousPattern);
  }
}

void animateHappy() {
  fillEyes(currentColor);
  // Add random sparkles
  if (random(10) < 3) {
    eyeLeds[random(NUM_EYE_LEDS)] = CRGB(255, 255, 0);
  }
}

void animateSad() {
  float breath = 64 + (sin(animationStep * 0.05) * 64);
  CRGB sadColor = currentColor;
  sadColor.nscale8(breath);
  fillEyes(sadColor);
}

void animateAngry() {
  int pulse = (animationStep % 10 < 5) ? 255 : 64;
  CRGB angryColor = currentColor;
  angryColor.nscale8(pulse);
  fillEyes(angryColor);
}

void updateMouth() {
  // CRITICAL FIX: Actually check the amplitude value!
  // If amplitude is 0, close the mouth properly

  // Special case: ENGAGED mode with no amplitude = completely black
  if (currentPattern == 'E' && mouthAmplitude == 0) {
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);
    return;
  }

  // For other modes, show small baseline when amplitude is 0
  if (mouthAmplitude == 0) {
    // Just a dim baseline glow
    CRGB dimColor = mouthColor;
    dimColor.nscale8(20);  // Very dim

    // Only center LEDs dimly lit
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);
    mouthLeds[1] = dimColor;
    mouthLeds[6] = dimColor;
    return;
  }

  // Scale amplitude for better dynamics
  // Apply logarithmic scaling for more natural speech visualization
  float openness = mouthAmplitude / 255.0;
  openness = sqrt(openness);  // Square root for better dynamics

  // Center LEDs (always brightest)
  int centerBright = openness * 255;
  mouthLeds[1] = mouthColor;
  mouthLeds[1].nscale8(centerBright);
  mouthLeds[6] = mouthColor;
  mouthLeds[6].nscale8(centerBright);

  // Corner LEDs (bloom at 20% threshold)
  if (openness > 0.2) {
    float cornerOpen = (openness - 0.2) / 0.8;
    int cornerBright = cornerOpen * 200;  // Max 200 to keep contrast
    mouthLeds[0] = mouthColor;
    mouthLeds[0].nscale8(cornerBright);
    mouthLeds[7] = mouthColor;
    mouthLeds[7].nscale8(cornerBright);
  } else {
    mouthLeds[0] = CRGB::Black;
    mouthLeds[7] = CRGB::Black;
  }

  // Lower-middle LEDs (bloom at 35% threshold)
  if (openness > 0.35) {
    float lowerOpen = (openness - 0.35) / 0.65;
    int lowerBright = lowerOpen * 255;
    mouthLeds[2] = mouthColor;
    mouthLeds[2].nscale8(lowerBright);
    mouthLeds[5] = mouthColor;
    mouthLeds[5].nscale8(lowerBright);
  } else {
    mouthLeds[2] = CRGB::Black;
    mouthLeds[5] = CRGB::Black;
  }

  // Bottom LEDs (bloom at 60% threshold)
  if (openness > 0.6) {
    float bottomOpen = (openness - 0.6) / 0.4;
    int bottomBright = bottomOpen * 255;
    mouthLeds[3] = mouthColor;
    mouthLeds[3].nscale8(bottomBright);
    mouthLeds[4] = mouthColor;
    mouthLeds[4].nscale8(bottomBright);
  } else {
    mouthLeds[3] = CRGB::Black;
    mouthLeds[4] = CRGB::Black;
  }
}

void fillEyes(CRGB color) {
  fill_solid(eyeLeds, NUM_EYE_LEDS, color);
}

void clearEyes() {
  fill_solid(eyeLeds, NUM_EYE_LEDS, CRGB::Black);
}

void resetSystem() {
  currentPattern = 'I';
  currentColor = CRGB(255, 120, 0);
  mouthColor = CRGB(0, 100, 255);
  currentBrightness = 128;
  mouthAmplitude = 0;
  animationStep = 0;
  FastLED.setBrightness(currentBrightness);
  setPattern('I');
}

// ============== TEST FUNCTIONS ==============

void printHelp() {
  Serial.println("\n=== DJ R3X LED TEST COMMANDS ===");
  Serial.println("Pattern Commands:");
  Serial.println("  I - IDLE (orange eyes, blue mouth)");
  Serial.println("  E - ENGAGED (cyan eyes, golden mouth)");
  Serial.println("  L - LISTENING (current color, dark blue mouth)");
  Serial.println("  T - THINKING (rotating dots, purple mouth)");
  Serial.println("  S - SPEAKING (pulsing eyes, current mouth)");
  Serial.println("  F - FLASH (green confirmation)");
  Serial.println("  H - HAPPY (green sparkle)");
  Serial.println("  D - SAD (blue breathing)");
  Serial.println("  A - ANGRY (red pulsing)");
  Serial.println("  R - RESET to defaults");
  Serial.println("\nTest Commands:");
  Serial.println("  1 - Test all patterns (2s each)");
  Serial.println("  2 - Test mouth amplitude sweep");
  Serial.println("  3 - Test color transitions");
  Serial.println("  4 - Test interactive sequence");
  Serial.println("  5 - Test speech simulation");
  Serial.println("  ? - Show this help");
  Serial.println("\nMulti-char Commands:");
  Serial.println("  CRRGGBB - Set eye color (hex)");
  Serial.println("  Bnnn    - Set brightness (0-255)");
  Serial.println("  Mnnn    - Set mouth amplitude (0-255)");
  Serial.println("  TALK    - 10-second talking test");
  Serial.println("================================\n");
}

void runPatternTest() {
  Serial.println("Starting pattern test (2s each)...");

  const char patterns[] = {'I', 'E', 'L', 'T', 'S', 'H', 'D', 'A', 'F'};
  const char* names[] = {"IDLE", "ENGAGED", "LISTENING", "THINKING",
                         "SPEAKING", "HAPPY", "SAD", "ANGRY", "FLASH"};

  for (int i = 0; i < 9; i++) {
    Serial.print("Testing: ");
    Serial.println(names[i]);
    setPattern(patterns[i]);

    // Animate for 2 seconds
    unsigned long startTime = millis();
    while (millis() - startTime < 2000) {
      updateEyeAnimation();
      updateMouth();
      FastLED.show();
      delay(10);
    }
  }

  // Return to IDLE
  setPattern('I');
  Serial.println("Pattern test complete!");
}

void runMouthTest() {
  Serial.println("Starting mouth amplitude test...");
  setPattern('E');  // ENGAGED mode for best visibility

  // Test amplitude sweep from 0 to 255 and back
  Serial.println("Sweeping 0 -> 255 -> 0");

  // Up sweep
  for (int i = 0; i <= 255; i += 5) {
    mouthAmplitude = i;
    updateMouth();
    FastLED.show();
    delay(20);
  }

  delay(500);  // Hold at max

  // Down sweep
  for (int i = 255; i >= 0; i -= 5) {
    mouthAmplitude = i;
    updateMouth();
    FastLED.show();
    delay(20);
  }

  // Test critical thresholds
  Serial.println("Testing threshold points:");
  int thresholds[] = {0, 51, 89, 153, 255};  // 0%, 20%, 35%, 60%, 100%
  const char* levels[] = {"Closed", "Center only", "Corners added", "Lower added", "Full open"};

  for (int i = 0; i < 5; i++) {
    Serial.print("  ");
    Serial.print(levels[i]);
    Serial.print(" (");
    Serial.print(thresholds[i]);
    Serial.println(")");

    mouthAmplitude = thresholds[i];
    updateMouth();
    FastLED.show();
    delay(1500);
  }

  mouthAmplitude = 0;
  setPattern('I');
  Serial.println("Mouth test complete!");
}

void runColorTest() {
  Serial.println("Starting color transition test...");

  // Test each pattern's default color
  Serial.println("Testing pattern colors:");

  setPattern('I');
  Serial.println("  IDLE - Orange");
  delay(1500);

  setPattern('E');
  Serial.println("  ENGAGED - Cyan");
  delay(1500);

  setPattern('H');
  Serial.println("  HAPPY - Green");
  delay(1500);

  setPattern('D');
  Serial.println("  SAD - Blue");
  delay(1500);

  setPattern('A');
  Serial.println("  ANGRY - Red");
  delay(1500);

  // Test custom colors
  Serial.println("Testing custom colors on ENGAGED:");
  setPattern('E');

  CRGB testColors[] = {
    CRGB(255, 0, 255),   // Magenta
    CRGB(255, 255, 0),   // Yellow
    CRGB(0, 255, 0),     // Green
    CRGB(0, 0, 255),     // Blue
    CRGB(255, 255, 255)  // White
  };

  const char* colorNames[] = {"Magenta", "Yellow", "Green", "Blue", "White"};

  for (int i = 0; i < 5; i++) {
    Serial.print("  ");
    Serial.println(colorNames[i]);
    currentColor = testColors[i];

    // Animate with new color
    unsigned long startTime = millis();
    while (millis() - startTime < 1000) {
      updateEyeAnimation();
      FastLED.show();
      delay(10);
    }
  }

  setPattern('I');
  Serial.println("Color test complete!");
}

void runInteractiveTest() {
  Serial.println("Starting interactive sequence simulation...");
  Serial.println("Simulating: IDLE -> ENGAGE -> LISTEN -> THINK -> SPEAK -> FLASH -> ENGAGED");

  // Start in IDLE
  Serial.println("1. IDLE mode (2s)");
  setPattern('I');
  animateFor(2000);

  // User says "engage"
  Serial.println("2. ENGAGED mode (2s)");
  setPattern('E');
  animateFor(2000);

  // User clicks to record
  Serial.println("3. LISTENING mode (3s)");
  setPattern('L');
  animateFor(3000);

  // Processing input
  Serial.println("4. THINKING mode (2s)");
  setPattern('T');
  animateFor(2000);

  // AI responds
  Serial.println("5. SPEAKING mode with mouth animation (3s)");
  setPattern('S');

  // Simulate speech with varying amplitude
  unsigned long startTime = millis();
  while (millis() - startTime < 3000) {
    // Simulate speech amplitude
    float t = (millis() - startTime) / 3000.0;
    float amplitude = sin(t * 20) * sin(t * PI);  // Modulated sine wave
    mouthAmplitude = abs(amplitude) * 200 + 55;  // Range 55-255

    updateEyeAnimation();
    updateMouth();
    FastLED.show();
    delay(10);
  }

  mouthAmplitude = 0;  // Speech ends

  // Flash confirmation
  Serial.println("6. FLASH confirmation");
  setPattern('F');
  animateFor(500);

  // Back to ENGAGED
  Serial.println("7. Return to ENGAGED");
  setPattern('E');
  animateFor(2000);

  setPattern('I');
  Serial.println("Interactive test complete!");
}

void runSpeechTest() {
  Serial.println("Starting speech simulation test...");
  Serial.println("Testing mouth response to speech patterns");

  setPattern('S');  // Speaking mode

  // Test 1: Quiet speech
  Serial.println("1. Quiet speech (low amplitude)");
  for (int i = 0; i < 100; i++) {
    mouthAmplitude = 30 + random(40);  // 30-70 range
    updateEyeAnimation();
    updateMouth();
    FastLED.show();
    delay(30);
  }

  mouthAmplitude = 0;
  delay(500);

  // Test 2: Normal speech
  Serial.println("2. Normal speech (medium amplitude)");
  for (int i = 0; i < 100; i++) {
    mouthAmplitude = 80 + random(80);  // 80-160 range
    updateEyeAnimation();
    updateMouth();
    FastLED.show();
    delay(30);
  }

  mouthAmplitude = 0;
  delay(500);

  // Test 3: Loud/excited speech
  Serial.println("3. Loud speech (high amplitude)");
  for (int i = 0; i < 100; i++) {
    mouthAmplitude = 150 + random(105);  // 150-255 range
    updateEyeAnimation();
    updateMouth();
    FastLED.show();
    delay(30);
  }

  // Test 4: Verify mouth closes
  Serial.println("4. Testing mouth close (should go to baseline/black)");
  mouthAmplitude = 0;
  updateMouth();
  FastLED.show();
  delay(2000);

  // Test 5: Pattern-specific mouth behavior
  Serial.println("5. Testing ENGAGED vs IDLE mouth baseline");

  Serial.println("  ENGAGED with amplitude=0 (should be BLACK)");
  setPattern('E');
  mouthAmplitude = 0;
  updateMouth();
  FastLED.show();
  delay(2000);

  Serial.println("  IDLE with amplitude=0 (should have dim blue glow)");
  setPattern('I');
  mouthAmplitude = 0;
  updateMouth();
  FastLED.show();
  delay(2000);

  Serial.println("Speech test complete!");
}

// Helper function to animate for a specific duration
void animateFor(unsigned long duration) {
  unsigned long startTime = millis();
  while (millis() - startTime < duration) {
    updateEyeAnimation();
    updateMouth();
    FastLED.show();
    delay(10);
  }
}

void runTalkTest() {
  Serial.println("Starting 10-second TALK test...");
  setPattern('S');  // Speaking mode

  unsigned long startTime = millis();
  while (millis() - startTime < 10000) {  // 10 seconds
    // Generate realistic speech pattern
    float t = (millis() - startTime) / 10000.0;  // 0 to 1 over 10 seconds

    // Create syllable-like patterns
    float syllableRate = 8.0;  // syllables per second
    float syllable = sin(t * syllableRate * TWO_PI);

    // Add sentence-level modulation
    float sentenceWave = sin(t * 3 * TWO_PI);

    // Combine for realistic speech
    float amplitude = (syllable * 0.5 + 0.5) * (sentenceWave * 0.3 + 0.7);

    // Add some randomness
    amplitude += (random(100) / 1000.0) - 0.05;
    amplitude = constrain(amplitude, 0, 1);

    // Set mouth amplitude (with minimum of 20 to keep some movement)
    mouthAmplitude = 20 + (amplitude * 235);

    updateEyeAnimation();
    updateMouth();
    FastLED.show();
    delay(20);
  }

  mouthAmplitude = 0;
  setPattern('I');
  Serial.println("TALK test complete!");
}