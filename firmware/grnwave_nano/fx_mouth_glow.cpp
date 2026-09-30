// fx_mouth_glow - mouth, idle / engaged / listening: only the V's tip glows (blue; half
// orange when engaged).
#include "effects.h"

void fx_mouth_glow(const Ctx &) {
  CRGB c = mode == 'E' ? scaled(MOUTH_ORANGE, 0.5) : MOUTH_BLUE;
  for (uint8_t i = 0; i < MOUTH_LEDS; i++) mouthLeds[i] = mouthRank(i) == 0 ? c : CRGB::Black;
}
