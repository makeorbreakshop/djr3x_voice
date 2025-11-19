/*
 * WS2812B LED Strip Test
 * Simple test sketch for individually addressable LED strips (WS2812B/NeoPixel)
 *
 * This will cycle through:
 * 1. Red chase
 * 2. Green chase
 * 3. Blue chase
 * 4. Rainbow cycle
 * 5. White fade
 *
 * Connect strip to pin 5 (eyes use pin 6)
 */

#include <FastLED.h>

// LED Configuration - ADJUST THESE FOR YOUR STRIP
#define LED_PIN     5         // Using pin 5 (eyes use pin 6)
#define NUM_LEDS    30        // Change this to match your strip length
#define LED_TYPE    WS2812B   // Most common type
#define COLOR_ORDER GRB       // Usually GRB, but might be RGB

// LED array
CRGB leds[NUM_LEDS];

// Animation state
unsigned long lastUpdate = 0;
int animationStep = 0;
int currentPattern = 0;

void setup() {
  // Start serial for debugging
  Serial.begin(115200);
  Serial.println("WS2812B Strip Test Starting...");
  Serial.print("LED Pin: ");
  Serial.println(LED_PIN);
  Serial.print("Number of LEDs: ");
  Serial.println(NUM_LEDS);

  // Initialize FastLED
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(128);  // 50% brightness for testing

  // Clear all LEDs
  FastLED.clear();
  FastLED.show();

  Serial.println("Initialization complete!");
  Serial.println("Patterns will cycle every 5 seconds:");
  Serial.println("0 = Red chase");
  Serial.println("1 = Green chase");
  Serial.println("2 = Blue chase");
  Serial.println("3 = Rainbow cycle");
  Serial.println("4 = White fade");
}

void loop() {
  unsigned long currentTime = millis();

  // Switch pattern every 5 seconds
  if (currentTime - lastUpdate > 5000) {
    currentPattern = (currentPattern + 1) % 5;
    animationStep = 0;
    Serial.print("Switching to pattern ");
    Serial.println(currentPattern);
    lastUpdate = currentTime;
  }

  // Run current pattern
  switch (currentPattern) {
    case 0:
      redChase();
      break;
    case 1:
      greenChase();
      break;
    case 2:
      blueChase();
      break;
    case 3:
      rainbowCycle();
      break;
    case 4:
      whiteFade();
      break;
  }

  FastLED.show();
  delay(50);  // 20 fps
  animationStep++;
}

void redChase() {
  // Moving red dot
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  int pos = animationStep % NUM_LEDS;
  leds[pos] = CRGB::Red;

  // Add a fading tail
  if (pos > 0) leds[pos - 1] = CRGB(100, 0, 0);
  if (pos > 1) leds[pos - 2] = CRGB(50, 0, 0);
}

void greenChase() {
  // Moving green dot
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  int pos = animationStep % NUM_LEDS;
  leds[pos] = CRGB::Green;

  // Add a fading tail
  if (pos > 0) leds[pos - 1] = CRGB(0, 100, 0);
  if (pos > 1) leds[pos - 2] = CRGB(0, 50, 0);
}

void blueChase() {
  // Moving blue dot
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  int pos = animationStep % NUM_LEDS;
  leds[pos] = CRGB::Blue;

  // Add a fading tail
  if (pos > 0) leds[pos - 1] = CRGB(0, 0, 100);
  if (pos > 1) leds[pos - 2] = CRGB(0, 0, 50);
}

void rainbowCycle() {
  // Rainbow wave across entire strip
  for (int i = 0; i < NUM_LEDS; i++) {
    int hue = (animationStep * 2 + i * (255 / NUM_LEDS)) % 256;
    leds[i] = CHSV(hue, 255, 255);
  }
}

void whiteFade() {
  // Breathing white
  float brightness = (sin(animationStep * 0.1) + 1.0) / 2.0;  // 0.0 to 1.0
  int level = brightness * 255;
  fill_solid(leds, NUM_LEDS, CRGB(level, level, level));
}
