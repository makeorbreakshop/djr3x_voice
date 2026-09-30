// fx_scan - body dots, thinking: one cyan dot runs up board A, B, C in turn, with a
// 25 % tail. Params: LEDS_PER_S 18.
#include "effects.h"

static const float LEDS_PER_S = 18;

void fx_scan(const Ctx &c) {
  const uint8_t total = BODY_BOARDS * SMALL_PER_BOARD;
  uint8_t pos = (uint32_t)(c.ms / 1000.0f * LEDS_PER_S) % total;
  uint8_t tail = (pos + total - 1) % total;
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++) {
      uint8_t i = b * SMALL_PER_BOARD + k;
      mainLeds[smallIdx(b, k)] = i == pos ? THINK_CYAN : i == tail ? scaled(THINK_CYAN, 0.25) : CRGB::Black;
    }
}
