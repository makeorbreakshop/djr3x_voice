/**
 * The puppeteer layer: a FIXED, NAMED command space, not raw joints.
 *
 * Disney's "Autonomous Human-Robot Interaction via Operator Imitation" (arXiv 2504.02724)
 * trained a small transformer on under an hour of an operator puppeteering BD-X through a
 * gamepad: ~10 continuous signals, a handful of discrete behaviours and modes, at 50 Hz.
 * Users could not tell it from the human operator, and it transferred zero-shot to another
 * robot with the same operator interface. The command space is therefore the future action
 * space for learning: keep it stable, name it, and log it (take.ts). Renaming or remapping
 * a signal invalidates every recorded take; add new signals at the end instead.
 *
 * Continuous intents, each normalised to [-1, 1] and mapped non-linearly to joints:
 *   gaze_yaw    head pan; past the neck limit the rings follow (head leads, body follows)
 *   gaze_pitch  head tilt (+1 = look up)
 *   lift        head lift
 *   body_yaw    rings (posture)
 *   lean        +1 leans in (lift up, chin down, visor up), -1 leans back
 *   visor       +1 = visor open (up)
 *   arm_raise   hero arm
 *   energy      a mood knob: scales procedural alive motion and cue-slot intensity/speed
 * Discrete: 8 cue slots, and a mode (idle / engaged / dj).
 *
 * Gamepad (standard mapping): left stick = body_yaw / lean, right stick = gaze, d-pad =
 * lift / visor, RT - LT = arm_raise, LB/RB = energy down/up, A B X Y = cue slots 1-4
 * (hold Back for 5-8), R3 = cycle mode, Start = motion.freeze toggle.
 */
import type { Pose } from './body';

export const CONTINUOUS = ['gaze_yaw', 'gaze_pitch', 'lift', 'body_yaw', 'lean', 'visor', 'arm_raise', 'energy'] as const;
export type Intent = (typeof CONTINUOUS)[number];
export const MODES = ['idle', 'engaged', 'dj'] as const;
export type PuppetMode = (typeof MODES)[number];
export const SLOT_COUNT = 8;

export type Command = Record<Intent, number>;

const clamp1 = (x: number) => (x < -1 ? -1 : x > 1 ? 1 : x);
/** Expo curve: fine control near centre, full range at the end of travel. */
const expo = (x: number, k = 0.55) => clamp1(x) * ((1 - k) + k * clamp1(x) * clamp1(x));
const DEAD = 0.12;
const dead = (x: number) => (Math.abs(x) < DEAD ? 0 : (x - Math.sign(x) * DEAD) / (1 - DEAD));

/** Joint ranges the intents map onto (soft-limit safe for r3x_animation). */
const NECK = 60; // deg of pan before the rings take over
const MAP = {
  gazeYaw: 85, gazePitch: 14, lift: 12, bodyYawLower: 24, bodyYawTop: 14, visorOpen: 11, visorClose: 14,
  armUp: 34, armDown: 20,
};

export interface PuppeteerHooks {
  slot(i: number): void;
  mode(m: PuppetMode): void;
  freeze(): void;
}

export class Puppeteer {
  /** The command the operator is giving now (after deadzone and smoothing). */
  readonly cmd: Command = Object.fromEntries(CONTINUOUS.map((k) => [k, 0])) as Command;
  /** Raw targets (UI sliders / __r3x / gamepad) before smoothing. */
  readonly target: Command = Object.fromEntries(CONTINUOUS.map((k) => [k, 0])) as Command;
  mode: PuppetMode = 'idle';
  /** Discrete triggers since the last take sample. */
  readonly fired: number[] = [];
  gamepad = false;
  enabled = true;
  private prevButtons: boolean[] = [];
  private dpadLift = 0;
  private dpadVisor = 0;

  constructor(private readonly hooks: PuppeteerHooks) {}

  set(partial: Partial<Command>) {
    for (const [k, v] of Object.entries(partial)) if (k in this.target && Number.isFinite(v)) this.target[k as Intent] = clamp1(v as number);
  }

  trigger(slot: number) {
    if (slot < 0 || slot >= SLOT_COUNT) return;
    this.fired.push(slot);
    this.hooks.slot(slot);
  }

  setMode(m: PuppetMode) {
    this.mode = m;
    this.hooks.mode(m);
  }

  /** Poll the gamepad (if any) and smooth the command. dt in s. */
  update(dt: number) {
    const pads = typeof navigator !== 'undefined' && navigator.getGamepads ? navigator.getGamepads() : [];
    const pad = [...pads].find((p) => p && p.connected && p.mapping === 'standard') ?? null;
    this.gamepad = !!pad;
    if (pad) this.readPad(pad, dt);
    const k = 1 - Math.exp(-dt / 0.08); // ~80 ms smoothing: sticks are noisy, servos are not
    for (const i of CONTINUOUS) this.cmd[i] += (this.target[i] - this.cmd[i]) * k;
  }

  private readPad(pad: Gamepad, dt: number) {
    const ax = (i: number) => dead(pad.axes[i] ?? 0);
    const btn = (i: number) => !!pad.buttons[i]?.pressed;
    const val = (i: number) => pad.buttons[i]?.value ?? 0;
    const edge = (i: number) => btn(i) && !this.prevButtons[i];
    // D-pad holds ease toward +/-1 and back to 0 when released.
    const rate = 1 - Math.exp(-dt / 0.15);
    this.dpadLift += ((btn(12) ? 1 : btn(13) ? -1 : 0) - this.dpadLift) * rate;
    this.dpadVisor += ((btn(15) ? 1 : btn(14) ? -1 : 0) - this.dpadVisor) * rate;
    this.target.body_yaw = ax(0);
    this.target.lean = -ax(1);
    this.target.gaze_yaw = ax(2);
    this.target.gaze_pitch = -ax(3);
    this.target.lift = this.dpadLift;
    this.target.visor = this.dpadVisor;
    this.target.arm_raise = clamp1(val(7) - val(6));
    if (edge(4)) this.target.energy = clamp1(this.target.energy - 0.25);
    if (edge(5)) this.target.energy = clamp1(this.target.energy + 0.25);
    const shift = btn(8) ? 4 : 0;
    for (let b = 0; b < 4; b++) if (edge(b)) this.trigger(b + shift);
    if (edge(11)) this.setMode(MODES[(MODES.indexOf(this.mode) + 1) % MODES.length]);
    if (edge(9)) this.hooks.freeze();
    this.prevButtons = pad.buttons.map((b) => b.pressed);
  }

  /** The layer: intents to additive joint offsets. `pose` is everything below. */
  apply(pose: Pose) {
    if (!this.enabled) return;
    const c = this.cmd;
    const add = (j: string, v: number) => { if (v) pose[j] = (pose[j] ?? 0) + v; };

    // Gaze: the head leads; whatever would take the neck past its limit goes to the rings.
    const yaw = expo(c.gaze_yaw) * MAP.gazeYaw;
    const pan = pose.head_pan ?? 0;
    const want = pan + yaw;
    const neck = Math.max(-NECK, Math.min(NECK, want));
    const spill = want - neck;
    add('head_pan', neck - pan);
    add('torso_top', spill * 0.6);
    add('torso_lower', spill * 0.4);
    add('head_tilt', -expo(c.gaze_pitch) * MAP.gazePitch);

    add('head_lift', expo(c.lift) * MAP.lift);
    const body = expo(c.body_yaw);
    add('torso_lower', body * MAP.bodyYawLower);
    add('torso_top', body * MAP.bodyYawTop);

    const lean = expo(c.lean);
    add('head_lift', lean * 7);
    add('head_tilt', lean * 5);
    add('visor', -lean * 6);

    const v = expo(c.visor);
    add('visor', v >= 0 ? -v * MAP.visorOpen : -v * MAP.visorClose);
    const arm = expo(c.arm_raise);
    add('hero_shoulder', arm >= 0 ? arm * MAP.armUp : arm * MAP.armDown);
    add('hero_wrist', arm * 20);
  }

  /** Energy as a gain: 0.4x at -1, 1x at 0, 1.6x at +1. */
  get energyGain() { return 1 + 0.6 * this.cmd.energy; }
}
