// fx_win_blink - windows, idle / engaged: the real droid's blinking blocks. Each window holds
// one of the block colours (or goes dark) for a while, then jumps to another - never the
// colour it just had.
// Params: on 75 %, level 0.8; hold 300 + up to 1400 ms (engaged: 150 + up to 600 ms).
#include "effects.h"

static const float ON = 0.75, LEVEL = 0.8;
static const uint16_t HOLD_MIN_IDLE = 300, HOLD_IDLE = 1400, HOLD_MIN_ENGAGED = 150, HOLD_ENGAGED = 600;

struct Blink { uint8_t color; bool on; uint32_t next; };
static Blink bl[WINDOWS];

void fx_win_blink(const Ctx &c) {
  bool engaged = mode == 'E';
  for (uint8_t i = 0; i < WINDOWS; i++) {
    Blink &s = bl[i];
    if (c.ms >= s.next) {
      s.on = rnd() < ON;
      s.color = (s.color + 1 + (uint8_t)(rnd() * 3)) % 4;
      s.next = c.ms + (engaged ? HOLD_MIN_ENGAGED + rnd() * HOLD_ENGAGED : HOLD_MIN_IDLE + rnd() * HOLD_IDLE);
    }
    win[i] = s.on ? scaled(BLOCK_COLORS[s.color], LEVEL) : CRGB::Black;
  }
}
