// grnwave_nano - hardware configuration. Edit this file to match your harness.
//
// Facts from grnwave's sample sketch (github.com/grnwaveworkshop/DJ-RexBody):
//   FastLED on pin 4; body board A, B, C (32 LEDs each), then the two eye LEDs, all on one
//   chain. Per body board: 8 small LEDs (light pipes), then 6 groups of 4 x 5050.
// Our choices (change freely): the mouth board on its own pin, brightness, power cap.
#pragma once

#define MAIN_PIN 4            // body A DIN; eyes daisy-chained after body C
#define MOUTH_PIN 5           // mouth board DIN (set MOUTH_ON_MAIN to chain it after the eyes)
#ifndef MOUTH_ON_MAIN
#define MOUTH_ON_MAIN 0       // 1: mouth at 98-105 on MAIN_PIN, MOUTH_PIN unused
#endif
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB

#define BODY_BOARDS 3
#define SMALL_PER_BOARD 8
#define GROUPS_PER_BOARD 6
#define LEDS_PER_GROUP 4
#define BOARD_LEDS (SMALL_PER_BOARD + GROUPS_PER_BOARD * LEDS_PER_GROUP)   // 32
#define BODY_LEDS (BODY_BOARDS * BOARD_LEDS)                               // 96
#define EYE_LEDS 2                                                         // 96 = droid's left
#define MAIN_LEDS (BODY_LEDS + EYE_LEDS)                                   // 98
#define MOUTH_LEDS 8          // a V: 0-3 viewer-left arm top -> tip, 4-7 right arm tip -> top
#define WINDOWS_PER_BOARD 3
#define WINDOWS (BODY_BOARDS * WINDOWS_PER_BOARD)                          // 9 = health bits

// Which of a board's 6 groups sit behind that panel's 3 windows (the groups grnwave's
// sample animates: A 12/24/28, B 40/44/48, C 84/88/92). The other groups stay dark.
// If a panel shows different groups on your droid, change its row here - nothing else.
static const uint8_t EXPOSED[BODY_BOARDS][WINDOWS_PER_BOARD] = {
  {1, 4, 5},
  {0, 1, 2},
  {3, 4, 5},
};

// Diffusers: an opal cover over each window (profiles/electronics/grnwave_full_led.json,
// lights.body.windows) mixes its 4 x 5050 into one colour, so effects author windows as
// units (win[] in state.h). 0 = bare windows: LOOKS' `blocks` column then adds per-pixel
// detail inside each window.
#ifndef DIFFUSED
#define DIFFUSED 1
#endif

#define BRIGHTNESS 128        // FastLED.setBrightness (the face board uses 128 too)
#define POWER_LIMIT_MA 3000   // FastLED power cap at 5 V: size the supply above this
#define FRAME_MS 20           // 50 Hz
#define FLASH_MS 350          // SF green flash
#define BOOT_TIMEOUT_MS 60000 // the boot sweep gives up on its own after this
#define SERIAL_BAUD 115200
