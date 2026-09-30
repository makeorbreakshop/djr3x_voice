// grnwave_nano - shared state and the helpers every effect uses.
#pragma once
#include <FastLED.h>
#include "config.h"

// What every effect reads.
struct Ctx {
  uint32_t ms;     // millis()
  float level;     // sqrt(amplitude / 255), 0..1: perceptual speech level
  uint16_t bpm;    // 0 = no music
  float beat;      // beats since boot (0 without music)
};

// ---- the LEDs
extern CRGB mainLeds[MAIN_LEDS];   // body 0-95, eyes 96-97
extern CRGB mouthLeds[MOUTH_LEDS];
// One colour per window, board-major (A0 A1 A2 B0 ... = the health bits). Window effects
// write here; presentWindows() (effects.cpp) puts it on the window's LEDs.
extern CRGB win[WINDOWS];

// ---- what the host told us (named_v1 words)
extern char mode;            // I E L T S
extern uint8_t amplitude;    // Mnnn
extern uint16_t bpm;         // Bnnn
extern uint8_t sysState;     // Xn: 0 normal, 1 boot sweep, 2 sleep, 3 fault
extern uint16_t health;      // Hxxx: bit i = window i healthy (board-major)
extern uint32_t flashUntil;  // SF
extern uint32_t bootStart;

// ---- palette (effects.cpp)
extern const CRGB SMALL_COLORS[3];
extern const CRGB BLOCK_COLORS[4];
extern const CRGB GOLD, ENGAGED_BLUE, LISTEN_BLUE, THINK_CYAN, FLASH_GREEN;
extern const CRGB VU_GREEN, VU_AMBER, ALERT_RED, MOUTH_BLUE, MOUTH_ORANGE, MOUTH_SPEAK;

// ---- helpers
// c scaled by k in 0..1 (nscale8, rounded down).
inline CRGB scaled(CRGB c, float k) {
  if (k <= 0) return CRGB::Black;
  if (k > 1) k = 1;
  c.nscale8((uint8_t)(k * 255));
  return c;
}
inline float frac(float x) { return x - floor(x); }
inline float rnd() { return random(10000) / 10000.0f; }   // 0..1

inline uint8_t smallIdx(uint8_t b, uint8_t k) { return b * BOARD_LEDS + k; }
inline uint8_t groupIdx(uint8_t b, uint8_t g) { return b * BOARD_LEDS + SMALL_PER_BOARD + g * LEDS_PER_GROUP; }
inline uint8_t windowIdx(uint8_t b, uint8_t k) { return groupIdx(b, EXPOSED[b][k]); }
// A window is a unit: set its colour (shown on all its LEDs by presentWindows).
inline void setWindow(uint8_t b, uint8_t k, CRGB c) { win[b * WINDOWS_PER_BOARD + k] = c; }
inline CRGB windowBase(uint8_t b, uint8_t k) { return BLOCK_COLORS[(k + b) % 4]; }
inline void setEyes(CRGB l, CRGB r) { mainLeds[BODY_LEDS] = l; mainLeds[BODY_LEDS + 1] = r; }
// Mouth V: distance of LED i from the tip (0 at the tip).
inline uint8_t mouthRank(uint8_t i) { return i < 4 ? 3 - i : i - 4; }
