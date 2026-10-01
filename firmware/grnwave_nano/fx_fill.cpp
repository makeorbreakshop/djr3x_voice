// fx_fill - body dots, listening: every bar fills bottom to top in blue.
// Params: SWEEPS_PER_S 1.6; the lit head at full, the rest at 60 %.
#include "effects.h"

static const float SWEEPS_PER_S = 1.6;

void fx_fill(const Ctx &c) {
  float row = floor(frac(c.ms / 1000.0f * SWEEPS_PER_S) * (SMALL_PER_BOARD + 2));
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++)
      mainLeds[smallIdx(b, k)] = k <= row ? scaled(LISTEN_BLUE, k == row ? 1.0 : 0.6) : CRGB::Black;
}
