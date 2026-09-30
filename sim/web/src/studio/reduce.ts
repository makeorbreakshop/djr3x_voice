/**
 * Take -> keys. A recorded channel (50 Hz samples) becomes a few min-jerk keys:
 *
 * 1. The samples are rate-limited to vMax / 1.875 first - what the servo could follow, and
 *    the bound that keeps every min-jerk segment built from them under vMax (a min-jerk
 *    segment peaks at 1.875x its average speed, and no segment can average faster than the
 *    samples it spans).
 * 2. Ramer-Douglas-Peucker against the *min-jerk* interpolant (not a straight line): a
 *    segment splits at its worst sample while that error exceeds `eps`. Splits never make a
 *    segment shorter than `minSeg` (60 ms), so keys do not crowd into accel-violating slivers.
 */
import { evalKeys, type Key, minjerk } from './model';

export interface Sample { t: number; v: number }
export interface ReduceOptions { eps: number; vMax: number; minSeg?: number }

/** Rate-limit a sampled channel to `rate` units/s (a follower, as the servo would lag). */
export function rateLimit(s: Sample[], rate: number): Sample[] {
  const out: Sample[] = [];
  for (const x of s) {
    const p = out[out.length - 1];
    if (!p) { out.push({ ...x }); continue; }
    const step = rate * (x.t - p.t);
    out.push({ t: x.t, v: p.v + Math.max(-step, Math.min(step, x.v - p.v)) });
  }
  return out;
}

export function reduce(samples: Sample[], o: ReduceOptions): Key[] {
  if (samples.length === 0) return [];
  const s = rateLimit(samples, o.vMax / 1.875);
  if (s.length === 1) return [{ t: s[0].t, v: s[0].v, ease: 'minjerk' }];
  const minSeg = o.minSeg ?? 0.06;
  const keep = new Set<number>([0, s.length - 1]);
  const stack: [number, number][] = [[0, s.length - 1]];
  while (stack.length) {
    const [i, j] = stack.pop()!;
    const a = s[i], b = s[j];
    let worst = -1, err = o.eps;
    for (let k = i + 1; k < j; k++) {
      if (s[k].t - a.t < minSeg || b.t - s[k].t < minSeg) continue;
      const e = Math.abs(a.v + (b.v - a.v) * minjerk((s[k].t - a.t) / (b.t - a.t)) - s[k].v);
      if (e > err) { err = e; worst = k; }
    }
    if (worst < 0) continue;
    keep.add(worst);
    stack.push([i, worst], [worst, j]);
  }
  const t0 = s[0].t;
  return [...keep].sort((x, y) => x - y).map((k) => ({ t: +(s[k].t - t0).toFixed(3), v: +s[k].v.toFixed(2), ease: 'minjerk' as const }));
}

/** Largest |error| between the samples and the keys' curve (for tests and the UI). */
export function maxError(samples: Sample[], keys: Key[]): number {
  const t0 = samples[0]?.t ?? 0;
  return Math.max(0, ...samples.map((x) => Math.abs(evalKeys(keys, x.t - t0) - x.v)));
}
