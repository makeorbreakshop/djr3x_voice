/*
 * DJ R3X Face Controller - WS2812B RGB Version
 * Controls eyes (2x7 LED rings) and mouth (8 LED V-shape)
 * Extended protocol with RGB color support
 * OPTIMIZED FOR ARDUINO MEGA 2560 (8KB SRAM, 4 Serial Ports)
 *
 * Hardware:
 * - Eyes: Pin 6, 14 LEDs (2 rings of 7)
 * - Mouth: Pin 5, 8 LEDs (V-shaped, fills bottom-up)
 * - Board: MEGA 2560 R3 or PRO (ATmega2560)
 *
 * Serial Commands:
 * - Pattern: I/L/T/S/E/F/H/D/A (Idle/Listen/Think/Speak/Engage/Flash/Happy/Sad/Angry)
 * - Color: CRRGGBB (hex RGB for eyes)
 * - Brightness: Bnnn (000-255 for eyes)
 * - Mouth Amplitude: Mnnn (000-255, VU meter fills bottom-up)
 * - Test Talking: TALK (10-second animated speech pattern)
 *
 * CHANGELOG:
 * 2025-11-19 @ 12:30 PM - Redesigned interaction animations (ENGAGED/LISTENING/THINKING/SPEAKING) with green completion flash
 * 2025-11-19 @ 3:00 PM - Fixed FLASH pattern in Python, LISTENING triggers on recording (not transcripts), sped up THINKING (1.5s)
 * 2025-11-19 @ 3:20 PM - Flash now runs at 100fps - TWO super fast smooth green pulses (0.3s total)
 * 2025-11-20 - Added MEGA 2560 optimizations and increased buffer size to 256 bytes
 * 2025-11-19 @ 1:45 PM - Added mouth control: 8 LED V-shape on pin 5 with amplitude VU meter, wake-up yawn animation, and TALK test command
 * 2025-11-19 @ 3:15 PM - ENGAGED pattern: Changed from rotating searchlight to smooth breathing cyan ring with white pupils (3.5s cycle)
 * 2025-11-19 @ 3:15 PM - Mouth idle state: Added dim blue baseline (50/255 brightness) - mouth always lit, blooms brighter when talking
 * 2025-11-19 @ 3:15 PM - CRITICAL FIX: Removed ALL debug Serial.print() statements - was polluting serial protocol causing Python timeouts
 */

// CRITICAL: Increase serial buffer for MEGA 2560 (8KB SRAM available!)
// This MUST be defined before any includes to take effect
#ifdef __AVR_ATmega2560__
  #define SERIAL_RX_BUFFER_SIZE 256  // Increase from default 64 bytes
  #define SERIAL_TX_BUFFER_SIZE 256  // 4x larger buffer = way less overflow!
#endif

#include <FastLED.h>

// Eye LED Configuration
#define EYE_PIN     6
#define NUM_EYE_LEDS    14       // 2 rings × 7 LEDs each
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB

// Mouth LED Configuration
#define MOUTH_PIN   5
#define NUM_MOUTH_LEDS  8        // V-shaped: LEDs 0-3 (left), 4-7 (right)

// Eye definitions
#define LEFT_EYE_START  0    // LEDs 0-6
#define LEFT_EYE_END    6
#define RIGHT_EYE_START 7    // LEDs 7-13
#define RIGHT_EYE_END   13
#define LEDS_PER_EYE    7

// Default settings
#define DEFAULT_BRIGHTNESS 128  // 0-255 (50%)
#define DEBUG_MODE false

// LED arrays
CRGB eyeLeds[NUM_EYE_LEDS];
CRGB mouthLeds[NUM_MOUTH_LEDS];

// Eye state
char currentPattern = 'I';  // Default to IDLE
char previousPattern = 'I';  // For returning after flash
int currentBrightness = DEFAULT_BRIGHTNESS;
CRGB currentColor = CRGB(255, 120, 0);  // Bright orange (more orange, less red)
CRGB previousColor;  // For returning after flash
unsigned long lastUpdate = 0;
int animationStep = 0;

// Idle animation state (breathing + independent eye blinking)
// Left eye blink state
unsigned long leftBlinkStartTime = 0;
unsigned long leftNextBlinkTime = 0;
bool leftIsBlinking = false;
int leftBlinkCloseTime = 50;
int leftBlinkOpenTime = 100;
int leftBlinkDepth = 100;
bool leftDoubleBlinkQueued = false;

// Right eye blink state
unsigned long rightBlinkStartTime = 0;
unsigned long rightNextBlinkTime = 0;
bool rightIsBlinking = false;
int rightBlinkCloseTime = 50;
int rightBlinkOpenTime = 100;
int rightBlinkDepth = 100;
bool rightDoubleBlinkQueued = false;

// Mouth state
int mouthAmplitude = 0;       // 0-255 audio amplitude
bool mouthTestTalking = false; // Test talking animation active
unsigned long mouthTestStartTime = 0;
CRGB mouthColor = CRGB(0, 100, 255);  // Blue

// Command buffer for multi-character commands
String commandBuffer = "";
bool readingCommand = false;

// Forward declarations
void fillEyes(CRGB color, bool whitePupil = true);
void clearEyes();
void setEyeLED(int eyeStart, int ledIndex, CRGB color);
void playWakeUpAnimation();
void updateMouth();
void setMouthAmplitude(int amplitude);
void startMouthTestTalking();
int calculateEyeBlinkBrightness(bool &isBlinking, unsigned long &startTime, unsigned long &nextBlinkTime,
                                 int &closeTime, int &openTime, int &depth, bool &doubleQueued,
                                 unsigned long currentTime, const char* eyeName);

void setup() {
  // Start serial communication
  Serial.begin(115200);

  // Initialize FastLED for eyes
  FastLED.addLeds<LED_TYPE, EYE_PIN, COLOR_ORDER>(eyeLeds, NUM_EYE_LEDS);
  FastLED.setBrightness(currentBrightness);

  // Initialize FastLED for mouth
  FastLED.addLeds<LED_TYPE, MOUTH_PIN, COLOR_ORDER>(mouthLeds, NUM_MOUTH_LEDS);

  // Clear all LEDs
  FastLED.clear(true);

  // WAKE-UP ANIMATION DISABLED FOR FASTER TESTING
  // Uncomment the line below to enable the wake-up animation
  // playWakeUpAnimation();

  // Or trigger it manually with serial command 'W'

  // Set initial pattern (IDLE)
  setPattern('I');

  // Set mouth to idle (blue, low amplitude)
  setMouthAmplitude(0);

  // Send ready message
  Serial.println("+");
  Serial.flush();
}

void loop() {
  // Process ALL available serial commands (drain the buffer completely)
  while (Serial.available() > 0) {
    char inChar = (char)Serial.read();

    // Ignore newlines and carriage returns when not reading a command
    if (!readingCommand && (inChar == '\n' || inChar == '\r')) {
      continue;  // Keep reading more characters
    }

    // Start of color command
    if (inChar == 'C' && !readingCommand) {
      commandBuffer = "C";
      readingCommand = true;
      continue;  // Keep reading more characters
    }

    // Start of brightness command
    if (inChar == 'B' && !readingCommand) {
      commandBuffer = "B";
      readingCommand = true;
      continue;  // Keep reading more characters
    }

    // Start of mouth amplitude command
    if (inChar == 'M' && !readingCommand) {
      commandBuffer = "M";
      readingCommand = true;
      continue;  // Keep reading more characters
    }

    // Start of TALK command
    if (inChar == 'T' && !readingCommand) {
      commandBuffer = "T";
      readingCommand = true;
      continue;  // Keep reading more characters
    }

    // Building a multi-character command
    if (readingCommand) {
      if (inChar == '\n' || inChar == '\r') {
        // Command complete, process it
        if (commandBuffer.length() > 1) {  // Avoid processing empty buffers
          processMultiCharCommand(commandBuffer);
        }
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
        } else if (commandBuffer.startsWith("M") && commandBuffer.length() == 4) {
          // Mouth amplitude command: Mnnn (1 + 3 digits)
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        } else if (commandBuffer == "TALK") {
          // TALK test command (4 chars)
          processMultiCharCommand(commandBuffer);
          commandBuffer = "";
          readingCommand = false;
        }
      }
      continue;  // Keep reading more characters
    }

    // Single-character command (pattern, brightness 0-9, reset)
    if (inChar != '\n' && inChar != '\r') {
      processSingleCharCommand(inChar);
    }
  }

  // Update animations (eyes update LED buffer but don't show yet)
  updateEyeAnimation();

  // Update mouth (updates LED buffer but doesn't show)
  updateMouth();

  // CRITICAL: Single FastLED.show() call for both eyes AND mouth
  // This prevents the two updates from fighting each other
  FastLED.show();

  // No delay - animations self-throttle with their own timing
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
    case 'F': // FLASH (green confirmation)
      clearEyes();
      setPattern(cmd);
      break;

    // Reset command
    case 'R':
      resetEyes();
      break;

    // Wake-up animation command (manual trigger)
    case 'W':
      playWakeUpAnimation();
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

  if (cmd.startsWith("C") && cmd.length() == 7) {
    // Color command: CRRGGBB
    String hexColor = cmd.substring(1);  // Remove 'C'
    long colorValue = strtol(hexColor.c_str(), NULL, 16);

    int r = (colorValue >> 16) & 0xFF;
    int g = (colorValue >> 8) & 0xFF;
    int b = colorValue & 0xFF;

    setColor(r, g, b);

  } else if (cmd.startsWith("B") && cmd.length() == 4) {
    // Brightness command: Bnnn
    String brightnessStr = cmd.substring(1);  // Remove 'B'
    int brightness = brightnessStr.toInt();

    setBrightness(brightness);

  } else if (cmd.startsWith("M") && cmd.length() == 4) {
    // Mouth amplitude command: Mnnn (000-255)
    String amplitudeStr = cmd.substring(1);  // Remove 'M'
    int amplitude = amplitudeStr.toInt();

    setMouthAmplitude(amplitude);
    mouthTestTalking = false;  // Stop test animation if running

  } else if (cmd == "TALK") {
    // Test talking animation command
    startMouthTestTalking();

  } else {
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
  // Save previous pattern (for returning after flash)
  if (pattern == 'F') {
    previousPattern = currentPattern;
    previousColor = currentColor;
  }

  // Update the current pattern
  currentPattern = pattern;

  // Reset animation
  animationStep = 0;
  lastUpdate = millis();

  switch (pattern) {
    case 'I': // IDLE - All LEDs solid color
      fillEyes(currentColor);
      mouthColor = CRGB(0, 100, 255);  // Blue mouth for idle
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'E': // ENGAGED - Breathing cyan eyes with golden yellow mouth
      fillEyes(currentColor);  // Eyes stay cyan (set by Python)
      mouthColor = CRGB(255, 200, 0);  // Golden yellow mouth for engaged
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'S': // SPEAKING - Will be animated
      // Initial state - will pulse in animation
      fillEyes(currentColor);
      // Mouth keeps current color during speaking (animated by amplitude)
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'T': // THINKING - Rotating dot
      // Initial state - center LED
      clearEyes();
      setEyeLED(LEFT_EYE_START, 0, currentColor);
      setEyeLED(RIGHT_EYE_START, 0, currentColor);
      mouthColor = CRGB(128, 0, 255);  // Purple mouth for thinking
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'L': // LISTENING - Pulsing rotation
      fillEyes(currentColor);
      mouthColor = CRGB(0, 50, 200);  // Darker blue mouth for listening
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'F': // FLASH - Green confirmation (auto-returns to previous pattern)
      // Start flash animation (handled in updateEyeAnimation)
      break;

    case 'H': // HAPPY - Sparkle effect
      fillEyes(currentColor);
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'D': // SAD - Slow breathing
      fillEyes(currentColor);
      // FastLED.show(); // Removed - single show() in loop()
      break;

    case 'A': // ANGRY - Fast pulsing
      fillEyes(currentColor);
      // FastLED.show(); // Removed - single show() in loop()
      break;

    default:
      // Default to IDLE
      currentPattern = 'I';
      fillEyes(currentColor);
      // FastLED.show(); // Removed - single show() in loop()
      break;
  }
}

void updateEyeAnimation() {
  unsigned long currentTime = millis();

  // Optimized update intervals for smooth 60-100fps animations:
  // - IDLE blinking: 10ms (100 fps) for buttery smooth fades
  // - IDLE breathing: 50ms (20 fps) for smooth breathing cycles
  // - FLASH: 10ms (100 fps) for super fast confirmation
  // - Other patterns: 50ms (20 fps) for responsive, smooth animations
  bool isIdleBlinking = (currentPattern == 'I' && leftIsBlinking);
  bool isIdleBreathing = (currentPattern == 'I' && !leftIsBlinking);
  bool isFlash = (currentPattern == 'F');

  unsigned long updateInterval;
  if (isIdleBlinking || isFlash) {
    updateInterval = 10;  // 100 fps - ultra smooth blinks and flash
  } else if (isIdleBreathing) {
    updateInterval = 50;  // 20 fps - smooth breathing
  } else {
    updateInterval = 50;  // 20 fps - smooth animations for all other patterns
  }

  if (currentTime - lastUpdate < updateInterval) {
    return;
  }

  lastUpdate = currentTime;

  // Update animation based on current pattern
  switch (currentPattern) {
    case 'T': // THINKING - Counter-rotating single LED (faster, more distinct)
      {
        clearEyes();

        // White pupils always visible
        eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);
        eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);

        // Calculate ring positions (positions 1-6 for each eye)
        // LEFT EYE: Clockwise rotation (~1.5 seconds per rotation = 30 frames @ 50ms)
        int leftRingPos = ((animationStep / 5) % 6) + 1;  // Divide by 5 for faster rotation (30 frames ≈ 1.5 sec)

        // RIGHT EYE: Counter-clockwise rotation
        int rightRingPos = 6 - ((animationStep / 5) % 6) + 1;  // Mirror direction

        // Single bright LED per ring
        setEyeLED(LEFT_EYE_START, leftRingPos, currentColor);
        setEyeLED(RIGHT_EYE_START, rightRingPos, currentColor);

        // FastLED.show(); // Removed - single show() in loop()
        animationStep++;
      }
      break;

    case 'S': // SPEAKING - Smooth outward pulse waves (expressive, not chaotic)
      {
        // White pupils always visible
        eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);
        eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);

        // Create smooth expanding pulse from center outward
        // Use sine wave for smooth brightness gradient
        int cycle = animationStep % 40;  // 40 frames = 2 second cycle @ 50ms

        // Each ring LED gets a brightness based on distance and time
        for (int i = 1; i < LEDS_PER_EYE; i++) {
          // Calculate phase offset based on LED position (creates outward wave)
          int phaseOffset = i * 7;  // Stagger each LED
          int totalPhase = (cycle + phaseOffset) % 40;

          // Sine wave brightness (smooth pulse)
          float brightness = (sin((totalPhase / 40.0) * TWO_PI) + 1.0) / 2.0;  // 0.0 - 1.0
          brightness = 0.3 + (brightness * 0.7);  // Scale to 30-100%

          CRGB ledColor = currentColor;
          ledColor.nscale8(brightness * 255);

          setEyeLED(LEFT_EYE_START, i, ledColor);
          setEyeLED(RIGHT_EYE_START, i, ledColor);
        }

        // FastLED.show(); // Removed - single show() in loop()
        animationStep++;
      }
      break;

    case 'L': // LISTENING - Heartbeat pulse (clear "I hear you" feedback)
      {
        // Pulse pattern: ~60 bpm (1 beat per second)
        // 20 frames @ 50ms = 1 second cycle
        int cycle = animationStep % 20;

        // Create heartbeat pulse curve (quick up, slow down)
        float pulseBrightness;
        if (cycle < 5) {
          // Quick rise (0-5 frames = 250ms)
          pulseBrightness = cycle / 5.0;  // 0.0 → 1.0
        } else {
          // Slow fall (5-20 frames = 750ms)
          pulseBrightness = 1.0 - ((cycle - 5) / 15.0);  // 1.0 → 0.0
        }

        // Map to brightness range (50% - 100%)
        pulseBrightness = 0.5 + (pulseBrightness * 0.5);

        // Pupils pulse
        CRGB pupilColor = CRGB(255, 255, 255);
        pupilColor.nscale8(pulseBrightness * 255);
        eyeLeds[LEFT_EYE_START] = pupilColor;
        eyeLeds[RIGHT_EYE_START] = pupilColor;

        // Ring pulses in sync
        CRGB ringColor = currentColor;
        ringColor.nscale8(pulseBrightness * 255);

        for (int i = 1; i < LEDS_PER_EYE; i++) {
          setEyeLED(LEFT_EYE_START, i, ringColor);
          setEyeLED(RIGHT_EYE_START, i, ringColor);
        }

        // FastLED.show(); // Removed - single show() in loop()
        animationStep++;
      }
      break;

    case 'E': // ENGAGED - Smooth breathing cyan with white pupils
      {
        // Smooth breathing effect (3.5 second cycle)
        float breatheCycle = (animationStep % 70) / 70.0;  // 70 frames @ 50ms = 3.5 sec
        float breatheValue = (sin(breatheCycle * 2 * PI) + 1) / 2;  // 0.0 to 1.0

        // Modulate brightness ±15% around base
        float brightnessMod = 0.85 + (breatheValue * 0.3);  // 0.85 to 1.15

        // White pupils (constant)
        eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);
        eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);

        // Cyan ring with breathing modulation
        CRGB breathingColor = currentColor;
        breathingColor.nscale8(brightnessMod * 255);

        for (int i = 1; i < LEDS_PER_EYE; i++) {
          setEyeLED(LEFT_EYE_START, i, breathingColor);
          setEyeLED(RIGHT_EYE_START, i, breathingColor);
        }

        // FastLED.show(); // Removed - single show() in loop()
        animationStep++;
      }
      break;

    case 'F': // FLASH - Two quick green confirmation pulses (auto-returns)
      {
        // Flash animation: 30 frames @ 10ms = 0.3 seconds total (100fps - super fast and smooth)
        // First flash: 0-12, gap: 12-16, second flash: 16-28, return: 28+

        if (animationStep < 30) {
          float brightness = 0.0;

          // First flash (frames 0-12)
          if (animationStep < 12) {
            if (animationStep < 4) {
              // Smooth rise
              brightness = animationStep / 4.0;
            } else if (animationStep < 6) {
              // Brief hold
              brightness = 1.0;
            } else {
              // Smooth fall
              brightness = 1.0 - ((animationStep - 6) / 6.0);
            }
          }
          // Gap between flashes (frames 12-16)
          else if (animationStep >= 12 && animationStep < 16) {
            brightness = 0.0;  // Dark gap
          }
          // Second flash (frames 16-28)
          else if (animationStep >= 16 && animationStep < 28) {
            int localStep = animationStep - 16;
            if (localStep < 4) {
              // Smooth rise
              brightness = localStep / 4.0;
            } else if (localStep < 6) {
              // Brief hold
              brightness = 1.0;
            } else {
              // Smooth fall
              brightness = 1.0 - ((localStep - 6) / 6.0);
            }
          }

          // Green color for confirmation
          CRGB greenColor = CRGB(0, 255, 0);
          greenColor.nscale8(brightness * 255);

          // Fill all LEDs with green pulse (including pupils)
          for (int i = 0; i < NUM_EYE_LEDS; i++) {
            eyeLeds[i] = greenColor;
          }

          // FastLED.show(); // Removed - single show() in loop()
          animationStep++;
        } else {
          // Flash complete - return to previous pattern and color
          currentColor = previousColor;
          setPattern(previousPattern);
        }
      }
      break;

    case 'H': // HAPPY - Green sparkle
      {
        // Random sparkles on green base
        fillEyes(CRGB::Green);

        // Add random bright pixels
        if (random(10) < 3) {
          int randomLED = random(NUM_EYE_LEDS);
          eyeLeds[randomLED] = CRGB::Yellow;
        }

        // FastLED.show(); // Removed - single show() in loop()
        animationStep++;
      }
      break;

    case 'D': // SAD - Slow blue breathing
      {
        // Slow breathing effect with blue color
        int breath = 64 + (sin(animationStep * 0.05) * 64);

        FastLED.setBrightness(breath);
        fillEyes(CRGB::Blue);
        // FastLED.show(); // Removed - single show() in loop()
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
        // FastLED.show(); // Removed - single show() in loop()
        FastLED.setBrightness(currentBrightness);

        animationStep++;
      }
      break;

    case 'I': // IDLE - Breathing with synchronized blinking (Disney-style "illusion of life")
    default:
      {
        unsigned long currentTime = millis();

        // Calculate breathing animation (3.5 second cycle, ±15% brightness)
        float breathCycle = sin(animationStep * 0.025) * 0.15;  // 0.025 = speed, 0.15 = 15% amplitude

        // Add subtle micro-movements (±2-3% random jitter) for organic feel
        float microMovement = 0.0;
        if (animationStep % 4 == 0) {  // Every 4th frame at 50ms = 200ms
          microMovement = (random(-30, 30) / 1000.0);  // ±3% variation
        }

        float breathMultiplier = 1.0 + breathCycle + microMovement;
        breathMultiplier = constrain(breathMultiplier, 0.0, 2.0);
        int baseBreathBrightness = currentBrightness * breathMultiplier;
        baseBreathBrightness = constrain(baseBreathBrightness, 0, 255);

        // SYNCHRONIZED BLINKING (both eyes together) - use left eye timing for both
        int blinkBrightness = calculateEyeBlinkBrightness(
          leftIsBlinking, leftBlinkStartTime, leftNextBlinkTime,
          leftBlinkCloseTime, leftBlinkOpenTime, leftBlinkDepth, leftDoubleBlinkQueued,
          currentTime, "BOTH"
        );

        // Apply breathing + blink to BOTH EYES (synchronized)
        int finalBrightness = (blinkBrightness * breathMultiplier);
        finalBrightness = constrain(finalBrightness, 0, 255);

        // Apply to all eye LEDs
        for (int i = 0; i < NUM_EYE_LEDS; i++) {
          eyeLeds[i] = currentColor;
          eyeLeds[i].nscale8(finalBrightness);
        }

        // White pupils
        eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);
        eyeLeds[LEFT_EYE_START].nscale8(finalBrightness);
        eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);
        eyeLeds[RIGHT_EYE_START].nscale8(finalBrightness);

        // Debug logging removed - interferes with serial protocol

        animationStep++;
      }
      break;
  }

  // Don't restore brightness here - will be handled by blink logic if needed
}

void setColor(int r, int g, int b) {
  currentColor = CRGB(r, g, b);

  // Immediately update LEDs with new color (keeping current pattern state)
  // Note: We don't call setPattern() here because it resets animation state
  // Python will send pattern command after color if needed
  if (currentPattern == 'I' || currentPattern == 'E' || currentPattern == 'H' ||
      currentPattern == 'D' || currentPattern == 'S' || currentPattern == 'L') {
    // For patterns that use currentColor, update the display
    fillEyes(currentColor);
    // FastLED.show(); // Removed - single show() in loop()
  }
}

void setBrightness(int brightness) {
  // Ensure brightness is in valid range (0-255)
  currentBrightness = constrain(brightness, 0, 255);
  FastLED.setBrightness(currentBrightness);
  // FastLED.show(); // Removed - single show() in loop()
}

void resetEyes() {
  FastLED.setBrightness(DEFAULT_BRIGHTNESS);
  currentBrightness = DEFAULT_BRIGHTNESS;
  currentColor = CRGB(255, 120, 0);  // Bright orange (matches IDLE)
  animationStep = 0;

  clearEyes();
  setPattern('I');
}

void clearEyes() {
  FastLED.clear();
}

void fillEyes(CRGB color, bool whitePupil = true) {
  // Fill outer ring with color
  for (int i = 0; i < NUM_EYE_LEDS; i++) {
    eyeLeds[i] = color;
  }

  // Set center LED (pupil) to white for contrast/realism
  if (whitePupil) {
    eyeLeds[LEFT_EYE_START] = CRGB(255, 255, 255);   // Left pupil
    eyeLeds[RIGHT_EYE_START] = CRGB(255, 255, 255);  // Right pupil
  }
}

void setEyeLED(int eyeStart, int ledIndex, CRGB color) {
  // Set a specific LED in an eye (0-6)
  if (ledIndex >= 0 && ledIndex < LEDS_PER_EYE) {
    eyeLeds[eyeStart + ledIndex] = color;
  }
}

void playWakeUpAnimation() {
  /*
   * R3X Wake-Up Sequence - Biologically Accurate Human Eye Opening
   * Based on actual human sleep-to-wake physiology (~6 seconds)
   *
   * PHASE 1: Deep Sleep (0.5s) @ 30 fps
   *   - Pupils maximally constricted (parasympathetic nervous system)
   *   - Very dim center LED only (small pupils)
   *   - Outer ring dark (eyelids closed)
   *
   * PHASE 2: Initial Arousal - Pupil Dilation (1.5s) @ 60 fps
   *   - Sympathetic nervous system activates
   *   - Pupils dilate slowly (center LED brightens)
   *   - Outer ring still dark (eyelids not open yet)
   *
   * PHASE 3: Eyelid Flutter (1.0s) @ 100 fps
   *   - Brief partial openings (testing the light)
   *   - Quick brightness spikes then back to dim
   *   - Realistic hesitation before full opening
   *
   * PHASE 4: Gradual Eye Opening (2.0s) @ 60 fps
   *   - Slow, smooth brightness increase
   *   - Outer ring fades in gradually (eyelids opening)
   *   - Non-linear easing (matches eyelid muscle movement)
   *
   * PHASE 5: Light Adjustment Blinks (1.5s) @ 100 fps
   *   - 2 blinks to lubricate and adjust to brightness
   *   - Natural asymmetric timing (fast close, slow open)
   */

  CRGB idleColor = CRGB(255, 120, 0);  // Bright orange (constant color)

  // PHASE 1: Deep Sleep - Small constricted pupils only (500ms @ 30fps)
  clearEyes();
  // Very dim center LEDs only (constricted pupils in deep sleep)
  eyeLeds[LEFT_EYE_START] = CRGB(10, 2, 0);   // Barely visible pupils
  eyeLeds[RIGHT_EYE_START] = CRGB(10, 2, 0);
  // Mouth completely closed
  fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);
  FastLED.setBrightness(15);  // Very dim
  FastLED.show();  // Required in setup() before loop() starts
  delay(500);

  // PHASE 2: Initial Arousal - Pupil Dilation (1500ms @ 60fps = 90 frames)
  // Sympathetic nervous system activates, pupils slowly dilate
  // Smooth 60fps for buttery pupil dilation
  for (int step = 0; step <= 90; step++) {
    clearEyes();

    // Smooth pupil dilation curve (ease-out)
    float progress = step / 90.0;
    float eased = 1.0 - (1.0 - progress) * (1.0 - progress);  // Quadratic ease-out

    // Pupils brighten gradually (dilation)
    int pupilBrightness = 10 + (eased * 70);  // 10 → 80
    CRGB pupilColor = CRGB(
      map(pupilBrightness, 10, 80, 10, idleColor.r),
      map(pupilBrightness, 10, 80, 2, idleColor.g),
      map(pupilBrightness, 10, 80, 0, idleColor.b)
    );

    eyeLeds[LEFT_EYE_START] = pupilColor;
    eyeLeds[RIGHT_EYE_START] = pupilColor;

    FastLED.setBrightness(30);  // Still dim overall
    FastLED.show();  // Required in setup() before loop() starts
    delay(16);  // ~60fps
  }

  delay(200);  // Brief pause (arousal complete)

  // PHASE 3: Eyelid Flutter - Testing the light (1000ms @ 100fps for smooth flutter)
  // Quick partial brightness increases (eyelids trying to open)

  // First flutter (very brief, 150ms)
  for (int step = 0; step <= 15; step++) {
    float progress = step / 15.0;
    int brightness = 30 + (sin(progress * PI) * 30);  // Smooth sine wave flutter

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(10);  // 100fps
  }

  delay(200);  // Pause

  // Second flutter (slightly longer, 200ms)
  for (int step = 0; step <= 20; step++) {
    float progress = step / 20.0;
    int brightness = 30 + (sin(progress * PI) * 50);  // Bigger flutter

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(10);  // 100fps
  }

  delay(300);  // Longer pause (preparing for full opening)

  // PHASE 4: Gradual Eye Opening - Slow, smooth (2000ms @ 60fps = 120 frames)
  // Non-linear easing curve (matches biological eyelid opening)
  for (int step = 0; step <= 120; step++) {
    // Quadratic ease-out for natural eyelid movement
    float progress = step / 120.0;
    float eased = 1.0 - (1.0 - progress) * (1.0 - progress);

    int brightness = 30 + (eased * (currentBrightness - 30));

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(16);  // ~60fps for smooth opening
  }

  delay(300);  // Pause (eyes fully open, adjusting to light)

  // PHASE 4.5: Wake-up Yawn - Mouth opens and closes (1500ms)
  // Gradual opening (1000ms)
  for (int step = 0; step <= 60; step++) {
    float progress = step / 60.0;
    // Ease-out for natural yawn opening
    float eased = 1.0 - (1.0 - progress) * (1.0 - progress);

    // Mouth opens to about 70% during yawn
    int yawnAmplitude = eased * 180;  // 0 → 180 (70% open)

    // Render mouth
    float ledsPerSide = (yawnAmplitude / 255.0) * 4.0;
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);

    // Fill left and right sides symmetrically
    for (int i = 0; i < 4; i++) {
      if (ledsPerSide >= (i + 1)) {
        mouthLeds[3 - i] = mouthColor;
        mouthLeds[4 + i] = mouthColor;
      } else if (ledsPerSide > i) {
        float brightness = ledsPerSide - i;
        mouthLeds[3 - i] = mouthColor;
        mouthLeds[3 - i].nscale8(brightness * 255);
        mouthLeds[4 + i] = mouthColor;
        mouthLeds[4 + i].nscale8(brightness * 255);
      }
    }

    FastLED.show();  // Required in setup() before loop() starts
    delay(16);  // ~60fps
  }

  delay(200);  // Hold yawn

  // Gradual closing (500ms)
  for (int step = 0; step <= 30; step++) {
    float progress = step / 30.0;
    // Ease-in for natural yawn closing
    float eased = progress * progress;

    // Mouth closes
    int yawnAmplitude = 180 - (eased * 180);  // 180 → 0

    // Render mouth
    float ledsPerSide = (yawnAmplitude / 255.0) * 4.0;
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);

    // Fill left and right sides symmetrically
    for (int i = 0; i < 4; i++) {
      if (ledsPerSide >= (i + 1)) {
        mouthLeds[3 - i] = mouthColor;
        mouthLeds[4 + i] = mouthColor;
      } else if (ledsPerSide > i) {
        float brightness = ledsPerSide - i;
        mouthLeds[3 - i] = mouthColor;
        mouthLeds[3 - i].nscale8(brightness * 255);
        mouthLeds[4 + i] = mouthColor;
        mouthLeds[4 + i].nscale8(brightness * 255);
      }
    }

    FastLED.show();  // Required in setup() before loop() starts
    delay(16);  // ~60fps
  }

  delay(300);  // Pause after yawn

  // PHASE 5: Light Adjustment Blinks @ 100fps for ultra-smooth blinks

  // First blink (slow, full blink - lubricating dry eyes)
  // Close phase: 50ms
  for (int step = 0; step <= 5; step++) {
    float progress = step / 5.0;
    int brightness = currentBrightness - (progress * (currentBrightness - 20));

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(10);  // 100fps
  }

  delay(60);  // Eyes closed (lubricating)

  // Open phase: 100ms (slower, asymmetric)
  for (int step = 0; step <= 10; step++) {
    float progress = step / 10.0;
    // Ease-out for smooth opening
    float eased = 1.0 - (1.0 - progress) * (1.0 - progress);
    int brightness = 20 + (eased * (currentBrightness - 20));

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(10);  // 100fps
  }

  delay(400);  // Longer pause

  // Second blink (quicker - adjusting to brightness)
  // Close phase: 40ms
  for (int step = 0; step <= 4; step++) {
    float progress = step / 4.0;
    int brightness = currentBrightness - (progress * (currentBrightness - 40));

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(10);  // 100fps
  }

  delay(40);  // Brief closure

  // Open phase: 80ms
  for (int step = 0; step <= 8; step++) {
    float progress = step / 8.0;
    float eased = 1.0 - (1.0 - progress) * (1.0 - progress);
    int brightness = 40 + (eased * (currentBrightness - 40));

    fillEyes(idleColor, true);
    FastLED.setBrightness(brightness);
    FastLED.show();  // Required in setup() before loop() starts
    delay(10);  // 100fps
  }

  // Fully awake - settle into idle state
  FastLED.setBrightness(currentBrightness);
  fillEyes(idleColor, true);
  FastLED.show();  // Required in setup() before loop() starts

  delay(500);  // Final pause before breathing animation starts
}

// ==================== MOUTH FUNCTIONS ====================

void setMouthAmplitude(int amplitude) {
  // Clamp amplitude to 0-255
  int newAmplitude = constrain(amplitude, 0, 255);

  // Debug logging disabled - interferes with serial protocol

  mouthAmplitude = newAmplitude;
}

void startMouthTestTalking() {
  mouthTestTalking = true;
  mouthTestStartTime = millis();
}

void updateMouth() {
  // If test talking animation is active, use that instead of amplitude
  if (mouthTestTalking) {
    unsigned long elapsed = millis() - mouthTestStartTime;
    
    // Test talking: 10-second animation with varied "syllables"
    // Pattern: quiet, LOUD, quiet, medium, LOUD, medium, quiet, LOUD, quiet
    int syllable = (elapsed / 800) % 9;  // 800ms per syllable
    int syllableProgress = (elapsed % 800);  // 0-800ms within syllable
    
    int targetAmplitude = 0;
    if (syllable == 1 || syllable == 4 || syllable == 7) {
      // LOUD syllables
      targetAmplitude = 200;
    } else if (syllable == 3 || syllable == 5) {
      // Medium syllables
      targetAmplitude = 120;
    } else {
      // Quiet/gap
      targetAmplitude = 20;
    }
    
    // Quick attack, slower decay (like speech envelope)
    float envelopeProgress = syllableProgress / 800.0;  // 0.0-1.0
    float envelope;
    if (envelopeProgress < 0.2) {
      // Attack: 0-0.2 (160ms)
      envelope = envelopeProgress / 0.2;
    } else {
      // Decay: 0.2-1.0 (640ms)
      envelope = 1.0 - ((envelopeProgress - 0.2) / 0.8);
    }
    envelope = max(0.0, min(1.0, envelope));
    
    mouthAmplitude = targetAmplitude * envelope;
    
    // Stop after 10 seconds
    if (elapsed > 10000) {
      mouthTestTalking = false;
      mouthAmplitude = 0;
    }
  }
  
  // CENTER-OUT BLOOM ANIMATION
  // Physical V-shape layout:
  //   LED 0 (top left)     LED 7 (top right)
  //   LED 1                LED 6
  //   LED 2                LED 5
  //   LED 3                LED 4
  //         └──────────┘ (bottom middle)
  //
  // Animation blooms from center (1,6) outward to top (0,7) and bottom (3,4)
  // Uses brightness gradients with delayed cascades for smooth diffused effect

  // Special case: ENGAGED mode with no amplitude = completely black mouth
  if (currentPattern == 'E' && mouthAmplitude == 0) {
    for (int i = 0; i < NUM_MOUTH_LEDS; i++) {
      mouthLeds[i] = CRGB::Black;
    }
    return;
  }

  // Normalize amplitude to 0.0-1.0 range
  float openness = mouthAmplitude / 255.0;

  // Dynamic baseline based on mode
  // ENGAGED = completely dark when silent for dramatic effect
  // Others = small baseline glow for visibility
  int idleBrightness = 0;
  if (currentPattern != 'E' && mouthAmplitude == 0) {
    idleBrightness = 20;  // Small glow for other modes when silent
  }

  // CENTER START: Upper-middle LEDs (1, 6) - brightest, bloom from here
  int centerBright = idleBrightness + (openness * (255 - idleBrightness));
  mouthLeds[1] = mouthColor;
  mouthLeds[1].nscale8(centerBright);
  mouthLeds[6] = mouthColor;
  mouthLeds[6].nscale8(centerBright);

  // BLOOM UP: Corner LEDs (0, 7) - bloom upward from center
  // Reduce brightness to compensate for physical spread/angle
  float cornerOpen = max(0.0f, (openness - 0.3f) * 1.43f);
  int cornerBright = idleBrightness + (cornerOpen * (200 - idleBrightness));  // Max 200 to keep darker
  mouthLeds[0] = mouthColor;
  mouthLeds[0].nscale8(cornerBright);
  mouthLeds[7] = mouthColor;
  mouthLeds[7].nscale8(cornerBright);

  // BLOOM DOWN: Lower-middle LEDs (2, 5) - bloom downward from center
  float lowerMidOpen = max(0.0f, (openness - 0.25f) * 1.33f);
  int lowerMidBright = idleBrightness + (lowerMidOpen * (255 - idleBrightness));
  mouthLeds[2] = mouthColor;
  mouthLeds[2].nscale8(lowerMidBright);
  mouthLeds[5] = mouthColor;
  mouthLeds[5].nscale8(lowerMidBright);

  // BLOOM DOWN: Bottom LEDs (3, 4) - bloom last, stays darkest longest
  float bottomOpen = max(0.0f, (openness - 0.5f) * 2.0f);
  int bottomBright = idleBrightness + (bottomOpen * (255 - idleBrightness));
  mouthLeds[3] = mouthColor;
  mouthLeds[3].nscale8(bottomBright);
  mouthLeds[4] = mouthColor;
  mouthLeds[4].nscale8(bottomBright);

  // Don't call show() - will be called at end of loop()
}

// Calculate blink brightness for a single eye (independent blinking)
int calculateEyeBlinkBrightness(bool &isBlinking, unsigned long &startTime, unsigned long &nextBlinkTime,
                                 int &closeTime, int &openTime, int &depth, bool &doubleQueued,
                                 unsigned long currentTime, const char* eyeName) {
  // Initialize next blink time on first run
  if (nextBlinkTime == 0) {
    nextBlinkTime = currentTime + random(8000, 15000);  // 8-15 seconds until first blink
    // Debug logging removed - interferes with serial protocol
  }

  // Check if it's time to blink
  if (!isBlinking && currentTime >= nextBlinkTime) {
    isBlinking = true;
    startTime = currentTime;

    // Randomize blink characteristics for natural variation
    closeTime = random(40, 60);    // Fast close: 40-60ms
    openTime = random(90, 120);    // Slow open: 90-120ms

    // Blink depth variation
    if (random(100) < 15) {
      depth = random(80, 95);  // 15% chance of partial blink
    } else {
      depth = 100;  // 85% chance of full blink
    }

    // Double blink pattern (20% chance)
    if (random(100) < 20) {
      doubleQueued = true;
    }

    // Debug logging removed - interferes with serial protocol
  }

  // Calculate brightness if blinking
  if (isBlinking) {
    unsigned long blinkDuration = currentTime - startTime;
    unsigned long totalBlinkTime = closeTime + openTime;
    int minBrightness = currentBrightness * (100 - depth) / 100;

    if (blinkDuration < closeTime) {
      // CLOSE PHASE
      int brightness = map(blinkDuration, 0, closeTime, currentBrightness, minBrightness);
      return brightness;
    } else if (blinkDuration < totalBlinkTime) {
      // OPEN PHASE
      int brightness = map(blinkDuration, closeTime, totalBlinkTime, minBrightness, currentBrightness);
      return brightness;
    } else {
      // Blink complete
      // Debug logging removed - interferes with serial protocol
      isBlinking = false;

      if (doubleQueued) {
        nextBlinkTime = currentTime + random(200, 300);
        doubleQueued = false;
        // Debug logging removed - interferes with serial protocol
      } else {
        nextBlinkTime = currentTime + random(8000, 15000);  // 8-15 seconds between blinks
      }

      return currentBrightness;
    }
  }

  // Not blinking - return current brightness
  return currentBrightness;
}
