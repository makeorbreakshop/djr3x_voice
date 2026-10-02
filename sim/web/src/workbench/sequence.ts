/**
 * Instructions' step animation (workbench.ts plays it): the order things go in, and where a fastener is
 * on its way. Pure, so the rules are tested (test/sequence.test.ts):
 *
 * - parts one at a time (a like pair with no screws of its own together), each followed by the
 *   fasteners it completes - those whose every joined part has now seated - so a servo drops into its
 *   pocket and its four screws drive in before the other servo comes;
 * - a batch of fasteners crosswise round its bolt circle, inserts first, each nut right after the
 *   screw it goes on;
 * - a fastener only ever moves along its own axis (its local Z: +Z is the insertion direction, the
 *   manifest's `transform`, the same line as `features.shank`): a screw or a washer from the head
 *   side, a nut from the far side, spinning on as it drives in; an insert or a pin presses in.
 */

import * as THREE from 'three';
import type { MSpec } from './manifest';

export interface SeqItem {
  kind: 'part' | 'fastener';
  ids: string[];
  /** Seconds from the step's start, and how long it moves. */
  start: number;
  dur: number;
}

export interface SeqFastener { id: string; spec: MSpec; joins: string[]; /** seat position and axis (any one frame) */ at: THREE.Vector3; axis: THREE.Vector3 }
export interface SeqPart { id: string; group: string; at: THREE.Vector3 }

const DUR = { part: 0.65, fastener: 0.45 };
/** The next item starts when the one before is this far in (a little overlap keeps it snappy). */
const OVERLAP = 0.7;
const HOLD = 0.35;
/** A long step is played faster, never longer than this (s), and no item quicker than MIN_DUR. */
const MAX_TOTAL = 8;
const MIN_DUR = 0.22;

type Kind = 'screw' | 'nut' | 'washer' | 'insert' | 'pin' | 'other';
export function fastenerKind(spec: MSpec): Kind {
  const t = `${spec.type}`.toLowerCase();
  if (t === 'insert') return 'insert';
  if (t === 't_nut') return 'other';
  if (t.includes('nut')) return 'nut';
  if (t.includes('washer')) return 'washer';
  if (t === 'pin' || t.includes('dowel')) return 'pin';
  if (/shcs|bhcs|fhcs|screw|bolt|grub|threaded/.test(t)) return 'screw';
  return 'other';
}

/** How far out a fastener starts (mm, along its axis) and which way: +1 the head side, -1 the far side. */
export function fastenerTravel(spec: MSpec): { mm: number; side: 1 | -1; turns: number } {
  const k = fastenerKind(spec);
  const len = Number(spec.length_mm) || 8;
  if (k === 'nut') return { mm: 16, side: -1, turns: 1.5 };
  if (k === 'screw') return { mm: Math.max(18, len * 1.6), side: 1, turns: 1.5 };
  if (k === 'washer') return { mm: 14, side: 1, turns: 0 };
  if (k === 'insert') return { mm: 12, side: 1, turns: 0 };
  return { mm: Math.max(12, len * 1.2), side: 1, turns: 0 };
}

/**
 * A fastener's pose at `u` (1: where it starts, 0: seated): its seated matrix moved back along its own
 * axis and turned about it. Never anything else: no part's offset rides along (a part still coming in only
 * pushes it further out along its own line, `clear`).
 */
export function fastenerPose(seat: THREE.Matrix4, spec: MSpec, u: number, out = new THREE.Matrix4(), clear = 0): THREE.Matrix4 {
  const { mm, side, turns } = fastenerTravel(spec);
  // `clear` (mm): further out along the same axis while a part it goes through is still on its way in
  const back = (mm * u + clear) * side;
  out.copy(seat).multiply(new THREE.Matrix4().makeTranslation(0, 0, -back));
  if (turns && u > 0) out.multiply(new THREE.Matrix4().makeRotationZ(u * turns * Math.PI * 2));
  return out;
}

/** Crosswise round a bolt circle: 0, n/2, 1, n/2+1 ... (four screws: 1-3-2-4). */
export function crosswise<T extends { at: THREE.Vector3; axis: THREE.Vector3 }>(list: T[]): T[] {
  if (list.length < 3) return list;
  const c = list.reduce((v, f) => v.add(f.at), new THREE.Vector3()).divideScalar(list.length);
  const n = list.reduce((v, f) => v.add(f.axis), new THREE.Vector3()).normalize();
  if (n.lengthSq() < 0.5) n.set(0, 1, 0);
  const u = new THREE.Vector3(1, 0, 0);
  if (Math.abs(u.dot(n)) > 0.9) u.set(0, 0, 1);
  u.sub(n.clone().multiplyScalar(u.dot(n))).normalize();
  const v = n.clone().cross(u);
  const ang = (f: T) => { const d = f.at.clone().sub(c); return Math.atan2(d.dot(v), d.dot(u)); };
  const ring = [...list].sort((a, b) => ang(a) - ang(b));
  const half = Math.ceil(ring.length / 2);
  const out: T[] = [];
  for (let i = 0; i < half; i++) {
    out.push(ring[i]);
    if (i + half < ring.length) out.push(ring[i + half]);
  }
  return out;
}

/** One batch of fasteners in the order they go in: inserts, then screws crosswise with each nut after its screw. */
export function fastenerOrder(batch: SeqFastener[]): SeqFastener[] {
  const kind = (f: SeqFastener) => fastenerKind(f.spec);
  const inserts = crosswise(batch.filter((f) => kind(f) === 'insert'));
  const screws = crosswise(batch.filter((f) => ['screw', 'pin', 'other'].includes(kind(f))));
  const followers = batch.filter((f) => kind(f) === 'nut' || kind(f) === 'washer');
  const out: SeqFastener[] = [...inserts];
  const left = new Set(followers);
  const dist = (p: THREE.Vector3, s: SeqFastener) => {
    const d = p.clone().sub(s.at);
    return d.sub(s.axis.clone().multiplyScalar(d.dot(s.axis))).length();
  };
  for (const s of screws) {
    // a washer under its head first, then the screw, then the nut on its end (on the same line)
    const mine = [...left].filter((f) => dist(f.at, s) < 1.5).sort((a, b) => (kind(a) === 'washer' ? -1 : 0) - (kind(b) === 'washer' ? -1 : 0));
    mine.forEach((f) => left.delete(f));
    out.push(...mine.filter((f) => kind(f) === 'washer'), s, ...mine.filter((f) => kind(f) !== 'washer'));
  }
  out.push(...crosswise([...left]));
  return out;
}

/**
 * The step's sequence. `parts` in the step's order; `seated(id)`: placed before this step (or not part of
 * it). A like group (the same `group`) with no fastener of its own goes in together.
 */
export function planSequence(parts: SeqPart[], fasteners: SeqFastener[], seatedBefore: (id: string) => boolean): { items: SeqItem[]; total: number } {
  const own = (ids: string[]) => fasteners.some((f) => f.joins.some((j) => ids.includes(j)));
  const units: string[][] = [];
  const byGroup = new Map<string, string[]>();
  for (const p of parts) (byGroup.get(p.group) ?? byGroup.set(p.group, []).get(p.group)!).push(p.id);
  const done = new Set<string>();
  for (const p of parts) {
    if (done.has(p.id)) continue;
    const g = byGroup.get(p.group)!;
    const together = g.length > 1 && !own(g) ? g : [p.id];
    together.forEach((x) => done.add(x));
    units.push(together);
  }
  const inStep = new Set(parts.map((p) => p.id));
  const seated = new Set<string>();
  const queued = new Set<string>();
  const raw: { kind: SeqItem['kind']; ids: string[]; dur: number }[] = [];
  const flush = () => {
    const ready = fasteners.filter((f) => !queued.has(f.id) && f.joins.every((j) => !inStep.has(j) || seated.has(j) || seatedBefore(j)));
    for (const f of fastenerOrder(ready)) {
      queued.add(f.id);
      raw.push({ kind: 'fastener', ids: [f.id], dur: DUR.fastener });
    }
  };
  flush(); // hardware into what is already there (heat-set inserts before anything else)
  for (const u of units) {
    raw.push({ kind: 'part', ids: u, dur: DUR.part });
    u.forEach((x) => seated.add(x));
    flush();
  }
  for (const f of fastenerOrder(fasteners.filter((f) => !queued.has(f.id)))) raw.push({ kind: 'fastener', ids: [f.id], dur: DUR.fastener });
  return timed(raw.map((it) => ({ kind: it.kind, ids: it.ids })));
}

/** How a step's items are paced: seconds per part and per fastener, how far in the one before is when the
 *  next starts (a bolt circle's screws may overlap more), the hold before the first, the longest a step
 *  may take (then it plays faster, no item quicker than minDur). */
export interface Timing { part: number; fastener: number; overlap: number; fastenerOverlap: number; hold: number; maxTotal: number; minDur: number }
export const GUIDE_TIMING: Timing = { part: DUR.part, fastener: DUR.fastener, overlap: OVERLAP, fastenerOverlap: OVERLAP, hold: HOLD, maxTotal: MAX_TOTAL, minDur: MIN_DUR };

/** Items in order, timed. */
export function timed(order: { kind: SeqItem['kind']; ids: string[] }[], tm: Timing = GUIDE_TIMING): { items: SeqItem[]; total: number } {
  const dur = (it: { kind: SeqItem['kind'] }) => (it.kind === 'part' ? tm.part : tm.fastener);
  const gap = (it: { kind: SeqItem['kind'] }, next?: { kind: SeqItem['kind'] }) =>
    dur(it) * (it.kind === 'fastener' && next?.kind === 'fastener' ? tm.fastenerOverlap : tm.overlap);
  const span = order.reduce((t, it, i) => t + (i === order.length - 1 ? dur(it) : gap(it, order[i + 1])), 0);
  const k = span > tm.maxTotal ? Math.max(tm.minDur / tm.fastener, tm.maxTotal / span) : 1;
  let t = tm.hold;
  const items: SeqItem[] = order.map((it, i) => {
    const item = { kind: it.kind, ids: it.ids, start: t, dur: dur(it) * k };
    t += gap(it, order[i + 1]) * k;
    return item;
  });
  const total = items.length ? Math.max(...items.map((i) => i.start + i.dur)) : 0;
  return { items, total };
}

/** Where along its path (way points: offsets from its seat, the start first) an item is at `u` (1 start, 0 seated). */
export function pathAt(points: THREE.Vector3[], u: number, out = new THREE.Vector3()): THREE.Vector3 {
  if (!points.length || u <= 0) return out.set(0, 0, 0);
  const pts = [...points, new THREE.Vector3()];
  const lens = pts.slice(1).map((p, i) => p.distanceTo(pts[i]));
  const total = lens.reduce((a, b) => a + b, 0);
  // distance still to go, walked back from the seat
  let left = Math.min(1, u) * total;
  for (let i = lens.length - 1; i >= 0; i--) {
    if (left <= lens[i] || i === 0) {
      const f = lens[i] > 0 ? Math.min(1, left / lens[i]) : 0;
      return out.copy(pts[i + 1]).lerp(pts[i], f);
    }
    left -= lens[i];
  }
  return out.copy(pts[0]);
}

/** An item's travel left at time t: 1 out, 0 seated, eased. */
export function itemU(it: SeqItem, t: number): number {
  const k = Math.min(1, Math.max(0, (t - it.start) / it.dur));
  const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
  return 1 - e;
}
