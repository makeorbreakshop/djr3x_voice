// fx_eyes_speak - eyes, speaking: gold at 0.6 + 0.4 x the speech level.
#include "effects.h"

void fx_eyes_speak(const Ctx &c) {
  CRGB px = scaled(GOLD, 0.6 + 0.4 * c.level);
  setEyes(px, px);
}
