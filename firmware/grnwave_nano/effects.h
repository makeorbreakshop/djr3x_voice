// grnwave_nano - the effects and the registry that picks them.
//
// To customise: edit an fx_*.cpp (each one documents its parameters at the top), or write a
// new one with the same signature, declare it here and put it in LOOKS (effects.cpp).
// Keep rust/crates/r3x-performer-core/src/leds/grnwave.rs in step so the sim shows it.
#pragma once
#include "state.h"

typedef void (*Effect)(const Ctx &c);

// One look per state: an effect for each zone.
struct Look {
  char mode;       // I E L T S
  Effect dots;     // the 8 small LEDs per body board
  Effect windows;  // the 3 windows per board, as units: writes win[] (setWindow)
  Effect blocks;   // per pixel inside each window, only without diffusers (DIFFUSED 0); NULL = flat
  Effect eyes;
  Effect mouth;
};
extern const Look LOOKS[];
extern const uint8_t NUM_LOOKS;

// body dots
void fx_twinkle(const Ctx &c);
void fx_fill(const Ctx &c);
void fx_scan(const Ctx &c);
void fx_vu(const Ctx &c);
void fx_beat_chase(const Ctx &c);   // replaces the dots while music plays (Bnnn > 0)
// windows (units: one colour each)
void fx_win_blink(const Ctx &c);    // the droid's blinking blocks
void fx_win_breathe(const Ctx &c);
void fx_win_think(const Ctx &c);
void fx_win_level(const Ctx &c);
void fx_win_beat(const Ctx &c);     // replaces the windows while music plays
// window -> pixels
void presentWindows(const Ctx &c, Effect blocks);  // win[] onto the LEDs (+ blocks if bare)
void fx_blk_spin(const Ctx &c);     // bare windows: a bright corner circles the 2 x 2
void fx_hidden_off(const Ctx &c);   // groups not behind a window: dark
void fx_health(const Ctx &c);       // overlay: a down subsystem's window blinks red
// eyes
void fx_eyes_flicker(const Ctx &c);
void fx_eyes_solid(const Ctx &c);
void fx_eyes_pulse(const Ctx &c);
void fx_eyes_alternate(const Ctx &c);
void fx_eyes_speak(const Ctx &c);
void fx_flash(const Ctx &c);        // SF, for FLASH_MS
// mouth
void fx_mouth_glow(const Ctx &c);
void fx_mouth_pulse(const Ctx &c);
void fx_mouth_vu(const Ctx &c);
// whole-droid system states (Xn)
void fx_boot(const Ctx &c);
void fx_sleep(const Ctx &c);
void fx_fault(const Ctx &c);
