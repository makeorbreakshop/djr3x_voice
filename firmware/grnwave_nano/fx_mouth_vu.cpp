// fx_mouth_vu - mouth, speaking: light spreads from the tip up both arms with the level
// (round(level x 4) LEDs per arm); the tip keeps a 20 % glow between words.
#include "effects.h"

void fx_mouth_vu(const Ctx &c) {
  uint8_t lit = (uint8_t)(c.level * 4 + 0.5f);
  for (uint8_t i = 0; i < MOUTH_LEDS; i++) {
    uint8_t r = mouthRank(i);
    mouthLeds[i] = r < lit ? MOUTH_SPEAK : r == 0 ? scaled(MOUTH_SPEAK, 0.2) : CRGB::Black;
  }
}
