/**
 * R3X Show Format v1 (show/SPEC.md) as TypeScript types. The JSON files under the repo's
 * `show/` folder are the source of truth; CantinaOS reads the same files.
 */

export type Kind = 'clip' | 'cue' | 'sequence';
export type Tier = 'free' | 'cheap' | 'show';
export type Ease = 'minjerk' | 'linear' | 'step';
export type Layer = 'gesture' | 'show';
export type Source = 'jev' | 'claude' | 'timeline' | 'idle' | 'ui' | 'cli';

export const KINDS: readonly Kind[] = ['clip', 'cue', 'sequence'];
export const TIERS: readonly Tier[] = ['free', 'cheap', 'show'];
export const SOURCES: readonly Source[] = ['jev', 'claude', 'timeline', 'idle', 'ui', 'cli'];

/** The eight joints the 8-servo `r3x_animation` build drives. */
export const BASE_JOINTS = [
  'head_pan', 'head_tilt', 'head_lift', 'visor', 'hero_shoulder', 'hero_wrist', 'torso_lower', 'torso_top',
] as const;
/** Joints that need `"requires": "extended"`. */
export const EXTENDED_JOINT = /^(torso_middle|hero_claw_.+|throttle_.+|poker_.+)$/;

interface Common {
  id: string;
  kind: Kind;
  title?: string;
  /** One line: what Claude and Jev see in their catalogue. */
  description: string;
  tags?: string[];
  tier: Tier;
}

export interface Track {
  mode: 'additive' | 'override';
  /** [t_seconds, value]; the first key is at t=0. */
  keys: [number, number][];
  ease?: Ease;
  /** Override blend in/out (s); default by joint class, see defaultBlend(). */
  blend?: number;
}

export interface Clip extends Common {
  kind: 'clip';
  duration: number;
  interruptible_after?: number;
  requires?: 'extended';
  tracks: Record<string, Track>;
}

export type Action =
  | { do: 'clip'; id: string; intensity?: number; speed?: number }
  | { do: 'eyes'; pattern: string; color?: string; intensity?: number; duration?: number }
  | { do: 'chest'; command: string; hold?: number }
  | { do: 'lights'; cue?: string; mode?: string; fade?: number; hold?: number; rig?: string }
  | { do: 'sfx'; id: string }
  | { do: 'speak'; text: string }
  | { do: 'duck' }
  | { do: 'unduck' }
  | { do: 'wait'; for: 'speech_end' };

export type ActionKind = Action['do'];
export const ACTIONS: readonly ActionKind[] = ['clip', 'eyes', 'chest', 'lights', 'sfx', 'speak', 'duck', 'unduck', 'wait'];

export type Timed<T> = T & { at: number };

export interface Cue extends Common {
  kind: 'cue';
  actions: Timed<Action>[];
}

export type TrackItem =
  | { at: number; cue: string }
  | { at: number; clip: string; intensity?: number; speed?: number }
  | { at: number; sequence: string }
  | Timed<Action>;

export interface Sequence extends Common {
  kind: 'sequence';
  clock?: 'time' | 'beat';
  bpm?: number;
  layer?: Layer;
  owns?: string[];
  loop?: boolean;
  length?: number;
  track: TrackItem[];
}

export type ShowItem = Clip | Cue | Sequence;

export interface WeightedId { id: string; weight: number }
export interface IdlePolicy {
  after_s: number;
  choices: WeightedId[];
  while_music?: WeightedId[];
}

export interface Params { intensity?: number; speed?: number }

/** One entry of an expansion: the flat, time-sorted parity format. */
export type DeptAction = Exclude<Action, { do: 'wait' }>;
export type Expanded = { t: number } & DeptAction;

/**
 * Default override blend by joint class (SPEC Clip "Keys", after Disney's BD-X engine:
 * light "show function" parts blend faster than the body).
 */
export function defaultBlend(joint: string): number {
  if (joint === 'visor') return 0.1;
  if (joint.startsWith('head_')) return 0.2;
  return 0.35;
}

export const clampIntensity = (x: number) => Math.min(1.5, Math.max(0, x));
export const clampSpeed = (x: number) => Math.min(2, Math.max(0.5, x));

/** Who may trigger what (SPEC "Tiers"). */
export function tierAllows(tier: Tier, source: Source): boolean {
  if (tier === 'free') return true;
  if (tier === 'cheap') return source !== 'idle';
  return source === 'claude' || source === 'timeline' || source === 'ui' || source === 'cli';
}
