/*
 * DJ R3X Eyes Controller - WS2812B RGB Version
 * Controls RGB LED ring animations for eyes
 * Extended protocol with RGB color support
 */

#include <FastLED.h>

// LED Configuration
#define LED_PIN     6
#define NUM_LEDS    14       // 2 rings × 7 LEDs each
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB

// Eye definitions
#define LEFT_EYE_START  0    // LEDs 0-6
#define LEFT_EYE_END    6
#define RIGHT_EYE_START 7    // LEDs 7-13
#define RIGHT_EYE_END   13
#define LEDS_PER_EYE    7

// Default settings
#define DEFAULT_BRIGHTNESS 128  // 0-255 (50%)
#define DEBUG_MODE false

// LED array
CRGB leds[NUM_LEDS];

// Current state
char currentPattern = 'I';  // Default to IDLE
int currentBrightness = DEFAULT_BRIGHTNESS;
CRGB currentColor = CRGB(255, 228, 181);  // Warm white default
unsigned long lastUpdate = 0;
int animationStep = 0;

// Command buffer for multi-character commands
String commandBuffer = "";
bool readingCommand = false;

void setup() {
  // Start serial communication
  Serial.begin(115200);

  // Initialize FastLED
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(currentBrightness);
  FastLED.clear();
  FastLED.show();

  // Set initial pattern (IDLE)
  setPattern('I');

  // Send ready message
  Serial.println("+");
  Serial.flush();
}

void loop() {
  // Process serial commands
  if (Serial.available() > 0) {
    char inChar = (char)Serial.read();

    // Ignore newlines and carriage returns when not reading a command
    if (!readingCommand && (inChar == '\n' || inChar == '\r')) {
      return;
    }

    // Start of color command
    if (inChar == 'C') {
      commandBuffer = "C";
      readingCommand = true;
      return;
    }

    // Start of brightness command
    if (inChar == 'B') {
      commandBuffer = "B";
      readingCommand = true;
      return;
    }

    // Building a multi-character command
    if (readingCommand) {
      if (inChar == '\n' || inChar == '\r') {
        // Command complete, process it
        processMultiCharCommand(commandBuffer);
        commandBuffer = "";
        readingCommand = false;
      } else {
        commandBuffer += inChar;

        // Check if we have a complete command
        if (commandBuffer.startsWith("C") && commandBuffer.length() == 7) {
          // Color command: CRRGGBB (1 + 6 chars)
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        } else if (commandBuffer.startsWith("B") && commandBuffer.length() == 4) {
          // Brightness command: Bnnn (1 + 3 digits)
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        }
      }
      return;
    }

    // Single-character command (pattern, brightness 0-9, reset)
    if (inChar != '\n' && inChar != '\r') {
      processSingleCharCommand(inChar);
    }
  }

  // Update animations
  updateEyeAnimation();
  delay(20);  // Small delay for stability
}

void processSingleCharCommand(char cmd) {
  bool success = true;

  switch (cmd) {
    // Pattern commands
    case 'I': // IDLE
    case 'S': // SPEAKING
    case 'T': // THINKING
    case 'L': // LISTENING
    case 'E': // ENGAGED
    case 'H': // HAPPY
    case 'D': // SAD
    case 'A': // ANGRY
      clearEyes();
      setPattern(cmd);
      break;

    // Reset command
    case 'R':
      resetEyes();
      break;

    // Legacy brightness commands (0-9 maps to 0-255)
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
      setBrightness((cmd - '0') * 28);  // Map 0-9 to 0-252
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

void processMultiCharCommand(String cmd) {
  bool success = true;

  // Clear any stale data from serial buffer
  while (Serial.available() > 0 && (Serial.peek() == '\n' || Serial.peek() == '\r')) {
    Serial.read();  // Consume trailing newlines
  }

  // DEBUG: Print received command
  Serial.print("DBG:RX:");
  Serial.print(cmd);
  Serial.print(":LEN:");
  Serial.println(cmd.length());

  if (cmd.startsWith("C") && cmd.length() == 7) {
    // Color command: CRRGGBB
    String hexColor = cmd.substring(1);  // Remove 'C'
    long colorValue = strtol(hexColor.c_str(), NULL, 16);

    int r = (colorValue >> 16) & 0xFF;
    int g = (colorValue >> 8) & 0xFF;
    int b = colorValue & 0xFF;

    Serial.print("DBG:COLOR:R=");
    Serial.print(r);
    Serial.print(",G=");
    Serial.print(g);
    Serial.print(",B=");
    Serial.println(b);

    setColor(r, g, b);

  } else if (cmd.startsWith("B") && cmd.length() == 4) {
    // Brightness command: Bnnn
    String brightnessStr = cmd.substring(1);  // Remove 'B'
    int brightness = brightnessStr.toInt();

    Serial.print("DBG:BRIGHTNESS:");
    Serial.println(brightness);

    setBrightness(brightness);

  } else {
    Serial.print("DBG:ERROR:Invalid command or length");
    Serial.println();
    success = false;
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
    case 'I': // IDLE - All LEDs solid color
      fillEyes(currentColor);
      FastLED.show();
      break;

    case 'S': // SPEAKING - Will be animated
      // Initial state - will pulse in animation
      fillEyes(currentColor);
      FastLED.show();
      break;

    case 'T': // THINKING - Rotating dot
      // Initial state - center LED
      clearEyes();
      setEyeLED(LEFT_EYE_START, 0, currentColor);
      setEyeLED(RIGHT_EYE_START, 0, currentColor);
      FastLED.show();
      break;

    case 'L': // LISTENING - Pulsing rotation
      fillEyes(currentColor);
      FastLED.show();
      break;

    case 'E': // ENGAGED - Breathing effect
      fillEyes(currentColor);
      FastLED.show();
      break;

    case 'H': // HAPPY - Sparkle effect
      fillEyes(currentColor);
      FastLED.show();
      break;

    case 'D': // SAD - Slow breathing
      fillEyes(currentColor);
      FastLED.show();
      break;

    case 'A': // ANGRY - Fast pulsing
      fillEyes(currentColor);
      FastLED.show();
      break;

    default:
      // Default to IDLE
      currentPattern = 'I';
      fillEyes(currentColor);
      FastLED.show();
      break;
  }
}

void updateEyeAnimation() {
  // Only update every 80ms for smooth animations
  if (millis() - lastUpdate < 80) {
    return;
  }

  lastUpdate = millis();

  // Update animation based on current pattern
  switch (currentPattern) {
    case 'T': // THINKING - Dual counter-rotating scanning (complex processing)
      {
        clearEyes();

        // LEFT EYE: Clockwise rotation
        int leftPos = animationStep % LEDS_PER_EYE;

        // RIGHT EYE: Counter-clockwise rotation (creates thinking/processing feel)
        int rightPos = (LEDS_PER_EYE - 1 - (animationStep % LEDS_PER_EYE)) % LEDS_PER_EYE;

        // Main bright pixel
        setEyeLED(LEFT_EYE_START, leftPos, currentColor);
        setEyeLED(RIGHT_EYE_START, rightPos, currentColor);

        // Trailing fade (2 pixels behind at 60% and 30% brightness)
        CRGB trail1 = currentColor;
        trail1.nscale8(153); // 60%
        CRGB trail2 = currentColor;
        trail2.nscale8(77);  // 30%

        int leftTrail1 = (leftPos - 1 + LEDS_PER_EYE) % LEDS_PER_EYE;
        int leftTrail2 = (leftPos - 2 + LEDS_PER_EYE) % LEDS_PER_EYE;
        int rightTrail1 = (rightPos + 1) % LEDS_PER_EYE;
        int rightTrail2 = (rightPos + 2) % LEDS_PER_EYE;

        setEyeLED(LEFT_EYE_START, leftTrail1, trail1);
        setEyeLED(LEFT_EYE_START, leftTrail2, trail2);
        setEyeLED(RIGHT_EYE_START, rightTrail1, trail1);
        setEyeLED(RIGHT_EYE_START, rightTrail2, trail2);

        FastLED.show();
        animationStep = (animationStep + 1) % LEDS_PER_EYE;
      }
      break;

    case 'S': // SPEAKING - Wave pattern radiating outward (expressive)
      {
        clearEyes();

        // Create waves radiating outward from center position (simulates speech)
        // Multiple waves with different phases for dynamic effect
        int phase1 = animationStep % LEDS_PER_EYE;
        int phase2 = (animationStep + 2) % LEDS_PER_EYE;
        int phase3 = (animationStep + 4) % LEDS_PER_EYE;

        // Layer multiple waves at different brightness
        CRGB wave1Color = currentColor;
        wave1Color.nscale8(255); // Full brightness

        CRGB wave2Color = currentColor;
        wave2Color.nscale8(180); // 70%

        CRGB wave3Color = currentColor;
        wave3Color.nscale8(100); // 40%

        // Apply waves to both eyes (synchronized for coherent speech)
        for (int eye = 0; eye < 2; eye++) {
          int eyeStart = (eye == 0) ? LEFT_EYE_START : RIGHT_EYE_START;

          // Add wave pixels (blend if they overlap)
          leds[eyeStart + phase1] += wave1Color;
          leds[eyeStart + phase2] += wave2Color;
          leds[eyeStart + phase3] += wave3Color;
        }

        FastLED.show();
        animationStep++;
      }
      break;

    case 'L': // LISTENING - Symmetrical expanding/contracting wave (attentive)
      {
        clearEyes();

        // Create synchronized expanding pulse from "top" of ring
        // Both eyes expand outward from position 0 simultaneously
        int waveSize = (animationStep % 4) + 1; // Wave expands 1-4 LEDs then contracts

        // Brightness levels for wave effect (brightest at leading edge)
        byte brightLevels[4] = {255, 200, 120, 60};

        for (int i = 0; i < waveSize && i < 4; i++) {
          CRGB waveColor = currentColor;
          waveColor.nscale8(brightLevels[i]);

          // Expand outward from position 0 (clockwise and counter-clockwise)
          int posClockwise = i % LEDS_PER_EYE;
          int posCounter = (LEDS_PER_EYE - i) % LEDS_PER_EYE;

          // Both eyes synchronized
          setEyeLED(LEFT_EYE_START, posClockwise, waveColor);
          setEyeLED(LEFT_EYE_START, posCounter, waveColor);
          setEyeLED(RIGHT_EYE_START, posClockwise, waveColor);
          setEyeLED(RIGHT_EYE_START, posCounter, waveColor);
        }

        FastLED.show();
        animationStep++;

        // Reset after full expand/contract cycle
        if (animationStep >= 8) { // 4 expanding + 4 contracting
          animationStep = 0;
        }
      }
      break;

    case 'E': // ENGAGED - Gentle single-color breathing (alert but calm)
      {
        // Smooth breathing effect with current color (not rainbow - too playful)
        // Sine wave for natural breathing rhythm
        float breathCycle = sin(animationStep * 0.08);  // Slower, calmer breathing

        // Map sine wave (-1 to 1) to brightness range (60% to 100%)
        int breathBrightness = currentBrightness * (0.6 + (breathCycle * 0.4));
        breathBrightness = constrain(breathBrightness, 0, 255);

        // Add subtle "pupil dilation" effect - center LED slightly brighter
        fillEyes(currentColor);

        // Center position (position 0) gets extra brightness during inhale
        if (breathCycle > 0) {
          CRGB centerBright = currentColor;
          centerBright.nscale8(255); // Max brightness
          setEyeLED(LEFT_EYE_START, 0, centerBright);
          setEyeLED(RIGHT_EYE_START, 0, centerBright);
        }

        FastLED.setBrightness(breathBrightness);
        FastLED.show();
        FastLED.setBrightness(currentBrightness);

        animationStep++;
      }
      break;

    case 'H': // HAPPY - Green sparkle
      {
        // Random sparkles on green base
        fillEyes(CRGB::Green);

        // Add random bright pixels
        if (random(10) < 3) {
          int randomLED = random(NUM_LEDS);
          leds[randomLED] = CRGB::Yellow;
        }

        FastLED.show();
        animationStep++;
      }
      break;

    case 'D': // SAD - Slow blue breathing
      {
        // Slow breathing effect with blue color
        int breath = 64 + (sin(animationStep * 0.05) * 64);

        FastLED.setBrightness(breath);
        fillEyes(CRGB::Blue);
        FastLED.show();
        FastLED.setBrightness(currentBrightness);

        animationStep++;
      }
      break;

    case 'A': // ANGRY - Fast red pulsing
      {
        // Fast pulsing red
        int pulse = (animationStep % 10 < 5) ? 255 : 64;

        FastLED.setBrightness(pulse);
        fillEyes(CRGB::Red);
        FastLED.show();
        FastLED.setBrightness(currentBrightness);

        animationStep++;
      }
      break;

    case 'I': // IDLE - Static (no animation)
    default:
      // No animation for idle
      break;
  }
}

void setColor(int r, int g, int b) {
  currentColor = CRGB(r, g, b);

  // Update display with new color (keep current pattern)
  setPattern(currentPattern);
}

void setBrightness(int brightness) {
  // Ensure brightness is in valid range (0-255)
  currentBrightness = constrain(brightness, 0, 255);
  FastLED.setBrightness(currentBrightness);
  FastLED.show();
}

void resetEyes() {
  FastLED.setBrightness(DEFAULT_BRIGHTNESS);
  currentBrightness = DEFAULT_BRIGHTNESS;
  currentColor = CRGB(255, 228, 181);  // Warm white
  animationStep = 0;

  clearEyes();
  setPattern('I');
}

void clearEyes() {
  FastLED.clear();
}

void fillEyes(CRGB color) {
  for (int i = 0; i < NUM_LEDS; i++) {
    leds[i] = color;
  }
}

void setEyeLED(int eyeStart, int ledIndex, CRGB color) {
  // Set a specific LED in an eye (0-6)
  if (ledIndex >= 0 && ledIndex < LEDS_PER_EYE) {
    leds[eyeStart + ledIndex] = color;
  }
}
