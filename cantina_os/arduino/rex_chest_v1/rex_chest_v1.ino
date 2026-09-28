/*
 * DJ R3X Chest (middle ring) Logic Panel Lights - V1
 *
 * Arduino Nano + addressable LEDs (WS2812B) behind the three front logic panels.
 * Each panel (MS_P_1_Full) has a vertical column of 8 LED holes and 3 square windows
 * over diffuser blocks: 3 x (8 + 3) = 33 pixels. Layout was measured from the panel
 * geometry by sim/model/build_r3x.py.
 *
 * This is a port of sim/web/src/chest.ts - the 3D sim runs the same logic, so what the
 * sim shows is what this board shows. Keep the two in step.
 *
 * Serial protocol (115200 baud, newline-terminated). The interaction words match the face
 * controller (rex_face_v3_clean) so the chest moves in lockstep with the eyes; the rest
 * show the machine's overall status. Driven by CantinaOS ChestLightControllerService.
 *   SI SE SL ST SS   idle / engaged / listening / thinking / speaking      -> "+"
 *   SF               green "done" sparkle, alongside the eyes' green flash -> "+"
 *   Mnnn             speech amplitude 000-255 (fire and forget)
 *   Bnnn             music tempo in BPM, 000 = no music (fire and forget)
 *   Xn               system state: 0 normal, 1 booting, 2 sleep, 3 fault   -> "+"
 *   Hxxx             health mask, 3 hex digits, bit i = window i (panel-major);
 *                    a 0 bit blinks that window red (fire and forget)
 *   R                reset (silent - so the face adapter's "R" probe can't mistake
 *                    this board for the face)
 *   ?                identify: "Chest: ..."
 * Boots with "CHEST READY" (deliberately not "READY", which the face adapter waits for).
 *
 * Wiring (change below to match the harness):
 *   DATA_PIN D6 -> DIN of the first pixel. 5 V supply sized for 33 px (~2 A worst case
 *   at full white; typical use is far below that at brightness 128). 330 R in series
 *   with DIN and a 1000 uF cap across the strip supply are recommended.
 *   Strip order: panel 1 (leftmost as seen from the front) dots bottom->top, then its
 *   3 windows, then panel 2, then panel 3. If your harness runs differently, edit
 *   DOT_PIXEL / WINDOW_PIXEL - nothing else depends on the physical order.
 */

#include <FastLED.h>

// ============================================================ hardware

#define DATA_PIN 6
#define NUM_PANELS 3
#define DOTS_PER_PANEL 8
#define WINDOWS_PER_PANEL 3
#define NUM_LEDS (NUM_PANELS * (DOTS_PER_PANEL + WINDOWS_PER_PANEL))
#define GLOBAL_BRIGHTNESS 128   // same as the face board
#define FRAME_MS 20             // 50 Hz

CRGB leds[NUM_LEDS];

// Physical strip index of each dot (bottom -> top) and window, per panel.
const uint8_t DOT_PIXEL[NUM_PANELS][DOTS_PER_PANEL] = {
  { 0,  1,  2,  3,  4,  5,  6,  7},
  {11, 12, 13, 14, 15, 16, 17, 18},
  {22, 23, 24, 25, 26, 27, 28, 29},
};
const uint8_t WINDOW_PIXEL[NUM_PANELS][WINDOWS_PER_PANEL] = {
  { 8,  9, 10},
  {19, 20, 21},
  {30, 31, 32},
};

// ============================================================ palette

// Dots are indicator LEDs, windows are backlit readouts.
const CRGB DOT_COLORS[] = {
  CRGB(255, 20, 0), CRGB(255, 110, 0), CRGB(40, 255, 30), CRGB(255, 20, 0), CRGB(0, 120, 255)
};
const uint8_t NUM_DOT_COLORS = sizeof(DOT_COLORS) / sizeof(DOT_COLORS[0]);
const CRGB WIN_COLORS[] = {
  CRGB(255, 170, 60), CRGB(60, 140, 255), CRGB(255, 60, 20), CRGB(220, 230, 255)
};
const uint8_t NUM_WIN_COLORS = sizeof(WIN_COLORS) / sizeof(WIN_COLORS[0]);
const CRGB VU_GREEN(40, 255, 30), VU_AMBER(255, 110, 0), VU_RED(255, 20, 0);
const CRGB THINK_CYAN(0, 200, 255), LISTEN_BLUE(0, 120, 255);

// ============================================================ state

char mode = 'I';           // I E L T S
uint8_t amplitude = 0;     // 0-255
uint16_t bpm = 0;          // 0 = no music
uint8_t sysState = 1;      // X: 0 normal, 1 booting, 2 sleep, 3 fault (boot until told)
uint16_t health = 0x1FF;   // 9 windows, 1 = healthy
unsigned long sparkleUntil = 0;
unsigned long bootStart = 0;

struct DotState {
  bool on;
  uint8_t color;
  unsigned long next;
};
DotState dotState[NUM_PANELS][DOTS_PER_PANEL];

unsigned long lastFrame = 0;

// ============================================================ helpers
// (declared before use: the Arduino preprocessor's auto-prototypes can't be relied on for
//  functions returning CRGB)

CRGB scaled(const CRGB &c, float k) {
  if (k <= 0) return CRGB::Black;
  if (k > 1) k = 1;
  CRGB out = c;
  out.nscale8((uint8_t)(k * 255));
  return out;
}

float frac(float x) { return x - floor(x); }

// ============================================================ serial

char rxBuf[8];
uint8_t rxLen = 0;

void handleLine(const char *cmd, uint8_t len) {
  if (len == 2 && cmd[0] == 'S') {
    switch (cmd[1]) {
      case 'I': case 'E': case 'L': case 'T': case 'S':
        mode = cmd[1];
        Serial.println("+");
        return;
      case 'F':  // "done": green sparkle alongside the eyes' flash
        sparkleUntil = millis() + 350;
        Serial.println("+");
        return;
    }
    Serial.println("-");
  } else if (len == 4 && cmd[0] == 'M') {
    int v = atoi(cmd + 1);
    amplitude = (uint8_t)constrain(v, 0, 255);
  } else if (len == 4 && cmd[0] == 'B') {
    int v = atoi(cmd + 1);
    bpm = (uint16_t)constrain(v, 0, 999);
  } else if (len == 2 && cmd[0] == 'X' && cmd[1] >= '0' && cmd[1] <= '3') {
    uint8_t s = cmd[1] - '0';
    if (s == 1 && sysState != 1) bootStart = millis();
    sysState = s;
    Serial.println("+");
  } else if (len == 4 && cmd[0] == 'H') {
    health = (uint16_t)(strtol(cmd + 1, NULL, 16) & 0x1FF);
  } else if (len == 1 && cmd[0] == 'R') {
    mode = 'I';
    amplitude = 0;
    bpm = 0;
    sysState = 0;
    health = 0x1FF;
  } else if (len == 1 && cmd[0] == '?') {
    Serial.println(F("Chest: rex_chest_v1 SI SE SL ST SS SF Mnnn Bnnn Xn Hxxx R ?"));
  } else if (len > 0) {
    Serial.println("-");
  }
}

void processSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      rxBuf[rxLen] = '\0';
      handleLine(rxBuf, rxLen);
      rxLen = 0;
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    } else {
      rxLen = 0;  // overlong garbage: drop the line
    }
  }
}

// ============================================================ animation

// Whole-chest states that override the interaction patterns.
bool renderSystemState(unsigned long ms) {
  const float t = ms / 1000.0f;
  // Powered but no host after a minute: stop the boot sweep and run standalone.
  if (sysState == 1 && ms - bootStart > 60000UL) sysState = 0;
  if (sysState == 1) {
    // Boot: each panel fills bottom -> top in turn, then its windows light; repeats.
    float cycle = frac((ms - bootStart) / 2400.0f) * NUM_PANELS * 1.25f;
    for (uint8_t p = 0; p < NUM_PANELS; p++) {
      float f = constrain(cycle - p, 0.0f, 1.25f);
      for (uint8_t k = 0; k < DOTS_PER_PANEL; k++)
        leds[DOT_PIXEL[p][k]] = (f * DOTS_PER_PANEL > k) ? LISTEN_BLUE : CRGB::Black;
      for (uint8_t k = 0; k < WINDOWS_PER_PANEL; k++)
        leds[WINDOW_PIXEL[p][k]] = f >= 1.0f ? scaled(WIN_COLORS[(k + p * 2) % NUM_WIN_COLORS], 0.6f) : CRGB::Black;
    }
    return true;
  }
  if (sysState == 3) {
    // Fault: windows pulse red at 1 Hz, a dim red scan on the dots.
    float pulse = 0.25f + 0.75f * (0.5f + 0.5f * sin(t * TWO_PI));
    int pos = (int)(t * 6) % DOTS_PER_PANEL;
    for (uint8_t p = 0; p < NUM_PANELS; p++) {
      for (uint8_t k = 0; k < DOTS_PER_PANEL; k++)
        leds[DOT_PIXEL[p][k]] = k == pos ? scaled(VU_RED, 0.6f) : CRGB::Black;
      for (uint8_t k = 0; k < WINDOWS_PER_PANEL; k++)
        leds[WINDOW_PIXEL[p][k]] = scaled(VU_RED, pulse);
    }
    return true;
  }
  if (sysState == 2) {
    // Sleep: windows breathe at 8%, one standby dot blinks.
    float b = 0.03f + 0.05f * (0.5f + 0.5f * sin(t * 0.8f));
    for (uint8_t p = 0; p < NUM_PANELS; p++) {
      for (uint8_t k = 0; k < DOTS_PER_PANEL; k++) leds[DOT_PIXEL[p][k]] = CRGB::Black;
      for (uint8_t k = 0; k < WINDOWS_PER_PANEL; k++)
        leds[WINDOW_PIXEL[p][k]] = scaled(WIN_COLORS[(k + p * 2) % NUM_WIN_COLORS], b);
    }
    if ((ms / 1000) % 3 == 0) leds[DOT_PIXEL[0][0]] = scaled(VU_GREEN, 0.4f);
    return true;
  }
  return false;
}

void renderFrame(unsigned long ms) {
  if (renderSystemState(ms)) return;
  const float t = ms / 1000.0f;
  const float amp = sqrt(amplitude / 255.0f);
  const float beat = bpm > 0 ? t * bpm / 60.0f : 0;
  const float beatPhase = frac(beat);
  const uint8_t totalDots = NUM_PANELS * DOTS_PER_PANEL;

  for (uint8_t p = 0; p < NUM_PANELS; p++) {
    // ---------------------------------------------------- dots
    for (uint8_t k = 0; k < DOTS_PER_PANEL; k++) {
      DotState &st = dotState[p][k];
      CRGB c = CRGB::Black;
      if (bpm > 0) {
        // Chase: a lit pair sweeps up each panel once per beat, panels offset.
        int row = (int)(frac(beat + p / 3.0f) * DOTS_PER_PANEL);
        if (k == row || k == row - 1) c = DOT_COLORS[(k + p) % NUM_DOT_COLORS];
        else c = scaled(DOT_COLORS[0], 0.06f);
      } else if (mode == 'S') {
        // VU meter, green -> amber -> red.
        int lit = (int)(amp * DOTS_PER_PANEL + 0.5f);
        if (k < lit) c = k < 4 ? VU_GREEN : (k < 6 ? VU_AMBER : VU_RED);
      } else if (mode == 'T') {
        int pos = (int)(t * 18) % totalDots;
        if (pos == p * DOTS_PER_PANEL + k) c = THINK_CYAN;
        else c = scaled(DOT_COLORS[st.color], st.on ? 0.15f : 0);
      } else if (mode == 'L') {
        int row = (int)(frac(t * 1.6f) * (DOTS_PER_PANEL + 2));
        if (k <= row) c = scaled(LISTEN_BLUE, 0.35f + 0.65f * (k == row ? 1.0f : 0.4f));
      } else {
        // Idle / engaged: indicator twinkle.
        if (ms >= st.next) {
          st.on = random(100) < (mode == 'E' ? 60 : 45);
          if (random(100) < 20) st.color = random(NUM_DOT_COLORS);
          st.next = ms + 150 + random(mode == 'E' ? 500 : 1100);
        }
        if (st.on) c = DOT_COLORS[st.color];
      }
      leds[DOT_PIXEL[p][k]] = c;
    }

    // ---------------------------------------------------- windows
    for (uint8_t k = 0; k < WINDOWS_PER_PANEL; k++) {
      const uint8_t i = WINDOW_PIXEL[p][k];
      const CRGB base = WIN_COLORS[(k + p * 2) % NUM_WIN_COLORS];
      float level;
      if (bpm > 0) {
        bool accent = ((k + p) % 2) == ((long)floor(beat) % 2);
        level = 0.35f + 0.65f * exp(-beatPhase * 6) * (accent ? 1.0f : 0.4f);
      } else if (mode == 'S') {
        level = 0.35f + 0.65f * amp;
      } else if (mode == 'T') {
        level = 0.3f + 0.3f * sin(t * 8 + i);
      } else {
        level = 0.45f + 0.25f * sin(t * (0.6f + 0.17f * k) + i * 1.7f);
      }
      // A subsystem that is down blinks its window red, whatever the pattern.
      uint8_t bit = p * WINDOWS_PER_PANEL + k;
      if (!(health & (1 << bit))) leds[i] = ((ms / 250) % 2) ? scaled(VU_RED, 0.9f) : CRGB::Black;
      else leds[i] = scaled(base, level);
    }
  }

  // "Done" sparkle: random dots flash green for ~350 ms after SF.
  if (ms < sparkleUntil) {
    for (uint8_t p = 0; p < NUM_PANELS; p++)
      for (uint8_t k = 0; k < DOTS_PER_PANEL; k++)
        if (random(100) < 55) leds[DOT_PIXEL[p][k]] = VU_GREEN;
  }
}

// ============================================================ main

void setup() {
  Serial.begin(115200);
  FastLED.addLeds<WS2812B, DATA_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(GLOBAL_BRIGHTNESS);
  FastLED.clear(true);
  randomSeed(analogRead(A0));
  for (uint8_t p = 0; p < NUM_PANELS; p++) {
    for (uint8_t k = 0; k < DOTS_PER_PANEL; k++) {
      dotState[p][k].on = random(2);
      dotState[p][k].color = random(NUM_DOT_COLORS);
      dotState[p][k].next = 0;
    }
  }
  bootStart = millis();
  Serial.println("CHEST READY");
}

void loop() {
  processSerial();
  unsigned long now = millis();
  if (now - lastFrame >= FRAME_MS) {
    lastFrame = now;
    renderFrame(now);
    FastLED.show();
  }
}
