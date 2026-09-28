/**
 * The CantinaOS side of the face link, emulated so the sim sends the firmware exactly
 * the bytes the real system would. Ports of:
 *
 * - ElevenLabsService amplitude (elevenlabs_service.py ~L488-530): per-chunk RMS -> dB,
 *   AGC over the last 30 chunks with a 12 dB minimum range, x2 boost.
 * - EyeLightControllerService (eye_light_controller_service.py): mode -> pattern mapping,
 *   interactive-only listening/thinking/speaking, EMA(0.3) amplitude with a 60 Hz,
 *   change-only send, and the "FLASH then M000" at speech end; a 60 Hz control loop that
 *   sends the pattern only when the target changes.
 * - SimpleEyeAdapter (simple_eye_adapter.py): pattern -> "S?\n", and mouth commands
 *   throttled to 10 Hz by DROPPING anything inside the window (not deferring it).
 *
 * Note the consequence of the last point: the M000 sent at speech end goes through the
 * same 10 Hz throttle, so if an amplitude update went out <100 ms earlier the reset is
 * dropped and the firmware keeps the stale amplitude. ENGAGED renders the mouth from
 * amplitude, so after the flash the mouth can stay lit. The sim reproduces that on
 * purpose - see `droppedMouthResets`.
 */

import { RexFaceFirmware } from './firmware';
import { ChestHost } from './chest';

export type SystemMode = 'IDLE' | 'AMBIENT' | 'INTERACTIVE';
export type Pattern = 'idle' | 'engaged' | 'listening' | 'thinking' | 'speaking' | 'flash';

const PATTERN_CMD: Record<Pattern, string> = {
  idle: 'SI', engaged: 'SE', listening: 'SL', thinking: 'ST', speaking: 'SS', flash: 'SF',
};

export interface SerialTap {
  (dir: 'tx' | 'rx', line: string, atMs: number): void;
}

export class CantinaHostEmulator {
  mode: SystemMode = 'IDLE';
  targetPattern: Pattern = 'idle';
  currentPattern: Pattern | null = null;

  // EyeLightControllerService amplitude state
  amplitudeModulation = 0;
  private lastMouthLevel = -1;
  private lastMouthCommandTime = -Infinity;
  private readonly MOUTH_UPDATE_INTERVAL = 16.7; // ms (0.0167 s)

  // SimpleEyeAdapter throttle
  private adapterLastMouthTime = -Infinity;
  private readonly adapterMouthInterval = 100; // ms (10 Hz)

  private lastControlTick = -Infinity;
  droppedMouthResets = 0;

  constructor(private readonly fw: RexFaceFirmware, private readonly tap?: SerialTap) {}

  private send(line: string) {
    this.fw.write(line + '\n');
    this.tap?.('tx', line, this.fw.now);
  }

  // ------------------------------------------------------------------ events in

  setMode(mode: SystemMode) {
    this.mode = mode;
    this.targetPattern = mode === 'IDLE' ? 'idle' : 'engaged';
  }

  private interactive() {
    return this.mode === 'INTERACTIVE';
  }

  listeningStarted() {
    if (this.interactive()) this.targetPattern = 'listening';
  }

  /** VOICE_LISTENING_STOPPED / processing started / mouse recording stopped. */
  listeningStopped() {
    if (this.interactive()) this.targetPattern = 'thinking';
  }

  /** LLM_RESPONSE_CHUNK while thinking. */
  llmChunk() {
    if (this.interactive() && this.targetPattern === 'thinking') this.targetPattern = 'speaking';
  }

  speechStarted() {
    if (this.interactive()) this.targetPattern = 'speaking';
  }

  /** SPEECH_SYNTHESIS_AMPLITUDE, amplitude 0..1. */
  amplitude(a: number) {
    if (this.targetPattern !== 'speaking') return;
    this.amplitudeModulation = 0.3 * a + 0.7 * this.amplitudeModulation;
    const now = this.fw.now;
    if (now - this.lastMouthCommandTime >= this.MOUTH_UPDATE_INTERVAL) {
      const level = Math.trunc(this.amplitudeModulation * 255);
      if (level !== this.lastMouthLevel) {
        this.adapterSetMouth(level);
        this.lastMouthLevel = level;
        this.lastMouthCommandTime = now;
      }
    }
  }

  speechEnded() {
    if (!this.interactive()) return;
    this.targetPattern = 'flash';
    this.amplitudeModulation = 0;
    this.lastMouthLevel = -1;
    if (!this.adapterSetMouth(0)) this.droppedMouthResets++;
  }

  /** Returns false when the adapter's 10 Hz throttle swallowed the command. */
  private adapterSetMouth(level: number): boolean {
    const now = this.fw.now;
    if (now - this.adapterLastMouthTime < this.adapterMouthInterval) return false;
    this.adapterLastMouthTime = now;
    const a = Math.max(0, Math.min(255, level));
    this.send(`M${String(a).padStart(3, '0')}`);
    return true;
  }

  /** 60 Hz control loop: send the pattern when the target changed. */
  tick() {
    const now = this.fw.now;
    if (now - this.lastControlTick < 1000 / 60) return;
    this.lastControlTick = now;
    if (this.targetPattern !== this.currentPattern) {
      this.send(PATTERN_CMD[this.targetPattern]);
      this.currentPattern = this.targetPattern;
    }
  }
}

/**
 * ElevenLabsService's per-chunk amplitude: RMS -> dBFS -> AGC-normalised 0..1.
 * Feed it int16-scale RMS values, one per ~16.7 ms chunk.
 */
export class TtsAmplitudeAgc {
  private recent: number[] = [];
  private readonly window = 30;
  private readonly minRangeDb = 12;

  next(rmsInt16: number): number {
    if (rmsInt16 <= 0) return 0;
    const db = 20 * Math.log10(rmsInt16 / 32768);
    this.recent.push(db);
    if (this.recent.length > this.window) this.recent.shift();
    let a: number;
    if (this.recent.length >= 10) {
      const lo = Math.min(...this.recent);
      const hi = Math.max(...this.recent);
      const range = Math.max(this.minRangeDb, hi - lo);
      a = Math.max(0, Math.min(1, (db - lo) / range));
    } else {
      a = Math.max(0, Math.min(1, (db + 50) / 40));
    }
    return Math.min(1, a * 2);
  }

  reset() {
    this.recent = [];
  }
}

/**
 * Both boards from one event stream, as CantinaOS does (EyeLightControllerService and
 * ChestLightControllerService subscribe to the same events). Call sites stay unchanged.
 */
export class DualHost {
  constructor(readonly face: CantinaHostEmulator, readonly chest: ChestHost) {}
  get mode() { return this.face.mode; }
  get droppedMouthResets() { return this.face.droppedMouthResets; }
  setMode(m: SystemMode) { this.face.setMode(m); this.chest.setMode(m); }
  listeningStarted() { this.face.listeningStarted(); this.chest.listeningStarted(); }
  listeningStopped() { this.face.listeningStopped(); this.chest.listeningStopped(); }
  llmChunk() { this.face.llmChunk(); this.chest.llmChunk(); }
  speechStarted() { this.face.speechStarted(); this.chest.speechStarted(); }
  amplitude(a: number) { this.face.amplitude(a); this.chest.amplitudeIn(a); }
  speechEnded() { this.face.speechEnded(); this.chest.speechEnded(); }
  tick(nowMs: number) { this.face.tick(); this.chest.tick(nowMs); }
}
