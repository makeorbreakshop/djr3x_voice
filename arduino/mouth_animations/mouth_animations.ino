/*
 * DJ R3X Mouth Animation Test Suite - Bloom Variations
 *
 * PHYSICAL LAYOUT (V-shaped mouth with diffusion):
 *
 *   LED 0              LED 7
 *      \              /
 *       LED 1    LED 6
 *        \        /
 *         LED 2  LED 5
 *          \    /
 *         LED 3  LED 4
 *            \/
 *     (bottom of V)
 *
 * Left side descends:  0 → 1 → 2 → 3 (top-left to bottom-center)
 * Right side ascends:  4 → 5 → 6 → 7 (bottom-center to top-right)
 * Center gap is between LED 3 and LED 4
 *
 * SERIAL COMMANDS:
 * 1 - Center-Out Bloom (talking simulation)
 * 2 - Top-Down Bloom (talking simulation)
 * 3 - Bottom-Up Bloom (talking simulation)
 */

#include <FastLED.h>

#define MOUTH_PIN   5
#define NUM_MOUTH_LEDS  8
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB
#define FPS 60

CRGB mouthLeds[NUM_MOUTH_LEDS];

int currentMode = 1;  // Default to center-out

// Easing functions
float easeOutCubic(float t) {
  float f = t - 1.0;
  return f * f * f + 1.0;
}

float easeInOutCubic(float t) {
  if (t < 0.5) {
    return 4 * t * t * t;
  } else {
    float f = 2 * t - 2;
    return 0.5 * f * f * f + 1;
  }
}

// Simulate realistic talking with varying amplitude
// Returns amplitude value 0.0 to 1.0
float getTalkingAmplitude(unsigned long timeMs) {
  // Create pseudo-random but smooth amplitude variations like real speech
  // Mix multiple sine waves at different frequencies

  float t = timeMs / 1000.0;  // Convert to seconds

  // Low frequency: syllables/words (2-4 Hz)
  float syllable = (sin(t * 2.5 * TWO_PI) + 1.0) / 2.0;

  // Mid frequency: phonemes (5-8 Hz)
  float phoneme = (sin(t * 6.3 * TWO_PI) + 1.0) / 2.0;

  // High frequency: micro-variations (10-15 Hz)
  float micro = (sin(t * 12.7 * TWO_PI) + 1.0) / 2.0;

  // Combine with weights (syllable dominates)
  float amplitude = (syllable * 0.6) + (phoneme * 0.3) + (micro * 0.1);

  // Add occasional pauses (every ~2 seconds)
  float pauseCycle = fmod(t, 2.3);
  if (pauseCycle < 0.15) {
    amplitude *= (pauseCycle / 0.15);  // Fade to silence
  } else if (pauseCycle > 2.0) {
    amplitude *= (2.3 - pauseCycle) / 0.3;  // Fade to silence
  }

  return amplitude;
}

// BLOOM 1: CENTER-OUT
// Opens from middle (1,6) outward to corners (0,7) and bottom (3,4)
void centerOutBloom(float amplitude) {
  // Use amplitude directly for full 0.0 to 1.0 range (completely off to full bright)
  float openness = amplitude;  // 0.0 to 1.0 range

  // CENTER START: Upper-middle LEDs (1, 6) - brightest, bloom from here
  int centerBright = 0 + (openness * 255);  // Range: 0-255
  mouthLeds[1] = CHSV(160, 255, centerBright);
  mouthLeds[6] = CHSV(160, 255, centerBright);

  // BLOOM UP: Corner LEDs (0, 7) - bloom upward from center
  // Reduce brightness to compensate for physical spread/angle
  float cornerOpen = max(0.0f, (openness - 0.3f) * 1.43f);
  int cornerBright = 0 + (cornerOpen * 200);  // Max 200 instead of 255
  mouthLeds[0] = CHSV(160, 255, cornerBright);
  mouthLeds[7] = CHSV(160, 255, cornerBright);

  // BLOOM DOWN: Lower-middle LEDs (2, 5) - bloom downward from center
  float lowerMidOpen = max(0.0f, (openness - 0.25f) * 1.33f);
  int lowerMidBright = 0 + (lowerMidOpen * 255);
  mouthLeds[2] = CHSV(160, 255, lowerMidBright);
  mouthLeds[5] = CHSV(160, 255, lowerMidBright);

  // BLOOM DOWN: Bottom LEDs (3, 4) - bloom last, stays darkest longest
  float bottomOpen = max(0.0f, (openness - 0.5f) * 2.0f);
  int bottomBright = 0 + (bottomOpen * 255);
  mouthLeds[3] = CHSV(160, 255, bottomBright);
  mouthLeds[4] = CHSV(160, 255, bottomBright);

  FastLED.show();
}

// BLOOM 2: TOP-DOWN
// Opens from top corners (0,7) down to center (3,4)
void topDownBloom(float amplitude) {
  float openness = amplitude;  // 0.0 to 1.0 range

  // Top corners (0, 7) - start bright
  int cornerBright = 0 + (openness * 255);
  mouthLeds[0] = CHSV(100, 255, cornerBright);  // Yellow-green
  mouthLeds[7] = CHSV(100, 255, cornerBright);

  // Upper-middle (1, 6)
  float upperMidOpen = max(0.0f, (openness - 0.2f) * 1.25f);
  int upperMidBright = 0 + (upperMidOpen * 255);
  mouthLeds[1] = CHSV(100, 255, upperMidBright);
  mouthLeds[6] = CHSV(100, 255, upperMidBright);

  // Lower-middle (2, 5)
  float lowerMidOpen = max(0.0f, (openness - 0.4f) * 1.67f);
  int lowerMidBright = 0 + (lowerMidOpen * 255);
  mouthLeds[2] = CHSV(100, 255, lowerMidBright);
  mouthLeds[5] = CHSV(100, 255, lowerMidBright);

  // Bottom center (3, 4) - bloom last, stays darkest
  float centerOpen = max(0.0f, (openness - 0.6f) * 2.5f);
  int centerBright = 0 + (centerOpen * 255);
  mouthLeds[3] = CHSV(100, 255, centerBright);
  mouthLeds[4] = CHSV(100, 255, centerBright);

  FastLED.show();
}

// BLOOM 3: BOTTOM-UP
// Opens from center bottom (3,4) up to top corners (0,7)
void bottomUpBloom(float amplitude) {
  float openness = amplitude;  // 0.0 to 1.0 range

  // Bottom center (3, 4) - start bright
  int centerBright = 0 + (openness * 255);
  mouthLeds[3] = CHSV(200, 255, centerBright);  // Purple
  mouthLeds[4] = CHSV(200, 255, centerBright);

  // Lower-middle (2, 5)
  float lowerMidOpen = max(0.0f, (openness - 0.2f) * 1.25f);
  int lowerMidBright = 0 + (lowerMidOpen * 255);
  mouthLeds[2] = CHSV(200, 255, lowerMidBright);
  mouthLeds[5] = CHSV(200, 255, lowerMidBright);

  // Upper-middle (1, 6)
  float upperMidOpen = max(0.0f, (openness - 0.4f) * 1.67f);
  int upperMidBright = 0 + (upperMidOpen * 255);
  mouthLeds[1] = CHSV(200, 255, upperMidBright);
  mouthLeds[6] = CHSV(200, 255, upperMidBright);

  // Top corners (0, 7) - bloom last, stays darkest
  float cornerOpen = max(0.0f, (openness - 0.6f) * 2.5f);
  int cornerBright = 0 + (cornerOpen * 255);
  mouthLeds[0] = CHSV(200, 255, cornerBright);
  mouthLeds[7] = CHSV(200, 255, cornerBright);

  FastLED.show();
}

void setup() {
  Serial.begin(115200);
  FastLED.addLeds<LED_TYPE, MOUTH_PIN, COLOR_ORDER>(mouthLeds, NUM_MOUTH_LEDS);
  FastLED.setBrightness(255);
  FastLED.setMaxRefreshRate(FPS);

  Serial.println("=== DJ R3X Mouth Bloom Animation Test ===");
  Serial.println("");
  Serial.println("SERIAL COMMANDS:");
  Serial.println("  1 - Center-Out Bloom (cyan)");
  Serial.println("  2 - Top-Down Bloom (yellow-green)");
  Serial.println("  3 - Bottom-Up Bloom (purple)");
  Serial.println("");
  Serial.println("All modes simulate realistic talking with amplitude variations");
  Serial.println("Each mode loops continuously - send command to switch");
  Serial.println("");
  Serial.print("Current mode: ");
  Serial.println(currentMode);
}

void loop() {
  // Check for serial input
  if (Serial.available() > 0) {
    char cmd = Serial.read();
    if (cmd >= '1' && cmd <= '3') {
      currentMode = cmd - '0';
      Serial.print("Switched to mode: ");
      Serial.println(currentMode);
    }
  }

  // Get current talking amplitude
  unsigned long now = millis();
  float amplitude = getTalkingAmplitude(now);

  // Run current bloom mode
  switch (currentMode) {
    case 1:
      centerOutBloom(amplitude);
      break;
    case 2:
      topDownBloom(amplitude);
      break;
    case 3:
      bottomUpBloom(amplitude);
      break;
  }

  FastLED.delay(1000 / FPS);
}
