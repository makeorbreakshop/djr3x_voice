// fx_eyes_pulse - eyes, listening: blue pulsing 0.5-1. Params: HZ 1.5.
#include "effects.h"

static const float HZ = 1.5;

void fx_eyes_pulse(const Ctx &c) {
  CRGB px = scaled(LISTEN_BLUE, 0.75 + 0.25 * sin(c.ms / 1000.0f * 2 * PI * HZ));
  setEyes(px, px);
}
