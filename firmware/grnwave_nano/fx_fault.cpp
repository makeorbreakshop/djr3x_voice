// fx_fault - X3: every window pulses red (0.25-1, 1 Hz), a red dot runs up every bar
// (6 rows/s), eyes pulse red, mouth dark.
#include "effects.h"

void fx_fault(const Ctx &c) {
  float t = c.ms / 1000.0f;
  float pulse = 0.25 + 0.75 * (0.5 + 0.5 * sin(t * PI * 2));
  uint8_t pos = (uint32_t)(t * 6) % SMALL_PER_BOARD;
  for (uint8_t b = 0; b < BODY_BOARDS; b++) {
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++)
      mainLeds[smallIdx(b, k)] = k == pos ? scaled(ALERT_RED, 0.6) : CRGB::Black;
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) setWindow(b, k, scaled(ALERT_RED, pulse));
  }
  fx_hidden_off(c);
  CRGB e = scaled(ALERT_RED, pulse);
  setEyes(e, e);
  fill_solid(mouthLeds, MOUTH_LEDS, CRGB::Black);
}
