// fx_sleep - X2: windows barely breathe (3-8 %), one green dot every third second, eyes a
// faint gold, mouth dark.
#include "effects.h"

void fx_sleep(const Ctx &c) {
  float t = c.ms / 1000.0f;
  float level = 0.03 + 0.05 * (0.5 + 0.5 * sin(t * 0.8));
  for (uint8_t b = 0; b < BODY_BOARDS; b++) {
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++) mainLeds[smallIdx(b, k)] = CRGB::Black;
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) setWindow(b, k, scaled(windowBase(b, k), level));
  }
  if ((c.ms / 1000) % 3 == 0) mainLeds[smallIdx(0, 0)] = scaled(VU_GREEN, 0.4);
  presentWindows(c, NULL);
  fx_hidden_off(c);
  CRGB e = scaled(GOLD, 0.08);
  setEyes(e, e);
  fill_solid(mouthLeds, MOUTH_LEDS, CRGB::Black);
}
