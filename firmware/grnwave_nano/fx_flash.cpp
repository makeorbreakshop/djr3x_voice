// fx_flash - SF: eyes green and a green sparkle over the body dots (55 % each frame).
#include "effects.h"

void fx_flash(const Ctx &) {
  setEyes(FLASH_GREEN, FLASH_GREEN);
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++)
      if (rnd() < 0.55) mainLeds[smallIdx(b, k)] = VU_GREEN;
}
