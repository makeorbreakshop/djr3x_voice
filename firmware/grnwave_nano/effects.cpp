// grnwave_nano - palette and the LOOKS registry (which effect runs in which state).
#include "effects.h"

// Small indicator LEDs: grnwave's dim red / white / blue.
const CRGB SMALL_COLORS[3] = {CRGB(80, 0, 0), CRGB(96, 136, 136), CRGB(0, 0, 85)};
// 5050 window blocks: red, white, gold, blue.
const CRGB BLOCK_COLORS[4] = {CRGB(255, 0, 0), CRGB(255, 255, 255), CRGB(255, 221, 136), CRGB(0, 0, 255)};
const CRGB GOLD(255, 221, 136);
const CRGB ENGAGED_BLUE(100, 180, 255);
const CRGB LISTEN_BLUE(0, 120, 255);
const CRGB THINK_CYAN(0, 255, 255);
const CRGB FLASH_GREEN(0, 255, 0);
const CRGB VU_GREEN(40, 255, 30);
const CRGB VU_AMBER(255, 110, 0);
const CRGB ALERT_RED(255, 20, 0);
const CRGB MOUTH_BLUE(0, 50, 150);
const CRGB MOUTH_ORANGE(255, 60, 0);
const CRGB MOUTH_SPEAK(255, 120, 20);

// The registry. Swap any function for another with the same signature.
const Look LOOKS[] = {
  // mode  body dots    windows         eyes               mouth
  {'I', fx_twinkle, fx_win_breathe, fx_eyes_flicker,   fx_mouth_glow},   // idle
  {'E', fx_twinkle, fx_win_breathe, fx_eyes_solid,     fx_mouth_glow},   // engaged
  {'L', fx_fill,    fx_win_breathe, fx_eyes_pulse,     fx_mouth_glow},   // listening
  {'T', fx_scan,    fx_win_think,   fx_eyes_alternate, fx_mouth_pulse},  // thinking
  {'S', fx_vu,      fx_win_level,   fx_eyes_speak,     fx_mouth_vu},     // speaking
};
const uint8_t NUM_LOOKS = sizeof(LOOKS) / sizeof(LOOKS[0]);
