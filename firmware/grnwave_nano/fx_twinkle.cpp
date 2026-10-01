// fx_twinkle - body dots, idle / engaged: each small LED blinks on and off at random in
// grnwave's dim red / white / blue.
// Params: chance on 45 % (engaged 60 %); hold 150 ms + up to 1100 ms (engaged 500 ms).
#include "effects.h"

static const float ON_IDLE = 0.45, ON_ENGAGED = 0.6;
static const uint16_t HOLD_MIN = 150, HOLD_IDLE = 1100, HOLD_ENGAGED = 500;

struct Twinkle { bool on; uint8_t color; uint32_t next; };
static Twinkle tw[BODY_BOARDS][SMALL_PER_BOARD];

void fx_twinkle(const Ctx &c) {
  bool engaged = mode == 'E';
  for (uint8_t b = 0; b < BODY_BOARDS; b++) {
    for (uint8_t k = 0; k < SMALL_PER_BOARD; k++) {
      Twinkle &t = tw[b][k];
      if (c.ms >= t.next) {
        t.on = rnd() < (engaged ? ON_ENGAGED : ON_IDLE);
        t.color = random(3);
        t.next = c.ms + HOLD_MIN + rnd() * (engaged ? HOLD_ENGAGED : HOLD_IDLE);
      }
      mainLeds[smallIdx(b, k)] = t.on ? SMALL_COLORS[t.color] : CRGB::Black;
    }
  }
}
