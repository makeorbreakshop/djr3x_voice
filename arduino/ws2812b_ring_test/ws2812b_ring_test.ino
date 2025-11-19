/*
 * WS2812B 7-LED Ring Test Sketch
 * For Arduino Mega 2560
 *
 * This sketch tests a 7-LED WS2812B ring with multiple patterns
 * to verify proper wiring and functionality.
 *
 * Wiring:
 * - WS2812B Data Pin -> Arduino Pin 6
 * - WS2812B VCC -> 5V
 * - WS2812B GND -> GND
 *
 * Required Library: FastLED
 * Install via: Arduino IDE -> Tools -> Manage Libraries -> Search "FastLED"
 */

#include <FastLED.h>

// Configuration
#define LED_PIN     6        // Data pin for WS2812B
#define NUM_LEDS    14       // Number of LEDs (2 rings × 7 LEDs each)
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB
#define BRIGHTNESS  128      // 0-255, 50% brightness - safe for USB power

CRGB leds[NUM_LEDS];

void setup() {
  Serial.begin(115200);
  Serial.println("WS2812B 7-LED Ring Test");
  Serial.println("=======================");

  // Initialize FastLED
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(BRIGHTNESS);

  // Clear all LEDs
  FastLED.clear();
  FastLED.show();

  Serial.println("Setup complete. Starting test patterns...");
}

void loop() {
  Serial.println("\n--- Test 1: Individual LED Test (Red) ---");
  testIndividualLEDs(CRGB::Red, 500);
  delay(1000);

  Serial.println("\n--- Test 2: Color Cycle (All LEDs) ---");
  testColorCycle(1000);
  delay(1000);

  Serial.println("\n--- Test 3: Rainbow Pattern ---");
  testRainbow(3000);
  delay(1000);

  Serial.println("\n--- Test 4: Breathing Effect (Blue) ---");
  testBreathing(CRGB::Blue, 3000);
  delay(1000);

  Serial.println("\n--- Test 5: Rotating Dot (Green) ---");
  testRotatingDot(CRGB::Green, 3000);
  delay(1000);

  Serial.println("\n--- Test 6: Theater Chase (Purple) ---");
  testTheaterChase(CRGB::Purple, 3000);
  delay(2000);
}

// Test 1: Light up each LED individually
void testIndividualLEDs(CRGB color, int delayMs) {
  for (int i = 0; i < NUM_LEDS; i++) {
    FastLED.clear();
    leds[i] = color;
    FastLED.show();
    Serial.print("LED ");
    Serial.print(i);
    Serial.println(" ON");
    delay(delayMs);
  }
  FastLED.clear();
  FastLED.show();
}

// Test 2: Cycle through primary colors on all LEDs
void testColorCycle(int delayMs) {
  CRGB colors[] = {CRGB::Red, CRGB::Green, CRGB::Blue, CRGB::Yellow, CRGB::Cyan, CRGB::Magenta, CRGB::White};
  int numColors = sizeof(colors) / sizeof(colors[0]);

  for (int c = 0; c < numColors; c++) {
    fill_solid(leds, NUM_LEDS, colors[c]);
    FastLED.show();
    Serial.print("Color: ");
    Serial.println(c);
    delay(delayMs);
  }
  FastLED.clear();
  FastLED.show();
}

// Test 3: Rainbow effect
void testRainbow(int durationMs) {
  unsigned long startTime = millis();
  int hue = 0;

  while (millis() - startTime < durationMs) {
    for (int i = 0; i < NUM_LEDS; i++) {
      leds[i] = CHSV(hue + (i * 255 / NUM_LEDS), 255, 255);
    }
    FastLED.show();
    hue = (hue + 1) % 256;
    delay(10);
  }
  FastLED.clear();
  FastLED.show();
}

// Test 4: Breathing effect (fade in/out)
void testBreathing(CRGB color, int durationMs) {
  unsigned long startTime = millis();

  while (millis() - startTime < durationMs) {
    // Fade in
    for (int brightness = 0; brightness <= 255; brightness += 5) {
      fill_solid(leds, NUM_LEDS, color);
      FastLED.setBrightness(brightness);
      FastLED.show();
      delay(10);
      if (millis() - startTime >= durationMs) break;
    }

    // Fade out
    for (int brightness = 255; brightness >= 0; brightness -= 5) {
      fill_solid(leds, NUM_LEDS, color);
      FastLED.setBrightness(brightness);
      FastLED.show();
      delay(10);
      if (millis() - startTime >= durationMs) break;
    }
  }

  FastLED.setBrightness(BRIGHTNESS); // Restore default brightness
  FastLED.clear();
  FastLED.show();
}

// Test 5: Single dot rotating around the ring
void testRotatingDot(CRGB color, int durationMs) {
  unsigned long startTime = millis();
  int position = 0;

  while (millis() - startTime < durationMs) {
    FastLED.clear();
    leds[position] = color;
    FastLED.show();
    position = (position + 1) % NUM_LEDS;
    delay(100);
  }
  FastLED.clear();
  FastLED.show();
}

// Test 6: Theater chase effect
void testTheaterChase(CRGB color, int durationMs) {
  unsigned long startTime = millis();
  int offset = 0;

  while (millis() - startTime < durationMs) {
    FastLED.clear();
    for (int i = 0; i < NUM_LEDS; i++) {
      if ((i + offset) % 3 == 0) {
        leds[i] = color;
      }
    }
    FastLED.show();
    offset = (offset + 1) % 3;
    delay(200);
  }
  FastLED.clear();
  FastLED.show();
}
