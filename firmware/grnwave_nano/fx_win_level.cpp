// fx_win_level - windows, speaking: each panel's 3 windows are a VU meter of the speech
// level. Window k lights the speaking orange (the mouth's) once the level passes
// 0.2 / 0.45 / 0.7, brighter with the level; below that it holds its own colour dim.
#include "effects.h"

static const float THRESH[WINDOWS_PER_BOARD] = {0.2, 0.45, 0.7};
static const float REST = 0.15;

void fx_win_level(const Ctx &c) {
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++)
      setWindow(b, k, c.level >= THRESH[k] ? scaled(MOUTH_SPEAK, 0.5 + 0.5 * c.level) : scaled(windowBase(b, k), REST));
}
