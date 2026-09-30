/**
 * The embedded Rust performer (`r3x-performer-core` via `r3x-performer-wasm`, built into
 * src/wasm by `npm run build:wasm`) for standalone mode: the same conductor the runtime
 * runs, ticked locally. JSON in, JSON out; the shapes below mirror
 * `r3x_performer_core::performer::{Command, Frames, Out}`.
 */
import init, { WasmPerformer } from './wasm/r3x_performer';
import type { PadFrame } from './generated/PadFrame';
import profileJson from '../../../profiles/r3x/robot.json?raw';
import { SHOW_FILES } from './show/loader';
import type { RGB } from './leds';

export const PROFILE_JSON: string = profileJson;

export type RunLayer = 'background' | 'gesture' | 'show';
export type Activity = 'idle' | 'engaged' | 'listening' | 'thinking' | 'speaking' | 'dj';
export type SystemMode = 'IDLE' | 'AMBIENT' | 'INTERACTIVE';
export type PuppetMode = 'idle' | 'engaged' | 'dj';

/** Puppeteer command space (`show::puppeteer::CONTINUOUS`, in order). */
export const INTENTS = ['gaze_yaw', 'gaze_pitch', 'lift', 'body_yaw', 'lean', 'visor', 'arm_raise', 'energy'] as const;
export const PUPPET_MODES: PuppetMode[] = ['idle', 'engaged', 'dj'];

export type PerfCmd =
  | { cmd: 'perform'; id: string; source?: string; params?: { intensity?: number; speed?: number }; layer?: RunLayer }
  | { cmd: 'stop'; id?: string; layer?: RunLayer; all?: boolean }
  | { cmd: 'freeze'; on: boolean }
  | { cmd: 'puppet'; intent: string; value: number }
  | { cmd: 'puppet_release' }
  | { cmd: 'puppet_mode'; mode: PuppetMode }
  | { cmd: 'emote'; slot: number }
  | { cmd: 'pad'; axes: number[]; buttons: [boolean, number][] }
  | { cmd: 'mode'; mode: SystemMode }
  | { cmd: 'listening_started' | 'listening_stopped' | 'llm_chunk' | 'speech_ended' | 'puppet_release' | 'jog_release' }
  | { cmd: 'speech_started'; timings?: number[]; tags?: [number, string][] }
  | { cmd: 'amplitude'; value: number }
  | { cmd: 'music'; playing: boolean }
  | { cmd: 'dj'; on: boolean }
  | { cmd: 'tempo'; bpm: number }
  | { cmd: 'look'; pan_tilt: [number, number] | null }
  | { cmd: 'home'; joints?: string[] }
  | { cmd: 'background'; activity: Activity; id: string | null }
  | { cmd: 'service_status'; service: string; status: string; detail?: string; latched?: boolean }
  | { cmd: 'autonomy'; on: boolean }
  | { cmd: 'jog'; joint: string; value: number | null }
  | { cmd: 'eyes'; pattern: string; duration?: number }
  | { cmd: 'alive'; breathing: boolean; saccades: boolean; gaze_wander: boolean; speech_bob: boolean };

export interface RunInfo {
  run_id: string;
  id: string;
  kind: 'clip' | 'cue' | 'sequence';
  source: string;
  layer: RunLayer;
  owns?: string[] | null;
  started_at: number;
}

export interface PerfFrames {
  t: number;
  targets: Record<string, number>;
  joints: Record<string, number>;
  eyes: RGB[];
  mouth: RGB[];
  chest: RGB[];
  /** Linear flux per stage-light group (stagelights.ts GROUPS order). */
  stage: RGB[];
  servo: { frame: number; t: number; targets: number[] };
  /** The gamepad and what the puppeteer made of it, while one is attached. */
  pad?: PadFrame | null;
}

export type LightsAction = { do: 'lights'; cue?: string; mode?: string; fade?: number; hold?: number; rig?: string };

export type PerfOut =
  | { type: 'started'; run: RunInfo }
  | { type: 'ended'; run: RunInfo; reason: 'done' | 'interrupted' | 'rejected' }
  | { type: 'action'; run_id: string; at: number; action: { do: string } }
  | { type: 'sfx'; id: string }
  | { type: 'speak'; text: string }
  | { type: 'duck' | 'unduck' }
  | { type: 'stage_lights'; action: LightsAction }
  | { type: 'face_line' | 'chest_line'; line: string }
  | { type: 'servo_goal'; joint: string; target: number }
  | { type: 'freeze'; on: boolean };

export interface CatalogItem {
  id: string;
  kind: 'clip' | 'cue' | 'sequence';
  tier: string;
  description: string;
  tags: string[];
  title?: string;
  clock?: 'time' | 'beat';
  loop?: boolean;
  layer?: RunLayer;
  duration?: number;
  requires?: string;
}

/** Typed wrapper; one per page. */
export class Performer {
  private constructor(private readonly w: WasmPerformer) {}

  static async create(seed: number): Promise<Performer> {
    await init();
    return new Performer(new WasmPerformer(PROFILE_JSON, JSON.stringify(SHOW_FILES), seed >>> 0));
  }

  command(c: PerfCmd) {
    try {
      this.w.command(JSON.stringify(c));
    } catch (e) {
      console.warn('performer command rejected', c, e);
    }
  }

  tick(t: number): PerfFrames {
    return JSON.parse(this.w.tick(t)) as PerfFrames;
  }

  events(): PerfOut[] {
    return JSON.parse(this.w.events()) as PerfOut[];
  }

  running(): RunInfo[] {
    return JSON.parse(this.w.running()) as RunInfo[];
  }

  catalog(): { items: CatalogItem[]; idle_after_s: number | null; errors: string[] } {
    return JSON.parse(this.w.catalog());
  }

  /** Studio: an unsaved clip document on the show layer from clip time `at`; `hold` pins it (scrub). Throws on an invalid clip. */
  preview(doc: unknown, at: number, hold: boolean) { this.w.preview(JSON.stringify(doc), at, hold); }
  previewStop() { this.w.previewStop(); }

  takeStart() { this.w.takeStart(new Date().toISOString()); }
  takeStop() { this.w.takeStop(); }
  takeInfo(): { recording: boolean; seconds: number; samples: number } { return JSON.parse(this.w.takeInfo()); }
  takeJsonl() { return this.w.takeJsonl(); }
}
