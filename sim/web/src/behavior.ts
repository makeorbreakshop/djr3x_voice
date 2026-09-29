import * as THREE from 'three';
import { Rig } from './rig';

export type Activity = 'idle' | 'engaged' | 'listening' | 'thinking' | 'speaking' | 'dj';

export interface PerformContext {
  /** Speech amplitude 0..1 as the host sees it (post-AGC). */
  amplitude: number;
  /** World-space point to look at (the camera), or null. */
  lookTarget: THREE.Vector3 | null;
  bpm: number;
  /** Mood gain from the puppeteer's `energy` intent (1 = neutral): scales alive motion and saccade rate. */
  energy?: number;
}

/**
 * Procedural performance as a layered stack (after Disney's gaze-controller subsumption
 * layers and van Breemen's iCat engine), producing *targets only* - the actuation
 * pipeline's jerk-limited followers and servo plant decide how fast anything gets there.
 *
 *   L1 alive     breathing on lift/tilt (0.25 Hz), slow micro-motion >= 2 deg so it
 *                survives servo deadband and pulse quantisation instead of stair-stepping
 *   L2 gaze      saccades with +/-20% timing jitter; the head leads and the rings follow
 *                200-350 ms later; anticipation (an 8% counter-move) before big turns
 *   L3 activity  listening / thinking / speaking / DJ poses
 *   L4 speech    envelope-driven bob (low-passed to ~3 Hz - servos cannot track
 *                syllables) plus accent detection that fires a nod, visor flick and lift
 *                pop on stressed syllables
 *
 * DJ mode reproduces the R-3X Animation Maestro show's choreography (lift bounce and
 * visor flap every beat, hand every 2, elbow and rings every 4) at any tempo.
 *
 * Borrowed from Reachy Mini's conversation app (moves.py):
 *   - listening freeze: on listening start he orients to the listener once and then holds
 *     still - no re-targeting saccades, alive motion damped to 15% - and eases back
 *     (~1 s) when listening stops;
 *   - speaking handoff: while speaking, gaze stays anchored on the listener (small drift,
 *     an occasional short glance aside that comes straight back);
 *   - speech wobble: small additive pan/tilt/lift micro-motion driven by the same speech
 *     amplitude that feeds the mouth LEDs.
 * Authored clips, cues and shows compose above this in show/body.ts.
 */
export class Performer {
  activity: Activity = 'idle';
  private since = 0;

  // gaze
  private gaze = { pan: 0, tilt: 0 };
  private gazeFrom = { pan: 0, tilt: 0 };
  private gazeAt = 0;
  private anticipate = 0;
  private nextSaccade = 0;
  private readonly panHistory: { t: number; v: number }[] = [];

  // speech envelope
  private envFast = 0;
  private envSlow = 0;
  private bob = 0;
  private accentAt = -10;
  private accentGain = 1;

  // listening freeze / speaking handoff / wobble
  private still = 0;
  private anchor = { pan: 0, tilt: 0 };
  private glanceBack = 0;
  private wobblePhase = 0;

  private readonly tmp = new THREE.Vector3();

  constructor(private readonly rig: Rig, private readonly command: (joint: string, value: number) => void) {}

  setActivity(a: Activity, t: number) {
    if (a === this.activity) return;
    // Speaking hands off from listening: keep looking where the listener was.
    if (a === 'speaking') this.anchor = { ...this.gaze };
    this.activity = a;
    this.since = t;
    this.nextSaccade = t;
    this.glanceBack = 0;
  }

  /** 0..1: how frozen the listening hold is right now (for the UI / idle gating). */
  get listeningHold() { return this.still; }

  /** Pan/tilt (deg) that would point the face at `p`, measured in the head_pan parent frame. */
  private aimAt(p: THREE.Vector3): [number, number] {
    const pan = this.rig.get('head_pan').node;
    const tilt = this.rig.get('head_tilt').node;
    const local = pan.parent!.worldToLocal(this.tmp.copy(p));
    const dy = local.y - (pan.position.y + tilt.position.y);
    const yaw = THREE.MathUtils.radToDeg(Math.atan2(local.x, local.z));
    // Rotation about +X tips the face down, so looking up is negative tilt.
    const pitch = -THREE.MathUtils.radToDeg(Math.atan2(dy, Math.hypot(local.x, local.z)));
    return [yaw, pitch];
  }

  /** New gaze target, with anticipation on big moves. */
  private look(t: number, pan: number, tilt: number) {
    const jump = Math.abs(pan - this.gaze.pan);
    this.gazeFrom = { ...this.gaze };
    this.gaze = { pan, tilt };
    this.gazeAt = t;
    this.anticipate = jump > 25 ? -0.08 * (pan - this.gazeFrom.pan) : 0;
  }

  private gazePan(t: number) {
    // 150 ms counter-move, then commit to the target.
    return t - this.gazeAt < 0.15 ? this.gazeFrom.pan + this.anticipate : this.gaze.pan;
  }

  private delayed(t: number, delay: number) {
    const h = this.panHistory;
    for (let i = h.length - 1; i >= 0; i--) if (h[i].t <= t - delay) return h[i].v;
    return h.length ? h[0].v : 0;
  }

  update(t: number, dt: number, ctx: PerformContext) {
    const since = t - this.since;
    const energy = ctx.energy ?? 1;
    const jitter = () => THREE.MathUtils.randFloat(0.8, 1.2) / (0.5 + 0.5 * energy);
    const pose: Record<string, number> = {};
    const look = ctx.lookTarget ? this.aimAt(ctx.lookTarget) : [0, 0];

    // ---------------------------------------------------------------- L4 speech envelope
    const a = ctx.amplitude;
    const kFast = 1 - Math.exp(-dt / (a > this.envFast ? 0.015 : 0.11)); // 15 ms attack, 110 ms release
    this.envFast += (a - this.envFast) * kFast;
    this.envSlow += (this.envFast - this.envSlow) * (1 - Math.exp(-dt / 1.5));
    this.bob += (this.envFast - this.bob) * (1 - Math.exp(-dt * 2 * Math.PI * 3)); // ~3 Hz low-pass
    if (this.activity === 'speaking' && this.envFast > 0.5 && this.envFast - this.envSlow > 0.22 && t - this.accentAt > 0.35) {
      this.accentAt = t;
      this.accentGain = THREE.MathUtils.randFloat(0.7, 1.2);
    }
    const accent = (dur: number) => {
      const u = (t - this.accentAt) / dur;
      return u >= 0 && u <= 1 ? Math.sin(Math.PI * u) * this.accentGain : 0;
    };

    // ---------------------------------------------------------------- L2 gaze + L3 activity
    switch (this.activity) {
      case 'idle':
      case 'engaged': {
        const calm = this.activity === 'idle';
        if (t >= this.nextSaccade) {
          const attend = !calm && Math.random() < 0.6;
          this.look(t,
            attend ? look[0] : THREE.MathUtils.randFloatSpread(calm ? 90 : 60),
            attend ? look[1] : THREE.MathUtils.randFloat(-10, 6));
          this.nextSaccade = t + (calm ? 3.5 : 2.4) * jitter() * THREE.MathUtils.randFloat(0.7, 1.4);
        }
        pose.visor = calm ? 0 : -3;
        break;
      }
      case 'listening':
        // Orient to the listener once, then hold (Reachy's listening freeze).
        if (t >= this.nextSaccade) {
          this.look(t, look[0], look[1] - 4);
          this.nextSaccade = Infinity;
        }
        pose.visor = -8; // brow up: attentive
        pose.head_lift = 6;
        pose.hero_shoulder = 6;
        break;
      case 'thinking':
        if (t >= this.nextSaccade) {
          this.look(t, THREE.MathUtils.randFloat(15, 30) * (Math.random() < 0.5 ? -1 : 1), -14);
          this.nextSaccade = t + 1.1 * jitter();
        }
        pose.visor = 6; // brow down: concentrating
        pose.hero_wrist = Math.sin(since * Math.PI * 1.6) * 25;
        pose.hero_claw_l = pose.hero_claw_r = pose.hero_claw_t = 3 + ((Math.sin(since * Math.PI * 5) + 1) / 2) * 12;
        break;
      case 'speaking': {
        // Handoff: the gaze stays anchored on the listener (the camera when we have one).
        if (ctx.lookTarget) this.anchor = { pan: look[0], tilt: look[1] };
        if (t >= this.nextSaccade) {
          if (this.glanceBack) {
            this.look(t, this.anchor.pan + THREE.MathUtils.randFloatSpread(4), this.anchor.tilt);
            this.glanceBack = 0;
            this.nextSaccade = t + 1.3 * jitter();
          } else if (Math.random() < 0.2) {
            const side = Math.random() < 0.5 ? -1 : 1;
            this.look(t, this.anchor.pan + side * THREE.MathUtils.randFloat(8, 14), this.anchor.tilt + THREE.MathUtils.randFloatSpread(4));
            this.glanceBack = 1;
            this.nextSaccade = t + THREE.MathUtils.randFloat(0.5, 0.9);
          } else {
            this.look(t, this.anchor.pan + THREE.MathUtils.randFloatSpread(4), this.anchor.tilt);
            this.nextSaccade = t + 1.3 * jitter();
          }
        }
        pose.head_lift = 3 + this.bob * 5 + accent(0.3) * 6;
        pose.visor = -4 - this.bob * 4 - accent(0.25) * 9;
        pose.hero_shoulder = 4 + this.bob * 10 + accent(0.5) * 14;
        pose.hero_wrist = Math.sin(t * 1.7) * 30;
        pose.hero_claw_l = pose.hero_claw_r = pose.hero_claw_t = 4 + this.bob * 20;
        pose.throttle_shoulder = Math.sin(t * 1.1) * 12;
        pose.throttle_elbow = -10 + Math.sin(t * 1.4) * 12;
        pose.poker_shoulder = -4 + this.bob * 8;
        break;
      }
      case 'dj': {
        // R-3X Animation show: alternate lift/visor every beat.
        const beat = (t * ctx.bpm) / 60;
        const n = Math.floor(beat);
        const up = n % 2 === 0;
        pose.head_lift = up ? 14 : 3;
        pose.visor = up ? -11 : 0;
        pose.hero_wrist = [60, 0, -60, 0][Math.floor(n / 2) % 4];
        pose.hero_shoulder = [0, 14][Math.floor(n / 4) % 2];
        const bar = Math.floor(n / 8) % 4;
        pose.torso_lower = [0, 18, 0, -18][bar];
        pose.torso_top = [14, 0, -14, 0][bar];
        pose.torso_middle = [0, -12, 0, 12][bar];
        this.gaze = { pan: [20, -20, 0, 10][bar], tilt: up ? -2 : 4 };
        this.gazeAt = -10;
        pose.hero_claw_l = pose.hero_claw_r = pose.hero_claw_t = up ? 22 : 4;
        pose.throttle_shoulder = up ? 14 : -14;
        pose.throttle_wrist = up ? 20 : -20;
        pose.poker_wrist = up ? -18 : 18;
        pose.poker_claw_upper = pose.poker_claw_lower = up ? 4 : 20;
        break;
      }
    }

    // ---------------------------------------------------------------- head leads, body follows
    const pan = this.gazePan(t);
    this.panHistory.push({ t, v: pan });
    while (this.panHistory.length > 2 && this.panHistory[0].t < t - 1) this.panHistory.shift();
    pose.head_pan = pan;
    pose.head_tilt = (pose.head_tilt ?? 0) + this.gaze.tilt + (this.activity === 'speaking' ? this.bob * 5 + accent(0.22) * 5 : 0);
    if (this.activity !== 'dj') {
      pose.torso_top = (pose.torso_top ?? 0) + this.delayed(t, 0.2) * 0.3;
      pose.torso_lower = (pose.torso_lower ?? 0) + this.delayed(t, 0.35) * 0.12;
    }

    // ---------------------------------------------------------------- speech wobble
    // Small, incommensurate sines whose rate and depth follow the speech envelope (the same
    // amplitude that drives the mouth LEDs). Subtle: <= 1.8 deg pan, 1.2 deg tilt, 1 mm lift.
    this.wobblePhase += dt * 2 * Math.PI * (1.6 + 2.2 * this.envFast);
    const w = this.envFast;
    if (w > 0.01) {
      pose.head_pan += w * 1.8 * Math.sin(this.wobblePhase);
      pose.head_tilt += w * 1.2 * Math.sin(1.37 * this.wobblePhase + 1.1);
      pose.head_lift = (pose.head_lift ?? 0) + w * 1.0 * Math.sin(0.71 * this.wobblePhase + 2.3);
    }

    // ---------------------------------------------------------------- L1 alive
    // Damped while the listening freeze holds; eases back over ~1 s when it lets go.
    this.still += ((this.activity === 'listening' ? 1 : 0) - this.still) * (1 - Math.exp(-dt / 0.35));
    const alive = (1 - 0.85 * this.still) * energy;
    const breath = Math.sin(2 * Math.PI * 0.25 * t);
    pose.head_lift = (pose.head_lift ?? 0) + breath * 1.5 * alive;
    pose.head_tilt += Math.sin(2 * Math.PI * 0.25 * t + 1.2) * 1.2 * alive;
    if (this.activity === 'idle' || this.activity === 'engaged') {
      pose.torso_middle = (pose.torso_middle ?? 0) + Math.sin(2 * Math.PI * 0.11 * t) * 3 * alive;
      pose.hero_shoulder = (pose.hero_shoulder ?? 0) + Math.sin(2 * Math.PI * 0.17 * t + 2) * 2.5 * alive;
      pose.throttle_elbow = (pose.throttle_elbow ?? 0) + Math.sin(2 * Math.PI * 0.13 * t + 4) * 2.5 * alive;
    }

    for (const j of this.rig.joints.keys()) this.command(j, pose[j] ?? 0);
  }
}
