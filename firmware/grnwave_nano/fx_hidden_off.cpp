// fx_hidden_off - groups not behind a window (config.h EXPOSED) stay dark: saves power
// and light leaking through the shell.
#include "effects.h"

void fx_hidden_off(const Ctx &) {
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t g = 0; g < GROUPS_PER_BOARD; g++) {
      bool shown = false;
      for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++) shown |= EXPOSED[b][k] == g;
      if (shown) continue;
      uint8_t i = groupIdx(b, g);
      for (uint8_t j = 0; j < LEDS_PER_GROUP; j++) mainLeds[i + j] = CRGB::Black;
    }
}
