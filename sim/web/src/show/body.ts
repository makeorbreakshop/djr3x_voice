/**
 * The body compositor: the "animation engine" between behaviour and the actuation pipeline
 * (after Disney's BD-X/Olaf engine and the plan's layer table, docs/plans/
 * dj-r3x-embodied-behavior.md section 2.1). It takes the procedural pose (Performer) and
 * stacks, bottom to top:
 *
 *   0 procedural   alive + gaze + activity + speech (behavior.ts), always on
 *   1 background   an authored loop per activity (a looping show/ sequence)
 *   2 gesture      triggered clips and cues
 *   3 show         show elements (sequences)
 *   4 puppeteer    additive offsets on high-level intents (puppeteer.ts), not raw joints
 *   5 freeze       motion stop: holds the setpoints; releases over 0.5 s
 *
 * Per joint, a track is `additive` (adds to everything below it) or `override` (replaces
 * it by weight). Override weights ramp in and out over the track's blend - by joint class
 * by default (visor 0.1 s, head 0.2 s, body 0.35 s) - and `intensity` scales an override's
 * offset from the pose it started over. An interrupted clip freezes where it is and fades
 * its weight out over the same blend: it never cuts.
 *
 * A run that `owns` joints (a sequence) suppresses everything below it on those joints -
 * it holds them at the pose it started over, blending in and out like an override - while
 * the joints it does not own stay live (gaze, breathing). Its override tracks are masked
 * to the owned joints; additive tracks still apply anywhere.
 *
 * The output is only a target: every joint still goes through the jerk-limited follower,
 * the pulse output stage and the servo plant.
 */
import { evalTrack, minjerk } from './curve';
import { defaultBlend, type Clip } from './types';
import type { RunLayer } from './player';

export type Pose = Record<string, number>;

const ramp = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : minjerk(x));

interface Play {
  runId: string;
  clip: Clip;
  intensity: number;
  speed: number;
  layer: RunLayer;
  mask: Set<string> | null;
  t0: number;
  stopAt: number | null;
  base: Map<string, number> | null;
  /** Longest fade among the clip's tracks, for the tail. */
  tail: number;
}

interface Own {
  runId: string;
  layer: RunLayer;
  joints: string[];
  t0: number;
  stopAt: number | null;
  hold: Map<string, number> | null;
}

export interface PlayRequest {
  runId: string;
  clip: Clip;
  intensity?: number;
  speed?: number;
  layer: RunLayer;
  /** Joints the run owns; override tracks outside it are masked. */
  owns?: string[] | null;
  /** Clock time the clip starts (s). */
  t0: number;
}

/** Offsets from the puppeteer, applied above the show layer. */
export interface PuppetOffsets { apply(pose: Pose): void }

export class BodyCompositor {
  private plays: Play[] = [];
  private owns: Own[] = [];
  puppet: PuppetOffsets | null = null;

  // freeze
  private frozen = false;
  private freezeHold: Pose | null = null;
  private freezeRelease = -Infinity;
  static readonly FREEZE_RELEASE_S = 0.5;
  private last: Pose = {};

  play(req: PlayRequest) {
    // Longest fade any track needs (overrides use their blend; additive tracks fade over the
    // joint-class blend when interrupted).
    const tail = Math.max(0, ...Object.entries(req.clip.tracks).map(([j, tr]) => (tr.mode === 'override' ? tr.blend ?? defaultBlend(j) : defaultBlend(j))));
    this.plays.push({
      runId: req.runId, clip: req.clip, intensity: req.intensity ?? 1, speed: req.speed ?? 1, layer: req.layer,
      mask: req.owns ? new Set(req.owns) : null, t0: req.t0, stopAt: null, base: null, tail,
    });
  }

  /** A run takes ownership of joints (sequence `owns`). */
  own(runId: string, layer: RunLayer, joints: string[], t0: number) {
    if (!joints.length || this.owns.some((o) => o.runId === runId)) return;
    this.owns.push({ runId, layer, joints, t0, stopAt: null, hold: null });
  }

  /** Blend out everything a run is doing (interrupt, stop, end). Never a cut. */
  release(runId: string, now: number) {
    for (const p of this.plays) if (p.runId === runId && p.stopAt === null) p.stopAt = now;
    for (const o of this.owns) if (o.runId === runId && o.stopAt === null) o.stopAt = now;
  }

  releaseLayer(layer: RunLayer, now: number) {
    for (const p of this.plays) if (p.layer === layer && p.stopAt === null) p.stopAt = now;
    for (const o of this.owns) if (o.layer === layer && o.stopAt === null) o.stopAt = now;
  }

  releaseAll(now: number) {
    for (const l of ['background', 'gesture', 'show'] as const) this.releaseLayer(l, now);
  }

  /**
   * motion.freeze: on holds the setpoints - `hold` if given (the followers' current
   * positions, so motion stops where the servos are rather than where they were headed),
   * else the last output; off blends back over 0.5 s.
   */
  freeze(on: boolean, now: number, hold?: Pose) {
    if (on === this.frozen) return;
    this.frozen = on;
    if (on) {
      this.releaseAll(now);
      this.freezeHold = { ...this.last, ...hold };
    } else {
      this.freezeRelease = now;
    }
  }
  get isFrozen() { return this.frozen; }

  /** What is on each layer now (for the UI): clip ids that are still in. */
  active(layer: RunLayer): string[] {
    return this.plays.filter((p) => p.layer === layer && p.stopAt === null).map((p) => p.clip.id);
  }

  /** Compose the layers over the procedural pose (mutated and returned). */
  apply(pose: Pose, t: number): Pose {
    for (const layer of ['background', 'gesture', 'show'] as const) {
      for (const o of this.owns) if (o.layer === layer) this.applyOwn(o, pose, t);
      for (const p of this.plays) if (p.layer === layer && t >= p.t0) this.applyPlay(p, pose, t);
    }
    this.puppet?.apply(pose);
    this.applyFreeze(pose, t);
    this.last = { ...pose };
    // Retire what has fully blended out.
    this.plays = this.plays.filter((p) => {
      const end = p.stopAt ?? p.t0 + p.clip.duration / p.speed;
      return t < end + p.tail + 0.05;
    });
    this.owns = this.owns.filter((o) => o.stopAt === null || t < o.stopAt + 0.4);
    return pose;
  }

  private applyOwn(o: Own, pose: Pose, t: number) {
    if (!o.hold) o.hold = new Map(o.joints.map((j) => [j, pose[j] ?? 0]));
    for (const j of o.joints) {
      const b = defaultBlend(j);
      const w = ramp((t - o.t0) / b) * (o.stopAt === null ? 1 : 1 - ramp((t - o.stopAt) / b));
      const v = pose[j] ?? 0;
      pose[j] = v + (o.hold.get(j)! - v) * w;
    }
  }

  private applyPlay(p: Play, pose: Pose, t: number) {
    const tEval = p.stopAt === null ? t : Math.min(t, p.stopAt);
    const u = (tEval - p.t0) * p.speed;
    const end = p.t0 + p.clip.duration / p.speed;
    if (!p.base) {
      p.base = new Map();
      for (const [j, tr] of Object.entries(p.clip.tracks)) if (tr.mode === 'override') p.base.set(j, pose[j] ?? 0);
    }
    for (const [j, tr] of Object.entries(p.clip.tracks)) {
      const value = evalTrack(tr, u);
      if (tr.mode === 'additive') {
        const wStop = p.stopAt === null ? 1 : 1 - ramp((t - p.stopAt) / defaultBlend(j));
        pose[j] = (pose[j] ?? 0) + value * p.intensity * wStop;
        continue;
      }
      if (p.mask && !p.mask.has(j)) continue;
      const b = tr.blend ?? defaultBlend(j);
      const base = p.base.get(j) ?? 0;
      const target = base + p.intensity * (value - base);
      const wIn = b > 0 ? ramp((t - p.t0) / b) : 1;
      const wOut = t > end ? (b > 0 ? 1 - ramp((t - end) / b) : 0) : 1;
      const wStop = p.stopAt === null ? 1 : b > 0 ? 1 - ramp((t - p.stopAt) / b) : 0;
      const w = wIn * wOut * wStop;
      const v = pose[j] ?? 0;
      pose[j] = v + (target - v) * w;
    }
  }

  private applyFreeze(pose: Pose, t: number) {
    if (!this.freezeHold) return;
    const w = this.frozen ? 1 : 1 - ramp((t - this.freezeRelease) / BodyCompositor.FREEZE_RELEASE_S);
    if (w <= 0) {
      this.freezeHold = null;
      return;
    }
    for (const [j, h] of Object.entries(this.freezeHold)) {
      const v = pose[j] ?? 0;
      pose[j] = v + (h - v) * w;
    }
  }
}
