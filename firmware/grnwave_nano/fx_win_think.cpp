// fx_win_think - windows, thinking: a cyan "working" block steps through the 9 windows,
// board by board (6 steps a second), with a fading trail; the rest hold their colour dim.
// Params: 6 windows/s, trail 0.35, rest 0.12.
#include "effects.h"

static const float STEPS_PER_S = 6, TRAIL = 0.35, REST = 0.12;

void fx_win_think(const Ctx &c) {
  uint8_t pos = (uint32_t)(c.ms / 1000.0f * STEPS_PER_S) % WINDOWS;
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) {
      uint8_t i = b * WINDOWS_PER_BOARD + k;
      if (i == pos) setWindow(b, k, THINK_CYAN);
      else if (i == (pos + WINDOWS - 1) % WINDOWS) setWindow(b, k, scaled(THINK_CYAN, TRAIL));
      else setWindow(b, k, scaled(windowBase(b, k), REST));
    }
}
