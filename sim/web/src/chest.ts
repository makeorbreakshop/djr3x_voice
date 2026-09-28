/**
 * Middle-ring logic panel lights: the sim side of the planned Arduino Nano + addressable
 * LED controller ("For middle ring lights" in the R-3X Animation parts list). The board's
 * firmware is cantina_os/arduino/rex_chest_v1 - a port of this file; keep the two in step.
 * Same serial words as the face controller, so one command stream can drive both.
 *
 * Layout (found by the model build, rig.json "chest_lights"): three front panels, each with
 * a vertical column of 8 LED holes and 3 square windows over diffuser blocks = 33 pixels.
 * Pixel order = strip order: panel 1 dots bottom->top, panel 1 windows, panel 2 ...
 *
 * Serial (newline-terminated; see the sketch header for the full protocol):
 *   SI SE SL ST SS   idle / engaged / listening / thinking / speaking
 *   SF               green "done" sparkle alongside the eyes' flash
 *   Mnnn             speech amplitude 0-255 (VU meter on the dots, window pulse)
 *   Bnnn             tempo in BPM, 000 = music stopped (beat chase)
 *   Xn               0 normal, 1 boot sweep, 2 sleep, 3 fault alarm
 *   Hxxx             health mask: a 0 bit blinks that window red
 *
 * ChestHost (below) is the port of CantinaOS ChestLightControllerService that produces
 * those commands offline; live, the sim feeds the real service's commands straight in.
 */

import * as THREE from 'three';
import type { RGB } from './firmware';

export interface ChestLightSpec {
  kind: 'dot' | 'window';
  pos: [number, number, number];
  normal: [number, number, number];
  w: number;
  h: number;
  panel: string;
}

type Mode = 'I' | 'E' | 'L' | 'T' | 'S';

// Droid-panel palette: dots are indicator LEDs, windows are backlit readouts.
const DOT_COLORS: RGB[] = [[255, 20, 0], [255, 110, 0], [40, 255, 30], [255, 20, 0], [0, 120, 255]];
// Windows glow cyan / white / teal / pale blue on the real droid (reference photos).
const WIN_COLORS: RGB[] = [[0, 210, 255], [225, 240, 255], [0, 175, 150], [110, 190, 255]];
const OFF: RGB = [0, 0, 0];

function scale(c: RGB, k: number): RGB {
  const q = Math.max(0, Math.min(1, k));
  return [Math.round(c[0] * q), Math.round(c[1] * q), Math.round(c[2] * q)];
}

export class ChestFirmware {
  readonly pixels: RGB[];
  mode: Mode = 'I';
  amplitude = 0;
  bpm = 0;
  now = 0;
  sysState = 1; // boot sweep until told otherwise, as on the board
  health = 0x1ff;
  private sparkleUntil = 0;
  private bootStart = 0;
  private readonly panels: { dots: number[]; windows: number[] }[];
  private readonly dotState: { on: boolean; color: number; next: number }[];
  private rx = '';
  private rand: () => number;

  constructor(readonly specs: ChestLightSpec[], random: () => number = Math.random) {
    this.rand = random;
    this.pixels = specs.map(() => [0, 0, 0] as RGB);
    // Group by panel, ordered around the ring; dots bottom -> top.
    const angle = (s: ChestLightSpec) => Math.atan2(s.pos[0], s.pos[2]);
    // Panels are ~31 deg apart; a panel's own openings span < 12 deg. Cluster by gaps in
    // angle (rounding to a grid split a panel that straddled a grid line).
    const order = specs.map((_, i) => i).filter((i) => specs[i].panel === 'MS_P_1_Full')
      .sort((a, b) => angle(specs[a]) - angle(specs[b]));
    const groups: number[][] = [];
    let prev = -Infinity;
    for (const i of order) {
      const deg = THREE.MathUtils.radToDeg(angle(specs[i]));
      if (deg - prev > 15) groups.push([]);
      groups[groups.length - 1].push(i);
      prev = deg;
    }
    this.panels = groups.map((idx) => ({
      dots: idx.filter((i) => specs[i].kind === 'dot').sort((a, b) => specs[a].pos[1] - specs[b].pos[1]),
      windows: idx.filter((i) => specs[i].kind === 'window'),
    }));
    this.dotState = specs.map(() => ({ on: this.rand() < 0.5, color: Math.floor(this.rand() * DOT_COLORS.length), next: 0 }));
  }

  write(data: string) {
    for (const c of data) {
      if (c === '\n' || c === '\r') {
        this.command(this.rx.trim());
        this.rx = '';
      } else {
        this.rx += c;
      }
    }
  }

  private command(cmd: string) {
    if (/^S[IELTS]$/.test(cmd)) this.mode = cmd[1] as Mode;
    else if (cmd === 'SF') this.sparkleUntil = this.now + 350;
    else if (/^M\d{3}$/.test(cmd)) this.amplitude = Math.min(255, parseInt(cmd.slice(1), 10));
    else if (/^B\d{3}$/.test(cmd)) this.bpm = parseInt(cmd.slice(1), 10);
    else if (/^X[0-3]$/.test(cmd)) {
      const s = Number(cmd[1]);
      if (s === 1 && this.sysState !== 1) this.bootStart = this.now;
      this.sysState = s;
    } else if (/^H[0-9A-Fa-f]{3}$/.test(cmd)) this.health = parseInt(cmd.slice(1), 16) & 0x1ff;
    else if (cmd === 'R') {
      this.mode = 'I';
      this.amplitude = 0;
      this.bpm = 0;
      this.sysState = 0;
      this.health = 0x1ff;
    }
  }

  /** Whole-chest states that override the interaction patterns (boot, fault, sleep). */
  private systemState(ms: number): boolean {
    const t = ms / 1000;
    if (this.sysState === 1 && ms - this.bootStart > 60000) this.sysState = 0;
    if (this.sysState === 1) {
      const cycle = (((ms - this.bootStart) / 2400) % 1) * this.panels.length * 1.25;
      this.panels.forEach((p, pi) => {
        const f = Math.min(1.25, Math.max(0, cycle - pi));
        p.dots.forEach((i, k) => (this.pixels[i] = f * p.dots.length > k ? [0, 120, 255] : OFF));
        p.windows.forEach((i, k) => (this.pixels[i] = f >= 1 ? scale(WIN_COLORS[(k + pi * 2) % WIN_COLORS.length], 0.6) : OFF));
      });
      return true;
    }
    if (this.sysState === 3) {
      const pulse = 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * Math.PI * 2));
      const pos = Math.floor(t * 6) % 8;
      this.panels.forEach((p) => {
        p.dots.forEach((i, k) => (this.pixels[i] = k === pos ? scale([255, 20, 0], 0.6) : OFF));
        p.windows.forEach((i) => (this.pixels[i] = scale([255, 20, 0], pulse)));
      });
      return true;
    }
    if (this.sysState === 2) {
      const b = 0.03 + 0.05 * (0.5 + 0.5 * Math.sin(t * 0.8));
      this.panels.forEach((p, pi) => {
        p.dots.forEach((i) => (this.pixels[i] = OFF));
        p.windows.forEach((i, k) => (this.pixels[i] = scale(WIN_COLORS[(k + pi * 2) % WIN_COLORS.length], b)));
      });
      if (Math.floor(ms / 1000) % 3 === 0 && this.panels[0]) this.pixels[this.panels[0].dots[0]] = scale([40, 255, 30], 0.4);
      return true;
    }
    return false;
  }

  /** Advance to `ms` and recompute the frame (the Nano would do this at ~50 Hz). */
  update(ms: number) {
    this.now = ms;
    if (this.systemState(ms)) return;
    const t = ms / 1000;
    const amp = Math.sqrt(this.amplitude / 255);
    const beat = this.bpm > 0 ? (t * this.bpm) / 60 : 0;
    const beatPhase = beat - Math.floor(beat);

    this.panels.forEach((p, pi) => {
      // ---- dots
      p.dots.forEach((i, k) => {
        const st = this.dotState[i];
        let c: RGB = OFF;
        if (this.bpm > 0) {
          // chase: one lit row sweeps up each panel per beat, panels offset
          const row = Math.floor(((beat + pi / 3) % 1) * p.dots.length);
          c = k === row || k === row - 1 ? DOT_COLORS[(k + pi) % DOT_COLORS.length] : scale(DOT_COLORS[0], 0.06);
        } else if (this.mode === 'S') {
          // VU meter: level from amplitude, green -> amber -> red
          const lit = Math.round(amp * p.dots.length);
          c = k < lit ? (k < 4 ? [40, 255, 30] : k < 6 ? [255, 110, 0] : [255, 20, 0]) : OFF;
        } else if (this.mode === 'T') {
          const pos = Math.floor(t * 18) % (p.dots.length * this.panels.length);
          c = pos === pi * p.dots.length + k ? [0, 200, 255] : scale(DOT_COLORS[st.color], st.on ? 0.15 : 0);
        } else if (this.mode === 'L') {
          const row = Math.floor((t * 1.6) % 1 * (p.dots.length + 2));
          c = k <= row ? scale([0, 120, 255], 0.35 + 0.65 * (k === row ? 1 : 0.4)) : OFF;
        } else {
          // idle / engaged: indicator twinkle
          if (ms >= st.next) {
            st.on = this.rand() < (this.mode === 'E' ? 0.6 : 0.45);
            if (this.rand() < 0.2) st.color = Math.floor(this.rand() * DOT_COLORS.length);
            st.next = ms + 150 + this.rand() * (this.mode === 'E' ? 500 : 1100);
          }
          c = st.on ? DOT_COLORS[st.color] : OFF;
        }
        this.pixels[i] = c;
      });
      // ---- windows
      p.windows.forEach((i, k) => {
        const base = WIN_COLORS[(k + pi * 2) % WIN_COLORS.length];
        let level: number;
        if (this.bpm > 0) level = 0.35 + 0.65 * Math.exp(-beatPhase * 6) * ((k + pi) % 2 === Math.floor(beat) % 2 ? 1 : 0.4);
        else if (this.mode === 'S') level = 0.35 + 0.65 * amp;
        else if (this.mode === 'T') level = 0.3 + 0.3 * Math.sin(t * 8 + i);
        else level = 0.45 + 0.25 * Math.sin(t * (0.6 + 0.17 * k) + i * 1.7);
        // A subsystem that is down blinks its window red, whatever the pattern.
        const bit = pi * p.windows.length + k;
        this.pixels[i] = !(this.health & (1 << bit)) ? (Math.floor(ms / 250) % 2 ? scale([255, 20, 0], 0.9) : OFF) : scale(base, level);
      });
    });
    if (ms < this.sparkleUntil) {
      for (const p of this.panels) for (const i of p.dots) if (this.rand() < 0.55) this.pixels[i] = [40, 255, 30];
    }
  }
}

/** Lights behind the panel openings, posed in the middle ring's frame. */
export class ChestLights {
  private readonly mats: THREE.MeshBasicMaterial[] = [];
  private readonly c = new THREE.Color();

  constructor(parent: THREE.Object3D, specs: ChestLightSpec[], private readonly gain = 3.5) {
    parent.updateWorldMatrix(true, false);
    const inv = new THREE.Matrix4().copy(parent.matrixWorld).invert();
    const z = new THREE.Vector3(0, 0, 1);
    for (const s of specs) {
      const geo = s.kind === 'dot'
        ? new THREE.CircleGeometry(0.0016, 12)
        : new THREE.PlaneGeometry(s.w * 0.95, s.h * 0.95);
      const mat = new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false });
      const m = new THREE.Mesh(geo, mat);
      m.position.set(...s.pos).applyMatrix4(inv);
      m.quaternion.setFromUnitVectors(z, new THREE.Vector3(...s.normal).normalize());
      parent.add(m);
      this.mats.push(mat);
    }
  }

  update(pixels: RGB[]) {
    pixels.forEach((p, i) => {
      // Addressable LEDs at the same global brightness as the face (128/255).
      this.c.setRGB((p[0] / 255) * 0.5, (p[1] / 255) * 0.5, (p[2] / 255) * 0.5, THREE.SRGBColorSpace);
      this.mats[i].color.copy(this.c).multiplyScalar(this.gain);
    });
  }
}


// ------------------------------------------------------------------ host side (offline)

/** Panel-major window order - same table as ChestLightControllerService.WINDOW_SUBSYSTEMS. */
export const WINDOW_SUBSYSTEMS: [label: string, services: string[]][] = [
  ['mic / speech-to-text', ['DeepgramDirectMicService']],
  ['LLM', ['ClaudeService']],
  ['text-to-speech', ['ElevenLabsService']],
  ['intent routing', ['IntentRouterService', 'JevIntentService']],
  ['music', ['MusicControllerService']],
  ['memory', ['MemoryService']],
  ['vision', ['VisionService']],
  ['face LEDs', ['EyeLightControllerService']],
  ['show control', ['BrainService', 'TimelineExecutorService']],
];
const CRITICAL = ['DeepgramDirectMicService', 'ClaudeService'];
const UNHEALTHY = new Set(['error', 'degraded']);

export function healthMask(statuses: Record<string, string>): number {
  let mask = 0;
  WINDOW_SUBSYSTEMS.forEach(([, services], i) => {
    if (!services.some((s) => UNHEALTHY.has(statuses[s]))) mask |= 1 << i;
  });
  return mask;
}

/**
 * Port of CantinaOS ChestLightControllerService: turns bus events into chest commands.
 * Used offline; live, the real service's CHEST_COMMAND stream drives the sim instead.
 */
export class ChestHost {
  mode = 'STARTUP';
  target = 'SI';
  amplitude = 0;
  bpm = 0;
  djOn = false;
  sleeping = false;
  booting = true;
  /** Runtime errors count for this long unless repeated (services never report recovery). */
  faultHoldMs = 60000;
  private readonly reports: Record<string, { status: string; at: number; latched: boolean }> = {};
  private nowMs = 0;
  /** Set while the live link is delivering the real service's commands. */
  muted = false;
  private bootedAt = -1;
  private sparkle = false;
  private lastAmp = -1;
  private readonly sent: Record<string, string> = {};

  constructor(private readonly out: (cmd: string) => void, readonly defaultBpm = 120) {}

  private send(cmd: string) {
    if (!this.muted) this.out(cmd);
  }

  private sendIfChanged(key: string, cmd: string) {
    if (this.sent[key] !== cmd) {
      this.sent[key] = cmd;
      this.send(cmd);
    }
  }

  /** Re-send everything on the next tick (e.g. after live control is released). */
  resync() {
    for (const k of Object.keys(this.sent)) delete this.sent[k];
    this.lastAmp = -1;
  }

  boot(nowMs: number) {
    this.booting = true;
    this.bootedAt = nowMs;
  }

  private interactive() {
    return this.mode === 'INTERACTIVE';
  }

  setMode(m: string) {
    this.mode = m;
    if (m !== 'STARTUP' && this.bootedAt < 0) this.booting = false;
    this.target = m === 'IDLE' || m === 'STARTUP' ? 'SI' : 'SE';
  }

  listeningStarted() { if (this.interactive()) this.target = 'SL'; }
  listeningStopped() { if (this.interactive()) this.target = 'ST'; }
  llmChunk() { if (this.interactive() && this.target === 'ST') this.target = 'SS'; }

  speechStarted() {
    if (!this.interactive()) return;
    this.amplitude = 0;
    this.lastAmp = -1;
    this.target = 'SS';
  }

  amplitudeIn(a: number) {
    if (this.target === 'SS') this.amplitude = 0.3 * a + 0.7 * this.amplitude;
  }

  speechEnded() {
    if (!this.interactive() || this.target !== 'SS') return;
    this.amplitude = 0;
    this.lastAmp = 0;
    this.send('M000');
    this.sparkle = true;
    this.target = 'SE';
  }

  music(playing: boolean, bpm = this.defaultBpm) { this.bpm = playing ? bpm : this.djOn ? this.defaultBpm : 0; }
  dj(on: boolean, bpm = this.defaultBpm) { this.djOn = on; this.bpm = on ? bpm : 0; }
  /** latched: a failure to start/initialise, or an offline toggle - holds until cleared. */
  serviceStatus(service: string, status: string, latched = true) {
    this.reports[service] = { status: status.toLowerCase(), at: this.nowMs, latched };
  }

  get statuses(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [svc, r] of Object.entries(this.reports)) {
      const expired = UNHEALTHY.has(r.status) && !r.latched && this.nowMs - r.at > this.faultHoldMs;
      out[svc] = expired ? 'running' : r.status;
    }
    return out;
  }

  systemState(): string {
    if (this.booting) return 'X1';
    const statuses = this.statuses;
    if (CRITICAL.some((s) => statuses[s] === 'error')) return 'X3';
    if (this.sleeping) return 'X2';
    return 'X0';
  }

  tick(nowMs: number) {
    this.nowMs = nowMs;
    if (this.booting && this.bootedAt >= 0 && nowMs - this.bootedAt > 3000) {
      this.booting = false;
      this.bootedAt = -1;
    }
    this.sendIfChanged('X', this.systemState());
    this.sendIfChanged('H', `H${healthMask(this.statuses).toString(16).toUpperCase().padStart(3, '0')}`);
    if (this.sparkle) {
      this.sparkle = false;
      this.send('SF');
    }
    this.sendIfChanged('S', this.target);
    this.sendIfChanged('B', `B${String(Math.max(0, Math.min(999, Math.round(this.bpm)))).padStart(3, '0')}`);
    if (this.target === 'SS') {
      const level = Math.trunc(Math.max(0, Math.min(1, this.amplitude)) * 255);
      if (level !== this.lastAmp) {
        this.lastAmp = level;
        this.send(`M${String(level).padStart(3, '0')}`);
      }
    }
  }
}
