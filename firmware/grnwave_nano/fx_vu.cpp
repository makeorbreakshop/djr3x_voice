// fx_vu - body dots, speaking: each bar is a VU meter of the speech level.
// Rows 0-3 green, 4-5 amber, 6-7 red; lit rows = round(level x 8).
#include "effects.h"

void fx_vu(const Ctx &c) {
  uint8_t lit = (uint8_t)(c.level * SMALL_PER_BOARD + 0.5f);
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++)
      mainLeds[smallIdx(b, k)] = k < lit ? (k < 4 ? VU_GREEN : k < 6 ? VU_AMBER : ALERT_RED) : CRGB::Black;
}
