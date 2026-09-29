/**
 * Expansion (SPEC "Cross-language parity"): a cue or sequence flattened to a time-sorted
 * list of department actions. Loops are not unrolled and waits resolve as 0 s. The Python
 * TimelineExecutor must produce the same list for show/tests/fixtures/*.json.
 */
import type { Catalog } from './catalog';
import type { Action, DeptAction, Expanded, Sequence, ShowItem, TrackItem } from './types';

export const MAX_DEPTH = 3;
export const DEFAULT_BPM = 120;

const round3 = (t: number) => Math.round(t * 1000) / 1000;

/** Fill the parity defaults: clip intensity/speed, lights fade/hold, chest hold. */
export function normalizeAction(a: Action): DeptAction | null {
  const { at: _at, ...rest } = a as Action & { at?: number };
  void _at;
  switch (rest.do) {
    case 'wait': return null;
    case 'clip': return { ...rest, intensity: rest.intensity ?? 1, speed: rest.speed ?? 1 };
    case 'lights': return { ...rest, fade: rest.fade ?? 0, hold: rest.hold ?? 0 };
    case 'chest': return { ...rest, hold: rest.hold ?? 0 };
    default: return rest;
  }
}

/** A sequence track item as a department action (clip/do), or a reference to descend into. */
export function trackEntry(it: TrackItem): { ref: 'cue' | 'sequence'; id: string } | Action {
  // `do` first: a lights action also has a `cue` field (the light cue).
  if ('do' in it) {
    const { at: _at, ...rest } = it;
    void _at;
    return rest as Action;
  }
  if ('cue' in it) return { ref: 'cue', id: it.cue as string };
  if ('sequence' in it) return { ref: 'sequence', id: it.sequence as string };
  if ('clip' in it) {
    const a: Action = { do: 'clip', id: it.clip as string };
    if (it.intensity !== undefined) a.intensity = it.intensity;
    if (it.speed !== undefined) a.speed = it.speed;
    return a;
  }
  throw new Error(`track item at ${(it as { at: number }).at} has no cue | clip | sequence | do`);
}

/** Seconds per unit of a sequence's clock at the given tempo. */
export function secondsPerUnit(seq: Sequence, bpm?: number) {
  return seq.clock === 'beat' ? 60 / (bpm ?? seq.bpm ?? DEFAULT_BPM) : 1;
}

export function expand(rootId: string, cat: Catalog, opts: { bpm?: number } = {}): Expanded[] {
  const out: { t: number; a: DeptAction }[] = [];
  const emit = (t: number, a: Action) => {
    const n = normalizeAction(a);
    if (n) out.push({ t: round3(t), a: n });
  };
  const walk = (item: ShowItem, offset: number, depth: number) => {
    if (depth > MAX_DEPTH) throw new Error(`${item.id}: nesting deeper than ${MAX_DEPTH}`);
    if (item.kind === 'clip') return emit(offset, { do: 'clip', id: item.id });
    if (item.kind === 'cue') {
      for (const a of item.actions) emit(offset + a.at, a);
      return;
    }
    const k = secondsPerUnit(item, opts.bpm);
    for (const it of item.track) {
      const t = offset + it.at * k;
      const e = trackEntry(it);
      if ('ref' in e) {
        const child = cat.get(e.id);
        if (!child || child.kind !== e.ref) throw new Error(`${item.id}: unknown ${e.ref} "${e.id}"`);
        walk(child, t, depth + 1);
      } else {
        emit(t, e);
      }
    }
  };
  const root = cat.get(rootId);
  if (!root) throw new Error(`unknown show item "${rootId}"`);
  walk(root, 0, 1);
  // Array.prototype.sort is stable: ties keep file (walk) order.
  return out.sort((x, y) => x.t - y.t).map(({ t, a }) => ({ t, ...a }) as Expanded);
}

/** Direct references of a cue or sequence, in order. */
export function children(it: ShowItem): ['clip' | 'cue' | 'sequence', string][] {
  if (it.kind === 'cue') return it.actions.flatMap((a) => (a.do === 'clip' ? [['clip', a.id] as ['clip', string]] : []));
  if (it.kind === 'sequence') {
    return it.track.flatMap((x): ['clip' | 'cue' | 'sequence', string][] => {
      if ('do' in x) return x.do === 'clip' ? [['clip', x.id]] : [];
      if ('cue' in x) return [['cue', x.cue as string]];
      if ('sequence' in x) return [['sequence', x.sequence as string]];
      if ('clip' in x) return [['clip', x.clip as string]];
      return [];
    });
  }
  return [];
}

/** The joints a run of this sequence owns: its own `owns` plus nested ones (as the player does). */
export function ownsUnion(it: ShowItem, cat: Catalog, depth = 1): string[] | null {
  if (it.kind !== 'sequence' || depth > MAX_DEPTH) return null;
  const set = new Set(it.owns ?? []);
  let any = !!it.owns;
  for (const [kind, id] of children(it)) {
    const c = kind === 'sequence' ? cat.sequence(id) : undefined;
    const o = c && ownsUnion(c, cat, depth + 1);
    if (o) { any = true; o.forEach((j) => set.add(j)); }
  }
  return any ? [...set] : null;
}
