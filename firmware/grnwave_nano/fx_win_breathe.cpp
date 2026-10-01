// fx_win_breathe - windows, idle / engaged / listening: each breathes slowly in its own
// colour. level = 0.45 + 0.25 sin(t (0.6 + 0.17 k) + 1.7 i).
#include "effects.h"

void fx_win_breathe(const Ctx &c) {
  float t = c.ms / 1000.0f;
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) {
      uint8_t i = b * WINDOWS_PER_BOARD + k;
      setWindow(b, k, scaled(windowBase(b, k), 0.45 + 0.25 * sin(t * (0.6 + 0.17 * k) + i * 1.7)));
    }
}
