// fx_boot - X1 (until X0, or BOOT_TIMEOUT_MS): boards fill blue one after another, then
// their windows light; one sweep every 2.4 s. Eyes dim blue, mouth dark.
#include "effects.h"

void fx_boot(const Ctx &c) {
  float cycle = frac((c.ms - bootStart) / 2400.0f) * BODY_BOARDS * 1.25;
  for (uint8_t b = 0; b < BODY_BOARDS; b++) {
    float f = constrain(cycle - b, 0, 1.25);
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++)
      mainLeds[smallIdx(b, k)] = f * SMALL_PER_BOARD > k ? LISTEN_BLUE : CRGB::Black;
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++)
      setWindow(b, k, f >= 1 ? scaled(windowBase(b, k), 0.6) : CRGB::Black);
  }
  presentWindows(c, NULL);
  fx_hidden_off(c);
  CRGB e = scaled(LISTEN_BLUE, 0.3);
  setEyes(e, e);
  fill_solid(mouthLeds, MOUTH_LEDS, CRGB::Black);
}
