/**
 * Studio's clip model and its SPEC (show/SPEC.md "Clip") (de)serialisation.
 *
 * The editor keeps an ease per key (the segment leaving it). SPEC has one `ease` per track,
 * so a track whose segments disagree is baked on save: the minority segments are resampled
 * as extra keys under the track's majority ease (steps become a 1 ms jump). Everything the
 * editor does not know (unknown fields) round-trips untouched.
 */

export type Ease = 'minjerk' | 'linear' | 'step';
export type Mode = 'additive' | 'override';
export const EASES: Ease[] = ['minjerk', 'linear', 'step'];

export interface Key { t: number; v: number; ease: Ease }

export interface Track {
  joint: string;
  mode: Mode;
  keys: Key[];
  blend?: number;
  /** The file said `ease` explicitly (kept so an untouched clip saves byte-for-byte equal). */
  easeExplicit?: boolean;
  extra?: Record<string, unknown>;
}

export interface StudioClip {
  id: string;
  title: string;
  description: string;
  tags: string[];
  tier: 'free' | 'cheap' | 'show';
  duration: number;
  interruptible_after?: number;
  requires?: string;
  tracks: Track[];
  extra?: Record<string, unknown>;
}

/** SPEC clip JSON, as the files hold it. */
export interface ClipDoc {
  id: string;
  kind: 'clip';
  title?: string;
  description: string;
  tags: string[];
  tier: string;
  duration: number;
  interruptible_after?: number;
  requires?: string;
  tracks: Record<string, { mode: Mode; keys: [number, number][]; ease?: Ease; blend?: number; [k: string]: unknown }>;
  [k: string]: unknown;
}

// ------------------------------------------------------------------ evaluation (curve.rs)

export const minjerk = (s: number) => s * s * s * (10 + s * (-15 + 6 * s));
const shape = (e: Ease, s: number) => (e === 'step' ? 0 : e === 'linear' ? s : minjerk(s));

/** Value at clip time `u`; holds the end keys outside the range. */
export function evalKeys(k: Key[], u: number): number {
  if (!k.length) return 0;
  if (u <= k[0].t) return k[0].v;
  const last = k[k.length - 1];
  if (u >= last.t) return last.v;
  let i = 1;
  while (k[i].t < u) i++;
  const a = k[i - 1], b = k[i];
  return a.v + (b.v - a.v) * shape(a.ease, (u - a.t) / (b.t - a.t));
}

/** Peak |velocity| of a segment (min-jerk exact; linear average; step infinite). */
export function segmentPeakV(a: Key, b: Key): number {
  const d = Math.abs(b.v - a.v), t = b.t - a.t;
  if (d === 0) return 0;
  return a.ease === 'step' ? Infinity : a.ease === 'linear' ? d / t : (1.875 * d) / t;
}

// ------------------------------------------------------------------ (de)serialisation

const round = (x: number, n: number) => { const p = 10 ** n; const r = Math.round(x * p) / p; return r === 0 ? 0 : r; };
const TOP = new Set(['id', 'kind', 'title', 'description', 'tags', 'tier', 'duration', 'interruptible_after', 'requires', 'tracks']);
const TRACK = new Set(['mode', 'keys', 'ease', 'blend']);
const pick = (o: Record<string, unknown>, known: Set<string>) => {
  const rest = Object.fromEntries(Object.entries(o).filter(([k]) => !known.has(k)));
  return Object.keys(rest).length ? rest : undefined;
};

export function fromDoc(doc: ClipDoc): StudioClip {
  return {
    id: doc.id,
    title: doc.title ?? '',
    description: doc.description ?? '',
    tags: [...(doc.tags ?? [])],
    tier: (doc.tier as StudioClip['tier']) ?? 'free',
    duration: doc.duration,
    interruptible_after: doc.interruptible_after,
    requires: doc.requires,
    extra: pick(doc, TOP),
    tracks: Object.entries(doc.tracks ?? {}).map(([joint, tr]) => ({
      joint,
      mode: tr.mode,
      keys: tr.keys.map(([t, v]) => ({ t, v, ease: tr.ease ?? 'minjerk' })),
      blend: tr.blend,
      easeExplicit: tr.ease !== undefined,
      extra: pick(tr, TRACK),
    })),
  };
}

/** The track's majority ease over its segments (ties: min-jerk first). */
export function trackEase(keys: Key[]): Ease {
  const n = new Map<Ease, number>();
  for (const k of keys.slice(0, -1)) n.set(k.ease, (n.get(k.ease) ?? 0) + 1);
  let best: Ease = 'minjerk';
  for (const e of EASES) if ((n.get(e) ?? 0) > (n.get(best) ?? 0)) best = e;
  return best;
}

/** Keys under one ease: segments with another ease resampled at `hz` (steps: a 1 ms jump). */
export function bake(keys: Key[], hz = 25): { ease: Ease; keys: [number, number][] } {
  const ease = trackEase(keys);
  const out: [number, number][] = [];
  keys.forEach((a, i) => {
    out.push([a.t, a.v]);
    const b = keys[i + 1];
    if (!b || a.ease === ease || a.v === b.v) return;
    if (a.ease === 'step') {
      if (b.t - a.t > 0.002) out.push([b.t - 0.001, a.v]);
      return;
    }
    const n = Math.max(1, Math.round((b.t - a.t) * hz));
    for (let j = 1; j < n; j++) {
      const t = a.t + ((b.t - a.t) * j) / n;
      out.push([t, a.v + (b.v - a.v) * shape(a.ease, j / n)]);
    }
  });
  return { ease, keys: out.map(([t, v]) => [round(t, 4), round(v, 3)]) };
}

export function toDoc(c: StudioClip): ClipDoc {
  const tracks: ClipDoc['tracks'] = {};
  for (const tr of c.tracks) {
    if (!tr.keys.length) continue;
    const { ease, keys } = bake(tr.keys);
    tracks[tr.joint] = {
      mode: tr.mode,
      keys,
      ...(ease !== 'minjerk' || tr.easeExplicit ? { ease } : {}),
      ...(tr.blend !== undefined ? { blend: tr.blend } : {}),
      ...tr.extra,
    };
  }
  const doc: ClipDoc = {
    id: c.id,
    kind: 'clip',
    ...(c.title ? { title: c.title } : {}),
    description: c.description,
    tags: c.tags,
    tier: c.tier,
    ...(c.requires ? { requires: c.requires } : {}),
    duration: round(c.duration, 3),
    ...(c.interruptible_after !== undefined ? { interruptible_after: c.interruptible_after } : {}),
    tracks,
    ...c.extra,
  };
  return doc;
}

/** A new, empty clip. */
export function blankClip(id = 'new_clip'): StudioClip {
  return { id, title: '', description: 'Studio clip', tags: [], tier: 'free', duration: 2, tracks: [] };
}

/** Keys sorted, first key pinned at t=0, times inside [0, duration]. */
export function normalizeTrack(tr: Track, duration: number) {
  tr.keys.sort((a, b) => a.t - b.t);
  for (const k of tr.keys) k.t = Math.min(Math.max(0, k.t), duration);
  if (tr.keys.length && tr.keys[0].t !== 0) tr.keys.unshift({ ...tr.keys[0], t: 0 });
  // Two keys at one instant make a zero-length segment: nudge apart by 1 ms.
  for (let i = 1; i < tr.keys.length; i++) if (tr.keys[i].t <= tr.keys[i - 1].t) tr.keys[i].t = tr.keys[i - 1].t + 0.001;
}

// ------------------------------------------------------------------ lint messages

export interface LintMark { joint?: string; t?: number; message: string }

/**
 * `lintShow` output filtered to one clip, with the joint and time pulled out of the
 * linter's messages (`<id>.<joint>: ... at t=<t> ...`, `(segment at t=<t>)`).
 */
export function lintMarks(errors: string[], id: string): LintMark[] {
  return errors
    .filter((e) => e.startsWith(`${id}.`) || e.startsWith(`${id}:`) || e.includes(`/${id}.json`))
    .map((message) => {
      const joint = message.startsWith(`${id}.`) ? message.slice(id.length + 1).split(':')[0] : undefined;
      const m = /t=(-?[\d.]+)/.exec(message);
      return { joint, t: m ? Number(m[1]) : undefined, message };
    });
}
