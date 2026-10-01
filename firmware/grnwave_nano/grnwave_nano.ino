/*
 * grnwave_nano - DJ R3X firmware for the grnwave workshop DJ Rex Full LED Set
 * (3 body LED boards, eye board, mouth board) on an Arduino Nano.
 *
 * Speaks our named_v1 protocol - the same words as the face board (rex_face_v3_clean) and
 * the chest board (rex_chest_v1) - so the R3X runtime drives it unchanged
 * (r3x-drivers `driver.grnwave`). Written from the facts of grnwave's sample sketch (pin 4,
 * chain order, LED counts); none of its code is used. See README.md.
 *
 * Serial, 115200 baud, newline-terminated:
 *   SI SE SL ST SS   idle / engaged / listening / thinking / speaking      -> "+"
 *   SF               green "done" flash, then engaged                      -> "+"
 *   Mnnn             speech amplitude 000-255
 *   Bnnn             music tempo, 000 = no music
 *   Xn               system: 0 normal, 1 boot sweep, 2 sleep, 3 fault      -> "+"
 *   Hxxx             health mask (hex), bit i = window i healthy
 *   R                reset (silent)
 *   ?                "Grnwave: ..."
 * Boots printing "GRNWAVE READY" (not "READY": that is the face board's).
 *
 * Twin: rust/crates/r3x-performer-core/src/leds/grnwave.rs (what the sim shows).
 */
#include <FastLED.h>
#include "config.h"
#include "state.h"
#include "effects.h"

#if MOUTH_ON_MAIN
CRGB chain[MAIN_LEDS + MOUTH_LEDS];
#endif
CRGB mainLeds[MAIN_LEDS];
CRGB mouthLeds[MOUTH_LEDS];
CRGB win[WINDOWS];

char mode = 'I';
uint8_t amplitude = 0;
uint16_t bpm = 0;
uint8_t sysState = 1;       // boot sweep until the host says X0
uint16_t health = 0x1FF;
uint32_t flashUntil = 0;
uint32_t bootStart = 0;

static char rxBuf[16];
static uint8_t rxLen = 0;
static uint32_t lastFrame = 0;

static bool digits3(const char *s) {
  return isDigit(s[0]) && isDigit(s[1]) && isDigit(s[2]);
}

static void handleLine(const char *cmd, uint8_t len) {
  if (len == 2 && cmd[0] == 'S') {
    switch (cmd[1]) {
      case 'I': case 'E': case 'L': case 'T': case 'S':
        mode = cmd[1];
        Serial.println("+");
        return;
      case 'F':
        flashUntil = millis() + FLASH_MS;
        mode = 'E';
        Serial.println("+");
        return;
    }
    Serial.println("-");
  } else if (len == 4 && cmd[0] == 'M' && digits3(cmd + 1)) {
    int v = atoi(cmd + 1);
    amplitude = v > 255 ? 255 : v;
  } else if (len == 4 && cmd[0] == 'B' && digits3(cmd + 1)) {
    bpm = atoi(cmd + 1);
  } else if (len == 2 && cmd[0] == 'X' && cmd[1] >= '0' && cmd[1] <= '3') {
    uint8_t s = cmd[1] - '0';
    if (s == 1 && sysState != 1) bootStart = millis();
    sysState = s;
    Serial.println("+");
  } else if (len == 4 && cmd[0] == 'H' && isHexadecimalDigit(cmd[1]) && isHexadecimalDigit(cmd[2]) && isHexadecimalDigit(cmd[3])) {
    health = strtol(cmd + 1, NULL, 16) & 0x1FF;
  } else if (len == 1 && cmd[0] == 'R') {
    mode = 'I'; amplitude = 0; bpm = 0; sysState = 0; health = 0x1FF; flashUntil = 0;
  } else if (len == 1 && cmd[0] == '?') {
    Serial.println("Grnwave: body 96 eyes 2 mouth 8 (named_v1)");
  } else if (len >= 1 && (cmd[0] == 'S' || cmd[0] == 'X')) {
    Serial.println("-");
  }
}

static void readSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      rxBuf[rxLen] = 0;
      if (rxLen) handleLine(rxBuf, rxLen);
      rxLen = 0;
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = ch;
    }
  }
}

static void render(uint32_t ms) {
  Ctx c;
  c.ms = ms;
  c.level = sqrt(amplitude / 255.0f);
  c.bpm = bpm;
  c.beat = bpm ? ms / 1000.0f * bpm / 60.0f : 0;

  if (sysState == 1 && ms - bootStart > BOOT_TIMEOUT_MS) sysState = 0;
  if (sysState == 1) { fx_boot(c); return; }
  if (sysState == 2) { fx_sleep(c); return; }
  if (sysState == 3) { fx_fault(c); return; }

  const Look *look = &LOOKS[0];
  for (uint8_t i = 0; i < NUM_LOOKS; i++) if (LOOKS[i].mode == mode) look = &LOOKS[i];
  if (bpm) {
    fx_beat_chase(c);
    fx_win_beat(c);
  } else {
    look->dots(c);
    look->windows(c);
  }
  fx_health(c);
  presentWindows(c, bpm ? NULL : look->blocks);
  fx_hidden_off(c);
  if (ms < flashUntil) fx_flash(c); else look->eyes(c);
  look->mouth(c);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
#if MOUTH_ON_MAIN
  FastLED.addLeds<LED_TYPE, MAIN_PIN, COLOR_ORDER>(chain, MAIN_LEDS + MOUTH_LEDS);
#else
  FastLED.addLeds<LED_TYPE, MAIN_PIN, COLOR_ORDER>(mainLeds, MAIN_LEDS);
  FastLED.addLeds<LED_TYPE, MOUTH_PIN, COLOR_ORDER>(mouthLeds, MOUTH_LEDS);
#endif
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.setMaxPowerInVoltsAndMilliamps(5, POWER_LIMIT_MA);
  randomSeed(analogRead(A0));
  FastLED.clear(true);
  bootStart = millis();
  Serial.println("GRNWAVE READY");
}

void loop() {
  readSerial();
  uint32_t now = millis();
  if (now - lastFrame < FRAME_MS) return;
  lastFrame = now;
  render(now);
#if MOUTH_ON_MAIN
  memcpy(chain, mainLeds, sizeof(mainLeds));
  memcpy(chain + MAIN_LEDS, mouthLeds, sizeof(mouthLeds));
#endif
  FastLED.show();
}
