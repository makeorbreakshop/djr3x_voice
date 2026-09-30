// fx_eyes_alternate - eyes, thinking: cyan hops between the eyes every 250 ms, the other
// eye at 15 %.
#include "effects.h"

void fx_eyes_alternate(const Ctx &c) {
  CRGB dim = scaled(THINK_CYAN, 0.15);
  if ((c.ms / 250) % 2 == 0) setEyes(THINK_CYAN, dim); else setEyes(dim, THINK_CYAN);
}
