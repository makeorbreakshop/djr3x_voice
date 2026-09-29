/**
 * Keyframe evaluation. Min-jerk (Flash & Hogan) between keys by default: each segment
 * starts and ends at rest, the smoothest move a servo can follow for a given duration.
 * The actuation pipeline's jerk-limited follower stays downstream regardless.
 */
import type { Ease, Track } from './types';

/** Min-jerk position profile on s in [0,1]. */
export const minjerk = (s: number) => s * s * s * (10 + s * (-15 + 6 * s));

/** Track value at clip time u (seconds at speed 1); holds the end keys outside the range. */
export function evalTrack(tr: Track, u: number): number {
  const k = tr.keys;
  if (u <= k[0][0]) return k[0][1];
  const last = k[k.length - 1];
  if (u >= last[0]) return last[1];
  let i = 1;
  while (k[i][0] < u) i++;
  const [t0, v0] = k[i - 1];
  const [t1, v1] = k[i];
  return v0 + (v1 - v0) * ease(tr.ease ?? 'minjerk', (u - t0) / (t1 - t0));
}

function ease(e: Ease, s: number) {
  if (e === 'step') return 0;
  if (e === 'linear') return s;
  return minjerk(s);
}

/**
 * Peak |velocity| and |acceleration| of a track, per unit time at speed 1 and intensity 1.
 * Min-jerk segments are exact: v = 1.875 D/T, a = 5.7735 D/T^2 (10/sqrt(3)). Linear
 * segments have infinite acceleration at the keys, and step segments infinite velocity;
 * the linter reports those as such.
 */
export function trackPeaks(tr: Track): { v: number; a: number; vAt: number; aAt: number } {
  let v = 0, a = 0, vAt = 0, aAt = 0;
  const e = tr.ease ?? 'minjerk';
  for (let i = 1; i < tr.keys.length; i++) {
    const [t0, v0] = tr.keys[i - 1];
    const [t1, v1] = tr.keys[i];
    const D = Math.abs(v1 - v0);
    const T = t1 - t0;
    if (D === 0) continue;
    let sv: number, sa: number;
    if (e === 'minjerk') { sv = (1.875 * D) / T; sa = ((10 / Math.sqrt(3)) * D) / (T * T); }
    else if (e === 'linear') { sv = D / T; sa = Infinity; }
    else { sv = Infinity; sa = Infinity; }
    if (sv > v) { v = sv; vAt = t0; }
    if (sa > a) { a = sa; aAt = t0; }
  }
  return { v, a, vAt, aAt };
}
