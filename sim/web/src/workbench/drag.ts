/**
 * Direct manipulation's math (direct.ts is the pointer side): which joints a grabbed part moves, the
 * drag projections - a revolute joint like a door (the swept angle in the plane square to its axis), a
 * prismatic one along its axis line -, limits (soft at the ends, the manifest's coupled limits), a
 * damped-least-squares IK for chains of more than one joint, and what an actuator part drives (a servo
 * horn through its push rods, a pinion through its ratio). Pure: three.js maths and manifest data only.
 */

import * as THREE from 'three';
import type { MAssembly, MGear, MJoint } from './manifest';

const DEG = Math.PI / 180;

/** Degrees into (-180, 180]. */
export const wrapDeg = (d: number) => {
  const w = ((((d + 180) % 360) + 360) % 360) - 180;
  return w === -180 ? 180 : w;
};

/** Whether a joint can move at all (a fixed joint, or one with no range, is part of the ground). */
export const moves = (j: MJoint) => j.type !== 'fixed' && j.limits.max > j.limits.min;

// ------------------------------------------------------------------ projections

/** An in-plane basis for `axis`: n (unit axis), u (the zero direction), w = n x u. */
export function planeBasis(axis: THREE.Vector3, hint?: THREE.Vector3) {
  const n = axis.clone().normalize();
  const pickU = () => (Math.abs(n.x) < 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0));
  let u = hint?.clone() ?? pickU();
  u.addScaledVector(n, -u.dot(n));
  if (u.lengthSq() < 1e-12) u = pickU().addScaledVector(n, -pickU().dot(n));
  u.normalize();
  return { n, u, w: new THREE.Vector3().crossVectors(n, u) };
}

/** The angle (deg, right hand about `axis`, from `u`) of point `p` about the line (centre, axis). */
export function pointAngle(p: THREE.Vector3, centre: THREE.Vector3, axis: THREE.Vector3, u: THREE.Vector3): number {
  const { n, w } = planeBasis(axis, u);
  const d = p.clone().sub(centre);
  return Math.atan2(d.dot(w), d.dot(u.clone().addScaledVector(n, -u.dot(n)).normalize())) / DEG;
}

/** Where a ray meets the plane square to `axis` through `centre`, as an angle about the axis (deg, from `u`);
 *  null when the plane is seen edge on (the ray within `minCos` of lying in it) or behind the ray. */
export function rayAngle(ray: THREE.Ray, centre: THREE.Vector3, axis: THREE.Vector3, u: THREE.Vector3, minCos = 0.12): number | null {
  const n = axis.clone().normalize();
  if (Math.abs(ray.direction.clone().normalize().dot(n)) < minCos) return null;
  const hit = ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, centre), new THREE.Vector3());
  return hit ? pointAngle(hit, centre, n, u) : null;
}

/**
 * One rotary drag update. The grabbed point sat at `grab0` (deg about the axis) when the value was `v0`, and
 * turns `rate` deg per unit of the value (1 for a joint's own child, a gear's deg per joint unit, 1 for a
 * servo horn): the value that brings it onto the cursor's angle, taking the short way round.
 */
export function rotaryValue(v: number, v0: number, grab0: number, cursor: number, rate: number): number {
  const grab = grab0 + rate * (v - v0);
  return v + wrapDeg(cursor - grab) / rate;
}

/** The parameter along the line (origin + s dir) of its point closest to the ray; null when they are parallel. */
export function rayLineParam(ray: THREE.Ray, origin: THREE.Vector3, dir: THREE.Vector3): number | null {
  // closest points of two lines: p = o + s d, q = r0 + t e
  const d = dir;
  const e = ray.direction;
  const w0 = origin.clone().sub(ray.origin);
  const a = d.dot(d), b = d.dot(e), c = e.dot(e), dd = d.dot(w0), ee = e.dot(w0);
  const den = a * c - b * b;
  if (Math.abs(den) < 1e-9 * a * c) return null;
  return (b * ee - c * dd) / den;
}

/** A limit with give: past an end the value creeps on, asymptotically, at most `soft` beyond it. */
export function softClamp(v: number, min: number, max: number, soft: number): number {
  if (soft <= 0) return Math.min(max, Math.max(min, v));
  if (v > max) return max + soft * (1 - Math.exp(-(v - max) / soft));
  if (v < min) return min - soft * (1 - Math.exp(-(min - v) / soft));
  return v;
}

// ------------------------------------------------------------------ coupled limits

/** The root's `couplings` (SCHEMA.md): the dependent joint's range as a table over the driving one. */
export interface Coupling {
  joint: string;
  depends_on: string;
  table: [number, number | null, number | null][];
  limits?: [number, number];
}

/** The dependent joint's range at driving value `a` (interpolated; clamped to the table's ends); null when
 *  no row has a range. */
const sortedRows = new WeakMap<Coupling, [number, number, number][]>();
export function couplingAt(c: Coupling, a: number): [number, number] | null {
  let rows = sortedRows.get(c);
  if (!rows) {
    rows = c.table.filter((r) => r[1] !== null && r[2] !== null).sort((p, q) => p[0] - q[0]) as [number, number, number][];
    sortedRows.set(c, rows);
  }
  if (!rows.length) return null;
  if (a <= rows[0][0]) return [rows[0][1], rows[0][2]];
  const last = rows[rows.length - 1];
  if (a >= last[0]) return [last[1], last[2]];
  const k = rows.findIndex((r) => r[0] > a);
  const [a0, lo0, hi0] = rows[k - 1];
  const [a1, lo1, hi1] = rows[k];
  const t = (a - a0) / (a1 - a0);
  return [lo0 + (lo1 - lo0) * t, hi0 + (hi1 - hi0) * t];
}

/** The driving joint's range that keeps the dependent's value `b` inside the table: the stretch of
 *  [aMin, aMax] around `aCur` (when `b` is already outside at `aCur`, the whole of it: never trap a joint). */
export function couplingRangeA(c: Coupling, b: number, aCur: number, aMin: number, aMax: number, step = 0.5): [number, number] {
  const ok = (a: number) => {
    const r = couplingAt(c, a);
    return !r || (b >= r[0] - 1e-6 && b <= r[1] + 1e-6);
  };
  if (!ok(aCur)) return [aMin, aMax];
  let lo = aCur, hi = aCur;
  while (lo - step >= aMin && ok(lo - step)) lo -= step;
  while (hi + step <= aMax && ok(hi + step)) hi += step;
  if (lo - step < aMin && ok(aMin)) lo = aMin;
  if (hi + step > aMax && ok(aMax)) hi = aMax;
  return [lo, hi];
}

// ------------------------------------------------------------------ IK

export interface IKJoint { min: number; max: number; weight: number }

export interface IKOptions {
  iters?: number;
  /** Finite-difference step (joint units). */
  h?: number;
  /** Damping, relative to the Jacobian's largest column. */
  lambda?: number;
  /** Largest step per iteration (joint units). */
  maxStep?: number;
  /** Only the error square to this (unit) normal counts: the cursor's plane, so the point follows the cursor
   *  on screen whatever depth it ends at. */
  normal?: THREE.Vector3 | null;
  /** Live limits (coupled ones depend on the other joints): defaults to the joint's own. */
  limits?: (i: number, q: number[]) => [number, number];
  tol?: number;
}

/**
 * Damped least squares on a chain: q moves so fk(q) (the grabbed point) approaches `target`.
 * dq = W J^T (J W J^T + lambda^2 I)^-1 e, W = diag(weight^2) - the nearest joint (weight 1) does most of the
 * work, the ones above it help when it runs out (a limit, or a direction it cannot move in).
 */
export function solveIK(fk: (q: number[]) => THREE.Vector3, q0: number[], joints: IKJoint[], target: THREE.Vector3, o: IKOptions = {}): number[] {
  const iters = o.iters ?? 12, h = o.h ?? 0.02, lamRel = o.lambda ?? 0.06, maxStep = o.maxStep ?? 10;
  const q = q0.slice();
  const n = q.length;
  const proj = (v: THREE.Vector3) => (o.normal ? v.addScaledVector(o.normal, -v.dot(o.normal)) : v);
  const lim = (i: number): [number, number] => o.limits?.(i, q) ?? [joints[i].min, joints[i].max];
  const A = new THREE.Matrix3();
  for (let it = 0; it < iters; it++) {
    const p = fk(q);
    const e = proj(target.clone().sub(p));
    if (e.length() <= (o.tol ?? 0)) break;
    const J: THREE.Vector3[] = [];
    let big = 0;
    for (let i = 0; i < n; i++) {
      const qp = q.slice();
      // step into the range, so a joint at its end still sees its slope
      const [lo, hi] = lim(i);
      const s = q[i] + h > hi && q[i] - h >= lo ? -h : h;
      qp[i] += s;
      const col = proj(fk(qp).sub(p).divideScalar(s));
      J.push(col);
      big = Math.max(big, col.length() * joints[i].weight);
    }
    if (big < 1e-12) break;
    const lam2 = (lamRel * big) ** 2;
    const m = [lam2, 0, 0, 0, lam2, 0, 0, 0, lam2];
    for (let i = 0; i < n; i++) {
      const w = joints[i].weight ** 2;
      const c = J[i].toArray();
      for (let r = 0; r < 3; r++) for (let k = 0; k < 3; k++) m[r * 3 + k] += w * c[r] * c[k];
    }
    A.set(m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8]);
    if (Math.abs(A.determinant()) < 1e-30) break;
    const y = e.clone().applyMatrix3(A.invert());
    let moved = 0;
    for (let i = 0; i < n; i++) {
      const dq = Math.max(-maxStep, Math.min(maxStep, joints[i].weight ** 2 * J[i].dot(y)));
      const [lo, hi] = lim(i);
      const v = Math.min(hi, Math.max(lo, q[i] + dq));
      moved = Math.max(moved, Math.abs(v - q[i]));
      q[i] = v;
    }
    if (moved < 1e-5) break;
  }
  return q;
}

// ------------------------------------------------------------------ what a part moves

/** The tree as the chain walk needs it (an AsmNode is one). */
export interface ChainNode { asm: MAssembly; parent: ChainNode | null }

export interface ChainJoint<N extends ChainNode = ChainNode> { node: N; joint: MJoint }

/** Every free joint from a link down to the ground, nearest first: the link's own joint, its parent link's,
 *  and on through the assembly's mount into its parent assembly. Empty: the link is grounded. */
export function jointChain<N extends ChainNode>(node: N, link: string | null): ChainJoint<N>[] {
  const out: ChainJoint<N>[] = [];
  let n: N | null = node;
  let l = link;
  for (let guard = 0; n && guard < 64; guard++) {
    const L = l ? n.asm.links.find((x) => x.id === l) : undefined;
    const j: MJoint | undefined = L?.joint ? n.asm.joints.find((x) => x.id === L.joint) : undefined;
    if (j) {
      if (moves(j)) out.push({ node: n, joint: j });
      l = j.parent_link;
      continue;
    }
    l = n.asm.mount?.parent_link ?? null;
    n = n.parent as N | null;
  }
  return out;
}

/** A turn about the droid's own vertical centre line: a torso ring, the neck's pan - it carries everything above. */
export function isBodyYaw(j: MJoint): boolean {
  const a = j.axis;
  const len = Math.hypot(...a) || 1;
  return j.type === 'revolute' && Math.abs(a[1] / len) > 0.99 && Math.hypot(j.pivot[0], j.pivot[2]) < 1;
}

export type ChainMode = 'nearest' | 'default' | 'extend';

/**
 * The joints a drag moves. `nearest`: the part's own joint only (Alt). `extend`: every free joint to the
 * ground (Shift). `default`: the nearest, and the ones above it in the same assembly - Hunter's head is tilt
 * and roll, the neck pan and lift, the hero hand wrist and shoulder - but never on into a body turn (a ring)
 * nor out of the assembly, so a drag on the hand does not swing the droid.
 */
export function pickChain<N extends ChainNode>(chain: ChainJoint<N>[], mode: ChainMode): ChainJoint<N>[] {
  if (!chain.length || mode === 'extend') return chain;
  if (mode === 'nearest') return chain.slice(0, 1);
  const out = [chain[0]];
  for (const c of chain.slice(1)) {
    if (c.node !== chain[0].node || isBodyYaw(c.joint)) break;
    out.push(c);
  }
  return out;
}

/** What a part does when grabbed, when it is an actuator's output rather than a body on a link. */
export type PartDrive =
  /** A servo's horn, ball stud or rod: the servo turns, the push rods decide the joints. */
  | { kind: 'linkage'; linkage: string; servo: string }
  /** A pinion, spline or hub: it turns `gear.deg_per_unit` per unit of its joint (in `gear.joint_assembly`). */
  | { kind: 'gear'; gear: MGear };

/** A part or fastener id's drive in its assembly (null: it rides its link like any body). */
export function partDrive(asm: MAssembly, id: string): PartDrive | null {
  const p = asm.parts.find((x) => x.id === id);
  const f = asm.fasteners?.find((x) => x.id === id);
  const lkId = p?.linkage ?? f?.linkage;
  const lk = lkId ? asm.linkages?.find((l) => l.id === lkId) : undefined;
  if (lk) return { kind: 'linkage', linkage: lk.id, servo: lk.servo };
  const g = (asm.gears ?? []).find((x) => x.parts.includes(id) || (x.fasteners ?? []).includes(id));
  return g ? { kind: 'gear', gear: g } : null;
}
