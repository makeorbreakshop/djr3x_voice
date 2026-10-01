// fx_win_beat - windows while music plays: on every beat each window changes colour (never
// to the one it had) and flashes, decaying e^(-6 phase); alternate windows accented
// (1.0 / 0.4) on alternate beats. Colour of window i on beat n: BLOCK_COLORS[(i + n (1 + i % 3)) % 4].
#include "effects.h"

void fx_win_beat(const Ctx &c) {
  float phase = frac(c.beat);
  uint32_t n = (uint32_t)floor(c.beat);
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) {
      uint8_t i = b * WINDOWS_PER_BOARD + k;
      float accent = ((k + b) % 2) == n % 2 ? 1.0 : 0.4;
      CRGB col = BLOCK_COLORS[(i + n * (1 + i % 3)) % 4];
      setWindow(b, k, scaled(col, 0.35 + 0.65 * exp(-phase * 6) * accent));
    }
}
