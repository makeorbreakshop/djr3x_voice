// fx_win_beat - windows while music plays: flash on the beat, decay e^(-6 phase);
// alternate windows accented (1.0 / 0.4) on alternate beats.
#include "effects.h"

void fx_win_beat(const Ctx &c) {
  float phase = frac(c.beat);
  uint8_t beatN = (uint32_t)floor(c.beat) % 2;
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) {
      float accent = ((k + b) % 2) == beatN ? 1.0 : 0.4;
      setWindow(b, k, scaled(windowBase(b, k), 0.35 + 0.65 * exp(-phase * 6) * accent));
    }
}
