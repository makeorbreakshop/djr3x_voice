/*
 * Pupil & Ring Color Test Sketch
 * Cycles through different pupil/iris combinations for DJ R3X eyes
 *
 * PURPOSE: Visual test to see what works best with 7-LED rings
 * - Center LED = pupil
 * - Outer 6 LEDs = iris/ring
 *
 * Each variation displays for 5 seconds before moving to next
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

// LED array
CRGB leds[NUM_LEDS];

// Test variations
int currentTest = 0;
unsigned long lastTestChange = 0;
#define TEST_DURATION 5000  // 5 seconds per test

void setup() {
  Serial.begin(115200);

  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(128);
  FastLED.clear();
  FastLED.show();

  Serial.println("Pupil Test Starting!");
  Serial.println("Each variation shows for 5 seconds...");
}

void loop() {
  unsigned long currentTime = millis();

  // Change test every 5 seconds
  if (currentTime - lastTestChange >= TEST_DURATION) {
    currentTest++;
    if (currentTest > 11) {
      currentTest = 0;  // Loop back to first test
    }
    lastTestChange = currentTime;

    // Clear LEDs
    FastLED.clear();

    // Apply current test pattern
    switch (currentTest) {
      case 0:
        Serial.println("Test 0: Baseline - All warm gold (255,100,0), no pupil distinction");
        setSolidColor(CRGB(255, 100, 0));
        break;

      case 1:
        Serial.println("Test 1: White pupil (255,255,255) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(255, 255, 255));
        break;

      case 2:
        Serial.println("Test 2: Bright white pupil (200,200,200) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(200, 200, 200));
        break;

      case 3:
        Serial.println("Test 3: Soft white pupil (150,150,150) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(150, 150, 150));
        break;

      case 4:
        Serial.println("Test 4: Black/dim pupil (20,20,20) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(20, 20, 20));
        break;

      case 5:
        Serial.println("Test 5: Dark pupil (10,10,10) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(10, 10, 10));
        break;

      case 6:
        Serial.println("Test 6: Pure black pupil (0,0,0) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(0, 0, 0));
        break;

      case 7:
        Serial.println("Test 7: Brighter gold pupil (255,150,0) + Warm gold ring (255,100,0)");
        setRingColor(CRGB(255, 100, 0));
        setPupilColor(CRGB(255, 150, 0));
        break;

      case 8:
        Serial.println("Test 8: White pupil + Orange ring (255,80,0)");
        setRingColor(CRGB(255, 80, 0));
        setPupilColor(CRGB(255, 255, 255));
        break;

      case 9:
        Serial.println("Test 9: White pupil + Red-orange ring (255,50,0)");
        setRingColor(CRGB(255, 50, 0));
        setPupilColor(CRGB(255, 255, 255));
        break;

      case 10:
        Serial.println("Test 10: Black pupil + Bright yellow ring (255,200,0)");
        setRingColor(CRGB(255, 200, 0));
        setPupilColor(CRGB(0, 0, 0));
        break;

      case 11:
        Serial.println("Test 11: Warm white pupil (255,200,150) + Deep orange ring (200,60,0)");
        setRingColor(CRGB(200, 60, 0));
        setPupilColor(CRGB(255, 200, 150));
        break;
    }

    FastLED.show();
  }

  delay(100);
}

void setSolidColor(CRGB color) {
  fill_solid(leds, NUM_LEDS, color);
}

void setRingColor(CRGB color) {
  // Left eye outer ring (LEDs 1-6)
  for (int i = LEFT_EYE_START + 1; i <= LEFT_EYE_END; i++) {
    leds[i] = color;
  }

  // Right eye outer ring (LEDs 8-13)
  for (int i = RIGHT_EYE_START + 1; i <= RIGHT_EYE_END; i++) {
    leds[i] = color;
  }
}

void setPupilColor(CRGB color) {
  // Left eye center (LED 0)
  leds[LEFT_EYE_START] = color;

  // Right eye center (LED 7)
  leds[RIGHT_EYE_START] = color;
}
