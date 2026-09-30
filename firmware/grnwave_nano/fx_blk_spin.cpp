// fx_blk_spin - bare windows only (DIFFUSED 0), thinking: inside every window one of the
// 2 x 2 5050s is at the window's colour and the rest at 30 %, the bright one circling
// 8 steps a second. Under a diffuser this would average out, so it doesn't run there.
#include "effects.h"

static const uint8_t ROUND[LEDS_PER_GROUP] = {0, 1, 3, 2};  // 2 x 2, bottom row first
static const float DIM = 0.3, STEPS_PER_S = 8;

void fx_blk_spin(const Ctx &c) {
  uint8_t lit = ROUND[(uint32_t)(c.ms / 1000.0f * STEPS_PER_S) % LEDS_PER_GROUP];
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) {
      uint8_t i = windowIdx(b, k);
      for (uint8_t j = 0; j < LEDS_PER_GROUP; j++)
        if (j != lit) mainLeds[i + j] = scaled(win[b * WINDOWS_PER_BOARD + k], DIM);
    }
}
