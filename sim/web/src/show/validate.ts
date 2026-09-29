/**
 * Structural validation of show files (SPEC "Common fields", "Clip", "Cue", "Sequence").
 * Returns human-readable errors; an empty list means the document is well formed.
 * Cross-file checks (ids resolve, rig limits, nesting depth) live in lint.ts.
 */
import { ACTIONS, KINDS, TIERS, type IdlePolicy, type ShowItem } from './types';

const ID = /^[a-z][a-z0-9_]*$/;
type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === 'object' && x !== null && !Array.isArray(x);
const num = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const str = (x: unknown): x is string => typeof x === 'string' && x.length > 0;

const ACTION_FIELDS: Record<string, string[]> = {
  clip: ['id', 'intensity', 'speed'],
  eyes: ['pattern', 'color', 'intensity', 'duration'],
  chest: ['command', 'hold'],
  lights: ['cue', 'mode', 'fade', 'hold', 'rig'],
  sfx: ['id'],
  speak: ['text'],
  duck: [],
  unduck: [],
  wait: ['for'],
};

function checkParams(o: Obj, where: string, err: string[]) {
  if (o.intensity !== undefined && !(num(o.intensity) && o.intensity >= 0 && o.intensity <= 1.5)) err.push(`${where}: intensity must be 0..1.5`);
  if (o.speed !== undefined && !(num(o.speed) && o.speed >= 0.5 && o.speed <= 2)) err.push(`${where}: speed must be 0.5..2`);
}

/** A department action (`{do: ...}`), with or without `at`. */
export function validateAction(a: unknown, where: string, err: string[]) {
  if (!isObj(a)) return void err.push(`${where}: not an object`);
  const d = a.do;
  if (typeof d !== 'string' || !ACTIONS.includes(d as never)) return void err.push(`${where}: unknown do "${String(d)}"`);
  const allowed = new Set(['do', 'at', ...ACTION_FIELDS[d]]);
  for (const k of Object.keys(a)) if (!allowed.has(k)) err.push(`${where}: "${d}" has unknown field "${k}"`);
  switch (d) {
    case 'clip':
      if (!str(a.id)) err.push(`${where}: clip needs id`);
      checkParams(a, where, err);
      break;
    case 'eyes':
      if (!str(a.pattern)) err.push(`${where}: eyes needs pattern`);
      if (a.color !== undefined && !(typeof a.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(a.color))) err.push(`${where}: color must be #rrggbb`);
      if (a.duration !== undefined && !(num(a.duration) && a.duration >= 0)) err.push(`${where}: duration must be >= 0`);
      if (a.intensity !== undefined && !(num(a.intensity) && a.intensity >= 0 && a.intensity <= 1)) err.push(`${where}: eyes intensity must be 0..1`);
      break;
    case 'chest':
      if (!str(a.command)) err.push(`${where}: chest needs command`);
      if (a.hold !== undefined && !(num(a.hold) && a.hold >= 0)) err.push(`${where}: hold must be >= 0`);
      break;
    case 'lights':
      if (!str(a.cue) && !str(a.mode) && !str(a.rig)) err.push(`${where}: lights needs cue, mode or rig`);
      for (const k of ['fade', 'hold'] as const) if (a[k] !== undefined && !(num(a[k]) && (a[k] as number) >= 0)) err.push(`${where}: ${k} must be >= 0`);
      break;
    case 'sfx':
      if (!str(a.id)) err.push(`${where}: sfx needs id`);
      break;
    case 'speak':
      if (!str(a.text)) err.push(`${where}: speak needs text`);
      break;
    case 'wait':
      if (a.for !== 'speech_end') err.push(`${where}: wait supports only for: "speech_end"`);
      break;
  }
}

export function validateItem(x: unknown, file?: string): string[] {
  const err: string[] = [];
  const w = file ?? (isObj(x) && typeof x.id === 'string' ? x.id : '?');
  if (!isObj(x)) return [`${w}: not an object`];
  if (!(typeof x.id === 'string' && ID.test(x.id))) err.push(`${w}: id must be snake_case`);
  if (!KINDS.includes(x.kind as never)) err.push(`${w}: kind must be clip | cue | sequence`);
  if (!TIERS.includes(x.tier as never)) err.push(`${w}: tier must be free | cheap | show`);
  if (!str(x.description)) err.push(`${w}: description is required`);
  else if (/\n/.test(x.description)) err.push(`${w}: description must be one line`);
  if (x.title !== undefined && !str(x.title)) err.push(`${w}: title must be a string`);
  if (x.tags !== undefined && !(Array.isArray(x.tags) && x.tags.every(str))) err.push(`${w}: tags must be strings`);

  if (x.kind === 'clip') {
    if (!(num(x.duration) && x.duration > 0)) err.push(`${w}: duration must be > 0`);
    const dur = num(x.duration) ? x.duration : Infinity;
    if (x.interruptible_after !== undefined && !(num(x.interruptible_after) && x.interruptible_after >= 0 && x.interruptible_after <= dur)) {
      err.push(`${w}: interruptible_after must be within 0..duration`);
    }
    if (x.requires !== undefined && x.requires !== 'extended') err.push(`${w}: requires must be "extended"`);
    if (!isObj(x.tracks) || !Object.keys(x.tracks).length) err.push(`${w}: tracks must be a non-empty object`);
    else {
      for (const [joint, tr] of Object.entries(x.tracks)) {
        const tw = `${w}.${joint}`;
        if (!isObj(tr)) { err.push(`${tw}: not an object`); continue; }
        if (tr.mode !== 'additive' && tr.mode !== 'override') err.push(`${tw}: mode must be additive | override`);
        if (tr.ease !== undefined && !['minjerk', 'linear', 'step'].includes(tr.ease as string)) err.push(`${tw}: ease must be minjerk | linear | step`);
        if (tr.blend !== undefined && !(num(tr.blend) && tr.blend >= 0 && tr.blend <= 2)) err.push(`${tw}: blend must be 0..2 s`);
        for (const k of Object.keys(tr)) if (!['mode', 'keys', 'ease', 'blend'].includes(k)) err.push(`${tw}: unknown field "${k}"`);
        const keys = tr.keys;
        if (!Array.isArray(keys) || keys.length < 2 || !keys.every((k) => Array.isArray(k) && k.length === 2 && num(k[0]) && num(k[1]))) {
          err.push(`${tw}: keys must be >= 2 [t, value] pairs`);
          continue;
        }
        const ks = keys as [number, number][];
        if (ks[0][0] !== 0) err.push(`${tw}: first key must be at t=0`);
        for (let i = 1; i < ks.length; i++) if (!(ks[i][0] > ks[i - 1][0])) err.push(`${tw}: key times must increase`);
        if (ks[ks.length - 1][0] > dur + 1e-9) err.push(`${tw}: key past duration`);
        if (tr.mode === 'additive' && (ks[0][1] !== 0 || ks[ks.length - 1][1] !== 0)) err.push(`${tw}: additive tracks must start and end at 0`);
      }
    }
  } else if (x.kind === 'cue') {
    if (!Array.isArray(x.actions) || !x.actions.length) err.push(`${w}: actions must be a non-empty list`);
    else x.actions.forEach((a, i) => {
      const aw = `${w}.actions[${i}]`;
      if (!isObj(a) || !(num(a.at) && a.at >= 0)) err.push(`${aw}: at must be >= 0`);
      validateAction(a, aw, err);
    });
  } else if (x.kind === 'sequence') {
    if (x.clock !== undefined && x.clock !== 'time' && x.clock !== 'beat') err.push(`${w}: clock must be time | beat`);
    if (x.bpm !== undefined && !(num(x.bpm) && x.bpm >= 30 && x.bpm <= 300)) err.push(`${w}: bpm must be 30..300`);
    if (x.layer !== undefined && x.layer !== 'show' && x.layer !== 'gesture') err.push(`${w}: layer must be show | gesture`);
    if (x.owns !== undefined && !(Array.isArray(x.owns) && x.owns.every(str))) err.push(`${w}: owns must be joint names`);
    if (x.loop !== undefined && typeof x.loop !== 'boolean') err.push(`${w}: loop must be boolean`);
    if (x.loop === true && !(num(x.length) && x.length > 0)) err.push(`${w}: loop needs length > 0`);
    if (!Array.isArray(x.track) || !x.track.length) err.push(`${w}: track must be a non-empty list`);
    else x.track.forEach((it, i) => {
      const iw = `${w}.track[${i}]`;
      if (!isObj(it) || !(num(it.at) && it.at >= 0)) return void err.push(`${iw}: at must be >= 0`);
      // A department action first: a lights action also carries a `cue` field.
      if ('do' in it) return validateAction(it, iw, err);
      const refs = ['cue', 'clip', 'sequence'].filter((k) => k in it);
      if (refs.length !== 1) return void err.push(`${iw}: needs exactly one of cue | clip | sequence | do`);
      if (!str(it[refs[0]])) err.push(`${iw}: ${refs[0]} must be an id`);
      const allowed = new Set(['at', refs[0], ...(refs[0] === 'clip' ? ['intensity', 'speed'] : [])]);
      for (const k of Object.keys(it)) if (!allowed.has(k)) err.push(`${iw}: unknown field "${k}"`);
      if (refs[0] === 'clip') checkParams(it, iw, err);
      if (x.loop === true && num(x.length) && it.at >= x.length) err.push(`${iw}: at is past the loop length`);
    });
  }
  return err;
}

export function validateIdle(x: unknown): string[] {
  const err: string[] = [];
  if (!isObj(x)) return ['idle.json: not an object'];
  if (!(num(x.after_s) && x.after_s > 0)) err.push('idle.json: after_s must be > 0');
  const list = (k: string, required: boolean) => {
    const l = x[k];
    if (l === undefined && !required) return;
    if (!Array.isArray(l) || !l.length) return void err.push(`idle.json: ${k} must be a non-empty list`);
    l.forEach((c, i) => {
      if (!isObj(c) || !str(c.id) || !(num(c.weight) && c.weight > 0)) err.push(`idle.json: ${k}[${i}] needs id and weight > 0`);
    });
  };
  list('choices', true);
  list('while_music', false);
  return err;
}

export const isShowItem = (x: unknown): x is ShowItem => validateItem(x).length === 0;
export const isIdlePolicy = (x: unknown): x is IdlePolicy => validateIdle(x).length === 0;
