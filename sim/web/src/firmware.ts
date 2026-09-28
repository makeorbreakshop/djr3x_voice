/**
 * Emulator for cantina_os/arduino/rex_face_v3_clean/rex_face_v3_clean.ino.
 *
 * A line-for-line port, not a re-imagining: same serial parser, same state machine, same
 * timers, same FastLED 8-bit arithmetic. `advanceTo(ms)` runs the Arduino `loop()` on a
 * 1 ms simulated clock (10 ms per iteration while a FLASH runs, because the sketch calls
 * `delay(10)` there), so an animation looks and times the way it does on the board.
 *
 * Feed it the exact bytes CantinaOS writes to the serial port (`SI`, `SS`, `M128`, ...)
 * and read back `eyeLeds` (14 x RGB) and `mouthLeds` (8 x RGB) - the frame buffers
 * before FastLED's global brightness is applied (see OUTPUT_BRIGHTNESS).
 *
 * Deliberately reproduced firmware defect: in THINKING the right eye's second dot index
 * collapses onto the first, so the right eye shows one dot, and at step 0 it writes
 * `eyeLeds[14]` - one past the end. With `oobAliasesMouth` (default on) that write lands
 * in `mouthLeds[0]`, which is where avr-gcc usually places the next global. On hardware
 * that would show as the top-left mouth LED turning cyan during THINKING. Verify there.
 */

export type RGB = [number, number, number];

export const NUM_EYE_LEDS = 14;
export const LEDS_PER_EYE = 7;
export const LEFT_EYE_START = 0;
export const RIGHT_EYE_START = 7;
export const NUM_MOUTH_LEDS = 8;
/** FastLED.setBrightness(128) - applied at show() time. */
export const OUTPUT_BRIGHTNESS = 128;

export enum State {
  IDLE = 'IDLE',
  ENGAGED = 'ENGAGED',
  LISTENING = 'LISTENING',
  THINKING = 'THINKING',
  SPEAKING = 'SPEAKING',
}

const COLOR_IDLE_EYES_OUTER: RGB = [255, 60, 0];
const COLOR_IDLE_EYES_CENTER: RGB = [255, 180, 80];
const COLOR_IDLE_MOUTH: RGB = [0, 50, 150];
const COLOR_ENGAGED_EYES: RGB = [0, 35, 110];
const COLOR_ENGAGED_CENTER: RGB = [100, 180, 255];
const COLOR_ENGAGED_MOUTH: RGB = [255, 60, 0];
const COLOR_THINKING_DOT: RGB = [0, 255, 255];
const COLOR_FLASH: RGB = [0, 255, 0];
const BLACK: RGB = [0, 0, 0];

const TWO_PI = Math.PI * 2;

/** C float -> uint8_t conversion (truncate, wrap like the implicit cast). */
const u8 = (v: number) => (Math.trunc(v) & 0xff) >>> 0;

/** FastLED scale8 with FASTLED_SCALE8_FIXED: (i * (1 + scale)) >> 8. */
export const scale8 = (i: number, scale: number) => ((i * (1 + scale)) >> 8) & 0xff;

/** CRGB::nscale8(uint8_t) on a copy. */
function nscale8(c: RGB, scale: number): RGB {
  const s = u8(scale);
  return [scale8(c[0], s), scale8(c[1], s), scale8(c[2], s)];
}

interface Timer {
  lastUpdate: number;
  interval: number;
  step: number;
}

export interface FirmwareOptions {
  /** Model the eyeLeds[14] overrun as a write to mouthLeds[0]. */
  oobAliasesMouth?: boolean;
  random?: () => number;
}

export class RexFaceFirmware {
  readonly eyeLeds: RGB[] = Array.from({ length: NUM_EYE_LEDS }, () => [0, 0, 0] as RGB);
  readonly mouthLeds: RGB[] = Array.from({ length: NUM_MOUTH_LEDS }, () => [0, 0, 0] as RGB);

  currentState = State.IDLE;
  previousState = State.IDLE;
  flashActive = false;
  mouthAmplitude = 0;
  /** Simulated millis(). */
  now = 0;

  private breathingTimer: Timer = { lastUpdate: 0, interval: 20, step: 0 };
  private pulseTimer: Timer = { lastUpdate: 0, interval: 20, step: 0 };
  private thinkingTimer: Timer = { lastUpdate: 0, interval: 30, step: 0 };
  private mouthGlowTimer: Timer = { lastUpdate: 0, interval: 16, step: 0 };

  private breathingPhase = 0;
  private mouthGlowPhase = 0;
  private mouthBrightnessOffset = 0;
  private mouthSpeedMultiplier = 1.0;
  private readonly mouthBrightnessDrift = 0.002;

  private isBlinking = false;
  private nextBlinkTime = 0;
  private blinkStartTime = 0;
  private flashStep = 0;

  private rx: string[] = [];
  private commandBuffer = '';
  private readingCommand = false;
  private readonly out: string[] = [];
  private readonly rand: () => number;
  readonly oobAliasesMouth: boolean;

  constructor(opts: FirmwareOptions = {}) {
    this.rand = opts.random ?? Math.random;
    this.oobAliasesMouth = opts.oobAliasesMouth ?? true;
    this.setup();
  }

  /** Arduino random(min, max): [min, max). */
  private random(min: number, max: number) {
    return min + Math.floor(this.rand() * (max - min));
  }

  private setup() {
    this.fill(this.eyeLeds, BLACK);
    this.fill(this.mouthLeds, BLACK);
    this.nextBlinkTime = this.now + this.random(12000, 25000);
    // setState(STATE_IDLE) with currentState already IDLE and no flash returns early -
    // exactly as in the sketch, so the eyes stay black until the first breathing tick.
    this.setState(State.IDLE);
    this.println('READY');
  }

  // ------------------------------------------------------------------ serial I/O

  /** Bytes from the host, as CantinaOS writes them (e.g. "SS\n", "M128\n"). */
  write(data: string) {
    for (const c of data) this.rx.push(c);
  }

  /** Lines the sketch printed since the last call. */
  readLines(): string[] {
    return this.out.splice(0, this.out.length);
  }

  private println(s: string) {
    this.out.push(s);
  }

  // ------------------------------------------------------------------ clock

  /** Run loop() until simulated time reaches `ms`. */
  advanceTo(ms: number) {
    // Guard against a huge catch-up after a background tab: never simulate > 2 s at once.
    if (ms - this.now > 2000) this.now = ms - 2000;
    while (this.now < ms) {
      const flashing = this.flashActive;
      this.loop();
      this.now += flashing ? 10 : 1;
    }
  }

  private loop() {
    this.processSerialCommands();
    this.updateBaseState();
    this.applyBreathingEffect();
    this.applyBlinkingEffect();
    this.updateMouth();
    this.updateFlash();
  }

  // ------------------------------------------------------------------ commands

  private processSerialCommands() {
    while (this.rx.length > 0) {
      const c = this.rx.shift()!;

      if (c === 'S' && !this.readingCommand) {
        this.commandBuffer = 'S';
        this.readingCommand = true;
        continue;
      }
      if (c === 'M' && !this.readingCommand) {
        this.commandBuffer = 'M';
        this.readingCommand = true;
        continue;
      }
      if (c === 'T' && !this.readingCommand) {
        this.commandBuffer = 'T';
        this.readingCommand = true;
        continue;
      }

      if (this.readingCommand) {
        this.commandBuffer += c;
        const b = this.commandBuffer;
        if (b.length === 2 && b[0] === 'S') {
          this.handleStateCommand(b[1]);
          this.commandBuffer = '';
          this.readingCommand = false;
        } else if (b.length === 4 && b[0] === 'M') {
          // String::toInt(): leading digits, 0 if none.
          const amp = parseInt(b.substring(1), 10);
          this.mouthAmplitude = Math.min(255, Math.max(0, Number.isNaN(amp) ? 0 : amp));
          this.commandBuffer = '';
          this.readingCommand = false;
        } else if (b[0] === 'T' && (b.length === 1 || b.length === 2)) {
          if (b.length === 1 || c === '\n' || c === '\r') {
            this.handleTestCommand(b);
            this.commandBuffer = '';
            this.readingCommand = false;
          } else if (b.length === 2 && c >= '1' && c <= '7') {
            this.handleTestCommand(b);
            this.commandBuffer = '';
            this.readingCommand = false;
          }
        } else if (b.length > 4) {
          this.commandBuffer = '';
          this.readingCommand = false;
        }
        continue;
      }

      if (c === 'R') {
        this.resetSystem();
        this.println('+');
      } else if (c === '?') {
        this.println('Commands: SI SE SL ST SS SF Mnnn R ? T T1-T7');
      }
    }
  }

  private handleTestCommand(cmd: string) {
    // The self-tests are blocking delay() sequences for bench checks; not emulated.
    this.println(`(test ${cmd} not emulated)`);
  }

  private handleStateCommand(ch: string) {
    switch (ch) {
      case 'I': this.setState(State.IDLE); break;
      case 'E': this.setState(State.ENGAGED); break;
      case 'L': this.setState(State.LISTENING); break;
      case 'T': this.setState(State.THINKING); break;
      case 'S': this.setState(State.SPEAKING); break;
      case 'F': this.triggerFlash(); break;
      default:
        this.println('-');
        return;
    }
    this.println('+');
  }

  // ------------------------------------------------------------------ state

  private setState(newState: State) {
    if (newState === this.currentState && !this.flashActive) return;

    this.previousState = this.currentState;
    this.currentState = newState;
    this.breathingTimer.step = 0;
    this.pulseTimer.step = 0;
    this.thinkingTimer.step = 0;

    switch (this.currentState) {
      case State.IDLE:
        this.setIdleEyes();
        this.nextBlinkTime = this.now + this.random(12000, 25000);
        break;
      case State.ENGAGED:
        this.setEngagedEyes();
        this.fill(this.mouthLeds, BLACK);
        break;
      case State.THINKING:
        this.fill(this.eyeLeds, BLACK);
        break;
      default:
        break;
    }
  }

  private setBothEyes(i: number, c: RGB) {
    this.eyeLeds[LEFT_EYE_START + i] = [...c] as RGB;
    this.eyeLeds[RIGHT_EYE_START + i] = [...c] as RGB;
  }

  private setIdleEyes() {
    for (let i = 0; i < LEDS_PER_EYE; i++) {
      this.setBothEyes(i, i === 0 ? COLOR_IDLE_EYES_CENTER : COLOR_IDLE_EYES_OUTER);
    }
  }

  private setEngagedEyes() {
    for (let i = 0; i < LEDS_PER_EYE; i++) {
      this.setBothEyes(i, i === 0 ? COLOR_ENGAGED_CENTER : COLOR_ENGAGED_EYES);
    }
  }

  // ------------------------------------------------------------------ base animations

  private updateBaseState() {
    switch (this.currentState) {
      case State.LISTENING: this.updateListeningPulse(); break;
      case State.THINKING: this.updateThinkingAnimation(); break;
      case State.SPEAKING: this.updateSpeakingPulse(); break;
      default: break;
    }
  }

  private scaledBothEyes(brightness: number, center: RGB, outer: RGB) {
    for (let i = 0; i < LEDS_PER_EYE; i++) {
      this.setBothEyes(i, nscale8(i === 0 ? center : outer, brightness * 255));
    }
  }

  private updateListeningPulse() {
    const t = this.pulseTimer;
    if (this.now - t.lastUpdate < t.interval) return;
    t.lastUpdate = this.now;
    const cycle = t.step % 50;
    const brightness = cycle < 10 ? 0.3 + (cycle / 10.0) * 0.7 : 1.0 - ((cycle - 10) / 40.0) * 0.7;
    this.scaledBothEyes(brightness, COLOR_ENGAGED_CENTER, COLOR_ENGAGED_EYES);
    t.step++;
  }

  private updateThinkingAnimation() {
    const t = this.thinkingTimer;
    if (this.now - t.lastUpdate < t.interval) return;
    t.lastUpdate = this.now;

    this.setEngagedEyes();
    const half = Math.trunc(t.step / 2);
    const leftPos1 = (half % 6) + 1;
    const leftPos2 = (leftPos1 % 6) + 1;
    const rightPos1 = 7 - (half % 6);
    const rightPos2 = ((rightPos1 - 7) % 6) + 7; // C '%' truncates toward zero, as JS does

    this.eyeLeds[LEFT_EYE_START + leftPos1] = [...COLOR_THINKING_DOT];
    this.eyeLeds[LEFT_EYE_START + leftPos2] = [...COLOR_THINKING_DOT];
    this.writeEye(RIGHT_EYE_START + rightPos1, COLOR_THINKING_DOT);
    this.writeEye(RIGHT_EYE_START + rightPos2, COLOR_THINKING_DOT);
    t.step++;
  }

  /** eyeLeds[i] = c, including the sketch's out-of-bounds index 14. */
  private writeEye(i: number, c: RGB) {
    if (i < NUM_EYE_LEDS) this.eyeLeds[i] = [...c];
    else if (this.oobAliasesMouth && i - NUM_EYE_LEDS < NUM_MOUTH_LEDS) this.mouthLeds[i - NUM_EYE_LEDS] = [...c];
  }

  private updateSpeakingPulse() {
    const t = this.pulseTimer;
    if (this.now - t.lastUpdate < t.interval) return;
    t.lastUpdate = this.now;
    const pulse = (Math.sin(t.step * 0.1) + 1) / 2;
    this.scaledBothEyes(0.7 + pulse * 0.3, COLOR_ENGAGED_CENTER, COLOR_ENGAGED_EYES);
    t.step++;
  }

  // ------------------------------------------------------------------ effect layers

  private applyBreathingEffect() {
    if (this.currentState !== State.IDLE && this.currentState !== State.ENGAGED) return;
    const t = this.breathingTimer;
    if (this.now - t.lastUpdate < t.interval) return;
    t.lastUpdate = this.now;

    this.breathingPhase += 0.036;
    if (this.breathingPhase > TWO_PI) this.breathingPhase -= TWO_PI;
    const breathValue = (Math.sin(this.breathingPhase) + 1) / 2;
    const brightness = 0.85 + breathValue * 0.15;

    if (this.currentState === State.IDLE) {
      this.scaledBothEyes(brightness, COLOR_IDLE_EYES_CENTER, COLOR_IDLE_EYES_OUTER);
    } else {
      this.scaledBothEyes(brightness, COLOR_ENGAGED_CENTER, COLOR_ENGAGED_EYES);
    }
  }

  private applyBlinkingEffect() {
    if (this.currentState !== State.IDLE) return;
    if (!this.isBlinking && this.now >= this.nextBlinkTime) {
      this.isBlinking = true;
      this.blinkStartTime = this.now;
    }
    if (!this.isBlinking) return;

    const elapsed = this.now - this.blinkStartTime;
    if (elapsed < 100) {
      this.scaledBothEyes(1.0 - (elapsed / 100.0) * 0.9, COLOR_IDLE_EYES_CENTER, COLOR_IDLE_EYES_OUTER);
    } else if (elapsed < 200) {
      this.scaledBothEyes(0.1 + ((elapsed - 100) / 100.0) * 0.9, COLOR_IDLE_EYES_CENTER, COLOR_IDLE_EYES_OUTER);
    } else {
      this.isBlinking = false;
      this.nextBlinkTime = this.now + this.random(12000, 25000);
    }
  }

  /** Force the next idle blink now (for the UI - the sketch has no such command). */
  blinkNow() {
    if (this.currentState === State.IDLE && !this.isBlinking) this.nextBlinkTime = this.now;
  }

  // ------------------------------------------------------------------ mouth

  private updateMouth() {
    if (this.currentState === State.IDLE) {
      const t = this.mouthGlowTimer;
      if (this.now - t.lastUpdate < t.interval) return;
      t.lastUpdate = this.now;

      this.fill(this.mouthLeds, BLACK);
      this.mouthBrightnessOffset += this.mouthBrightnessDrift;
      if (this.mouthBrightnessOffset > TWO_PI) this.mouthBrightnessOffset -= TWO_PI;
      this.mouthSpeedMultiplier = 1.0 + Math.sin(this.mouthBrightnessOffset * 0.3) * 0.3;
      this.mouthGlowPhase += 0.00523 * this.mouthSpeedMultiplier;
      if (this.mouthGlowPhase > TWO_PI) this.mouthGlowPhase -= TWO_PI;

      const glowValue = (Math.sin(this.mouthGlowPhase) + 1) / 2;
      const brightnessDrift = Math.sin(this.mouthBrightnessOffset) * 0.05;
      let brightness = 0.2 + glowValue * 0.18 + brightnessDrift;
      brightness = Math.min(0.42, Math.max(0.15, brightness));

      const tube: RGB = [...COLOR_IDLE_MOUTH];
      if (glowValue > 0.5) {
        let warmth = (glowValue - 0.5) / 0.5;
        warmth = warmth * warmth;
        const warmAdd = Math.trunc(warmth * 35);
        tube[0] = u8(Math.min(255, tube[0] + warmAdd));
        tube[1] = u8(Math.min(255, tube[1] + warmAdd * 0.4));
      }
      const glow = nscale8(tube, brightness * 255);
      for (const i of [1, 2, 5, 6]) this.mouthLeds[i] = [...glow];
      return;
    }

    if (this.currentState !== State.SPEAKING && this.currentState !== State.ENGAGED) return;

    if (this.mouthAmplitude === 0) {
      this.fill(this.mouthLeds, BLACK);
      return;
    }

    const scaledAmp = Math.sqrt(Math.sqrt(this.mouthAmplitude / 255.0));
    this.fill(this.mouthLeds, BLACK);
    const mouth = COLOR_ENGAGED_MOUTH;

    if (scaledAmp > 0.0) {
      const s1 = Math.min(1.0, scaledAmp / 0.4);
      const middleBright = Math.trunc(s1 * s1 * 180);
      const middle: RGB = [...mouth];
      if (scaledAmp > 0.95) {
        const w = (scaledAmp - 0.95) / 0.05;
        const whiteAdd = Math.trunc(w * w * 40);
        middle[0] = Math.min(255, middle[0] + whiteAdd);
        middle[1] = Math.min(255, middle[1] + whiteAdd);
        middle[2] = Math.min(255, middle[2] + whiteAdd);
      }
      const c = nscale8(middle, middleBright);
      this.mouthLeds[1] = [...c];
      this.mouthLeds[6] = [...c];
    }
    if (scaledAmp > 0.3) {
      const s2 = Math.min(1.0, (scaledAmp - 0.3) / 0.3);
      const c = nscale8(mouth, Math.trunc(s2 * s2 * 160));
      this.mouthLeds[2] = [...c];
      this.mouthLeds[5] = [...c];
    }
    if (scaledAmp > 0.6) {
      const s3 = Math.min(1.0, (scaledAmp - 0.6) / 0.2);
      const c = nscale8(mouth, Math.trunc(s3 * s3 * 140));
      this.mouthLeds[0] = [...c];
      this.mouthLeds[7] = [...c];
    }
    if (scaledAmp > 0.8) {
      const s4 = Math.min(1.0, (scaledAmp - 0.8) / 0.2);
      const c = nscale8(mouth, Math.trunc(s4 * s4 * 200));
      this.mouthLeds[3] = [...c];
      this.mouthLeds[4] = [...c];
    }
  }

  // ------------------------------------------------------------------ flash

  private updateFlash() {
    if (!this.flashActive) return;
    if (this.flashStep < 30) {
      let brightness = 0;
      const pulse = (s: number) => (s < 3 ? s / 3.0 : s < 5 ? 1.0 : 1.0 - (s - 5) / 5.0);
      if (this.flashStep < 10) brightness = pulse(this.flashStep);
      else if (this.flashStep >= 15 && this.flashStep < 25) brightness = pulse(this.flashStep - 15);
      this.fill(this.eyeLeds, nscale8(COLOR_FLASH, brightness * 255));
      this.flashStep++;
      // delay(10) - accounted for in advanceTo()
    } else {
      this.flashActive = false;
      this.flashStep = 0;
      this.setState(State.ENGAGED);
    }
  }

  private triggerFlash() {
    this.flashActive = true;
    this.flashStep = 0;
  }

  private resetSystem() {
    this.mouthAmplitude = 0;
    this.flashActive = false;
    this.flashStep = 0;
    this.setState(State.IDLE);
  }

  private fill(buf: RGB[], c: RGB) {
    for (let i = 0; i < buf.length; i++) buf[i] = [...c];
  }
}
