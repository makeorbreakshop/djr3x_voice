// fx_mouth_pulse - mouth, thinking: the whole V pulses dim blue (0.15-0.4) at 1 Hz.
#include "effects.h"

void fx_mouth_pulse(const Ctx &c) {
  CRGB px = scaled(MOUTH_BLUE, 0.15 + 0.25 * (0.5 + 0.5 * sin(c.ms / 1000.0f * 2 * PI)));
  for (uint8_t i = 0; i < MOUTH_LEDS; i++) mouthLeds[i] = px;
}
