// fx_health - overlay: a window whose subsystem is down (Hxxx bit 0) blinks red at 2 Hz,
// whatever the look. Bits are board-major: A0 A1 A2 B0 ... (the chest's 9 status windows).
#include "effects.h"

void fx_health(const Ctx &c) {
  bool on = (c.ms / 250) % 2;
  for (uint8_t b = 0; b < BODY_BOARDS; b++)
    for (uint8_t k = 0; k < WINDOWS_PER_BOARD; k++)
      if (!(health & (1 << (b * WINDOWS_PER_BOARD + k)))) setWindow(b, k, on ? scaled(ALERT_RED, 0.9) : CRGB::Black);
}
