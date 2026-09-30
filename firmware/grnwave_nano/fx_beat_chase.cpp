// fx_beat_chase - body dots while music plays: a two-LED bar climbs each board once per
// beat, boards a third of a beat apart, over a 30 % red floor.
#include "effects.h"

void fx_beat_chase(const Ctx &c) {
  for (uint8_t b = 0; b < BODY_BOARDS; b++) {
    float row = floor(frac(c.beat + b / 3.0f) * SMALL_PER_BOARD);
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++)
      mainLeds[smallIdx(b, k)] = (k == row || k == row - 1) ? BLOCK_COLORS[(k + b) % 4] : scaled(SMALL_COLORS[0], 0.3);
  }
}
