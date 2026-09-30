// fx_win_level - windows, speaking: level = 0.35 + 0.65 x the speech level.
#include "effects.h"

void fx_win_level(const Ctx &c) {
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++)
      setWindow(b, k, scaled(windowBase(b, k), 0.35 + 0.65 * c.level));
}
