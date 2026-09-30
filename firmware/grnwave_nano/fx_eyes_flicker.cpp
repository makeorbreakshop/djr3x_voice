// fx_eyes_flicker - eyes, idle: gold, each eye drifting between 0.4 and 1 on its own
// (grnwave's candle look). Params: a new target every 200-1600 ms, eased 8 % per frame.
#include "effects.h"

static float level[EYE_LEDS] = {0.8, 0.8}, target[EYE_LEDS] = {0.8, 0.8};
static uint32_t next[EYE_LEDS];

void fx_eyes_flicker(const Ctx &c) {
  CRGB px[EYE_LEDS];
  for (uint8_t e = 0; e < EYE_LEDS; e++) {
    if (c.ms >= next[e]) {
      target[e] = 0.4 + 0.6 * rnd();
      next[e] = c.ms + 200 + rnd() * 1400;
    }
    level[e] += (target[e] - level[e]) * 0.08;
    px[e] = scaled(GOLD, level[e]);
  }
  setEyes(px[0], px[1]);
}
