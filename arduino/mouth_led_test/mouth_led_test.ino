/*
 * Mouth LED Mapping Test
 * Lights each LED one at a time to determine physical positions
 *
 * PHYSICAL LAYOUT (V-shaped mouth):
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
 */

#include <FastLED.h>

#define MOUTH_PIN   5
#define NUM_MOUTH_LEDS  8
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB

CRGB mouthLeds[NUM_MOUTH_LEDS];

void setup() {
  Serial.begin(115200);
  FastLED.addLeds<LED_TYPE, MOUTH_PIN, COLOR_ORDER>(mouthLeds, NUM_MOUTH_LEDS);
  FastLED.setBrightness(128);

  Serial.println("Mouth LED Mapping Test");
  Serial.println("Each LED will light up for 2 seconds");
  Serial.println("Note which physical position lights up for each number");
  Serial.println("");
}

void loop() {
  for (int i = 0; i < NUM_MOUTH_LEDS; i++) {
    // Clear all
    fill_solid(mouthLeds, NUM_MOUTH_LEDS, CRGB::Black);

    // Light current LED
    mouthLeds[i] = CRGB(0, 100, 255);  // Blue
    FastLED.show();

    Serial.print("LED ");
    Serial.print(i);
    Serial.println(" is lit (blue)");

    delay(2000);
  }

  Serial.println("");
  Serial.println("=== Cycle complete, starting over ===");
  Serial.println("");
  delay(1000);
}
