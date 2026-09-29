/**
 * The show linter (run by test/show.test.ts): every file validates, every id resolves,
 * nesting stays within 3 levels, tiers never escalate through nesting, and every clip is
 * something the servos can physically perform:
 *
 * - joint limits: the channel's soft limits exactly as the pipeline computes them (rig
 *   min/max, the servo's pulse reach through its gearing, minus the channel margin);
 * - velocity and acceleration: each track's peak against its channel's vMax/aMax. Those
 *   limits are joint-side - the jerk-limited follower runs in joint units, i.e. already
 *   after the gear ratio or rack pinion - so a 40 deg/s ring limit is 152 servo deg/s
 *   through a 3.8:1 gear;
 * - extended joints (anything outside the 8-servo r3x_animation build) need
 *   `"requires": "extended"`, and coupled joints (claw fingers) must be driven through the
 *   channel's primary joint.
 */
import servoMap from '../actuation/servo_map.json';
import { Channel, type ChannelConfig } from '../actuation/pipeline';
import type { JointSpec } from '../rig';
import { RIGS, type Mode } from '../stagelights';
import { Catalog, norm } from './catalog';
import { trackPeaks } from './curve';
import { MAX_DEPTH, children, ownsUnion } from './expand';
import rigLimits from './rig_limits.json';
import kitSfx from './kit_sfx.json';
import { EXTENDED_JOINT, TIERS, type Clip, type ShowItem, type Tier } from './types';

/** EyePattern values CantinaOS accepts (eye_light_controller_service.py). */
export const EYE_PATTERNS = ['idle', 'startup', 'engaged', 'listening', 'thinking', 'speaking', 'flash', 'happy', 'sad', 'angry', 'surprised', 'error', 'custom'];
/**
 * The ones the face firmware can show. SimpleEyeAdapter.set_pattern maps every other
 * pattern to "idle" (SI), so HAPPY/SAD/ANGRY/SURPRISED/ERROR currently look like IDLE.
 */
export const RENDERABLE_EYES = ['idle', 'engaged', 'listening', 'thinking', 'speaking', 'flash'];
export const CHEST_WORD = /^(S[IELTSF]|M\d{3}|B\d{3}|X[0-3]|H[0-9A-F]{3}|R)$/;
const LIGHT_MODES: Mode[] = ['idle', 'music', 'dj', 'speaking', 'off'];

type Doc = { default: string; profiles: Record<string, { inherit?: string; supplyVolts: number; channels: ChannelConfig[] }> };
const DOC = servoMap as unknown as Doc;

export interface JointLimit {
  lo: number;
  hi: number;
  vMax: number;
  aMax: number;
  channel: string;
  primary: boolean;
  base: boolean;
}

/** Per-joint soft limits and motion limits, from servo_map.json + the committed rig table. */
export function jointLimits(): Map<string, JointLimit> {
  const specs = new Map<string, JointSpec>(Object.entries(rigLimits.joints).map(([name, j]) => [name, {
    name, parent: null, pivot: [0, 0, 0], axis: [0, 1, 0], min: j.min, max: j.max, type: j.type as JointSpec['type'],
  }]));
  const base = new Set(DOC.profiles.r3x_animation.channels.flatMap((c) => Object.keys(c.joints)));
  const collect = (name: string): ChannelConfig[] => {
    const p = DOC.profiles[name];
    return [...(p.inherit ? collect(p.inherit) : []), ...p.channels];
  };
  const out = new Map<string, JointLimit>();
  for (const cfg of collect('extended')) {
    const ch = new Channel(structuredClone(cfg), specs, {}, DOC.profiles.extended.supplyVolts);
    const f = ch.follower;
    for (const j of Object.keys(cfg.joints)) {
      out.set(j, { lo: f.softMin, hi: f.softMax, vMax: cfg.vMax, aMax: cfg.aMax, channel: cfg.name, primary: j === ch.primaryJoint, base: base.has(j) });
    }
  }
  return out;
}

export const SFX_STEMS = new Map(kitSfx.stems.map((s) => [norm(s), s]));

export interface LintResult {
  errors: string[];
  warnings: string[];
}

const rank = (t: Tier) => TIERS.indexOf(t);

export function lintCatalog(cat: Catalog, limits = jointLimits()): LintResult {
  const errors = [...cat.errors];
  const warnings: string[] = [];

  for (const it of cat.items.values()) {
    if (it.description.length > 120) warnings.push(`${it.id}: description over 120 chars (it goes in an LLM catalogue)`);
    if (it.kind === 'clip') lintClip(it, limits, errors);
    else lintRefs(it, cat, errors, warnings);
  }

  // Nesting: depth <= 3, no cycles; tiers never escalate through nesting; owns covers overrides.
  for (const it of cat.items.values()) {
    if (it.kind === 'clip') continue;
    const walk = (x: ShowItem, depth: number, path: string[]) => {
      if (depth > MAX_DEPTH) return void errors.push(`${it.id}: nesting deeper than ${MAX_DEPTH} (${path.join(' > ')})`);
      for (const [kind, id] of children(x)) {
        const c = cat.get(id);
        if (!c || c.kind !== kind) continue; // reported by lintRefs
        if (path.includes(id)) return void errors.push(`${it.id}: cycle ${[...path, id].join(' > ')}`);
        if (x === it && rank(c.tier) > rank(it.tier)) errors.push(`${it.id} (${it.tier}) plays ${c.kind} ${id} (${c.tier}): a lower tier would let ${c.tier} content through`);
        if (kind !== 'clip') walk(c, depth + 1, [...path, id]);
      }
    };
    walk(it, 1, [it.id]);
    const union = it.kind === 'sequence' ? ownsUnion(it, cat) : null;
    if (union) {
      const owned = new Set(union);
      for (const clip of reachableClips(it, cat)) {
        for (const [j, tr] of Object.entries(clip.tracks)) {
          if (tr.mode === 'override' && !owned.has(j)) errors.push(`${it.id}: clip ${clip.id} overrides ${j}, which the sequence does not own (it would be masked)`);
        }
      }
    }
  }

  const idle = cat.idle;
  if (!idle) errors.push('show/idle.json missing or invalid');
  else {
    for (const c of [...idle.choices, ...(idle.while_music ?? [])]) {
      const x = cat.get(c.id);
      if (!x) errors.push(`idle.json: unknown id "${c.id}"`);
      else if (x.tier !== 'free') errors.push(`idle.json: ${c.id} is ${x.tier}; the idle source may only trigger free items`);
    }
  }
  return { errors, warnings };
}

function lintClip(c: Clip, limits: Map<string, JointLimit>, errors: string[]) {
  for (const [j, tr] of Object.entries(c.tracks)) {
    const w = `${c.id}.${j}`;
    const lim = limits.get(j);
    if (!lim) { errors.push(`${w}: no such joint`); continue; }
    if ((EXTENDED_JOINT.test(j) || !lim.base) && c.requires !== 'extended') errors.push(`${w}: extended joint; the clip needs "requires": "extended"`);
    if (!lim.primary) errors.push(`${w}: coupled joint; drive channel ${lim.channel} through its primary joint`);
    for (const [t, v] of tr.keys) {
      if (v < lim.lo - 1e-9 || v > lim.hi + 1e-9) errors.push(`${w}: ${v} at t=${t} outside ${lim.lo.toFixed(1)}..${lim.hi.toFixed(1)}`);
    }
    const pk = trackPeaks(tr);
    if (pk.v > lim.vMax + 1e-6) errors.push(`${w}: peak velocity ${pk.v.toFixed(0)} > vMax ${lim.vMax} (segment at t=${pk.vAt})`);
    if (pk.a > lim.aMax + 1e-6) errors.push(`${w}: peak acceleration ${Number.isFinite(pk.a) ? pk.a.toFixed(0) : 'inf'} > aMax ${lim.aMax} (segment at t=${pk.aAt})`);
  }
}

function lintRefs(it: ShowItem, cat: Catalog, errors: string[], warnings: string[]) {
  for (const [kind, id] of children(it)) {
    const c = cat.get(id);
    if (!c) errors.push(`${it.id}: unknown ${kind} "${id}"`);
    else if (c.kind !== kind) errors.push(`${it.id}: "${id}" is a ${c.kind}, not a ${kind}`);
  }
  const actions = it.kind === 'cue' ? it.actions : it.kind === 'sequence' ? it.track.filter((x) => 'do' in x) : [];
  for (const a of actions as { do: string; [k: string]: unknown }[]) {
    const w = `${it.id}: ${a.do}`;
    if (a.do === 'sfx' && !SFX_STEMS.has(norm(String(a.id)))) errors.push(`${w} "${a.id}" matches no kit sound (${kitSfx.stems.join(', ')})`);
    if (a.do === 'eyes') {
      if (!EYE_PATTERNS.includes(String(a.pattern))) errors.push(`${w} pattern "${a.pattern}" is not an EyePattern (${EYE_PATTERNS.join(', ')})`);
      else if (!RENDERABLE_EYES.includes(String(a.pattern))) warnings.push(`${w} "${a.pattern}" renders as IDLE on the current firmware`);
    }
    if (a.do === 'chest' && !CHEST_WORD.test(String(a.command))) errors.push(`${w} "${a.command}" is not a chest serial word`);
    if (a.do === 'lights') {
      if (a.cue !== undefined && !Object.values(RIGS).some((r) => String(a.cue) in r.cues)) errors.push(`${w} cue "${a.cue}" exists in no rig`);
      else if (a.cue !== undefined && !(String(a.cue) in RIGS.disneyland_2019.cues)) warnings.push(`${w} cue "${a.cue}" is not in the default rig`);
      if (a.mode !== undefined && !LIGHT_MODES.includes(a.mode as Mode)) errors.push(`${w} mode "${a.mode}" (${LIGHT_MODES.join(', ')})`);
      if (a.rig !== undefined && !(String(a.rig) in RIGS)) errors.push(`${w} rig "${a.rig}" (${Object.keys(RIGS).join(', ')})`);
    }
  }
}

function reachableClips(it: ShowItem, cat: Catalog, depth = 1): Clip[] {
  if (depth > MAX_DEPTH) return [];
  const out: Clip[] = [];
  for (const [, id] of children(it)) {
    const c = cat.get(id);
    if (!c) continue;
    if (c.kind === 'clip') out.push(c);
    else out.push(...reachableClips(c, cat, depth + 1));
  }
  return out;
}
