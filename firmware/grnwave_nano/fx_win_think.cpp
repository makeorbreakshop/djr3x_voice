// fx_win_think - windows, thinking: a fast shimmer, level = 0.3 + 0.3 sin(8 t + i).
#include "effects.h"

void fx_win_think(const Ctx &c) {
  float t = c.ms / 1000.0f;
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++)
      setWindow(b, k, scaled(windowBase(b, k), 0.3 + 0.3 * sin(t * 8 + b * WINDOWS_PER_BOARD + k)));
}
