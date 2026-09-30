/**
 * Required servo torque, live, from the CAD mass properties (mechrig/model.ts).
 *
 * What is computed (and nothing more):
 * - Rigid-body inverse dynamics of the link tree by virtual work: for each joint, the sum over
 *   every link it carries of J_v . m (a_com - g) + J_w . (I a + w x I w), with J the joint's
 *   axis (and lever to the link's COM). Gravity is exact per pose; a, w and alpha come from
 *   the trajectory itself (second differences of each link's COM and orientation over the
 *   last three samples), so coupling between joints is included, not just each joint's own
 *   acceleration.
 * - Through the drive: gear/direct = joint torque / ratio / efficiency; the head rack = force
 *   x pinion lever; push rods = J^-T (servo-angle Jacobian of the closed-form rod solve, taken
 *   at the current pose, so leverage changes with the pose).
 * - Lazy-susan rolling friction on vertical turntables while they move (0.02 x weight x
 *   110 mm, as r3xmech's phase-A check).
 * - Load = |servo torque| / stall torque at the supply voltage (servos.rs ratings); the
 *   motion-control 70 % rule flags anything above 0.7.
 *
 * Not modelled: gear and rod friction beyond the efficiency factor, backlash, cable drag,
 * contact, compliance, the servo's own speed-torque curve, the printed parts' real density
 * distribution (uniform solids, so inertia is a lower bound for infilled parts).
 */

import * as THREE from 'three';
import { solveRod } from '../workbench/kinematics';
import { jointMatrices, jointOrder, MECH, subtrees, type MechBlock, type MechDrive } from './model';

export const RULE = 0.7;
const G = new THREE.Vector3(0, -9.80665, 0);
const MU_SUSAN = 0.02;
const R_SUSAN = 0.11;
const RAD = 180 / Math.PI;

export interface ServoLoad {
  servo: string;
  model: string | null;
  joints: string[];
  /** Servo torque, N m (signed: + drives the joint positive). */
  nm: number;
  /** Gravity-only (holding) part. */
  staticNm: number;
  stallNm: number;
  /** |nm| / stall. */
  load: number;
  over: boolean;
}

interface LinkData { id: string; joint: string; kg: number; com0: THREE.Vector3; I0: THREE.Matrix3 }
interface Sample { t: number; values: Record<string, number>; com: THREE.Vector3[]; R: THREE.Matrix3[]; jm: Map<string, THREE.Matrix4> }

const inertia = (v: number[]) => new THREE.Matrix3().set(v[0], v[3], v[4], v[3], v[1], v[5], v[4], v[5], v[2]);

/** Rotation vector of R (log map), radians. */
function logRot(R: THREE.Matrix3, out = new THREE.Vector3()): THREE.Vector3 {
  const e = R.elements; // column-major
  const tr = e[0] + e[4] + e[8];
  const c = Math.min(1, Math.max(-1, (tr - 1) / 2));
  const th = Math.acos(c);
  const v = out.set(e[5] - e[7], e[6] - e[2], e[1] - e[3]);
  const s = Math.sin(th);
  return s < 1e-9 ? v.multiplyScalar(0.5) : v.multiplyScalar(th / (2 * s));
}

export class TorqueModel {
  readonly order: string[];
  readonly links: LinkData[];
  readonly drives: MechDrive[];
  private readonly sub: Map<string, Set<string>>;
  private hist: Sample[] = [];
  private dynSmooth = new Map<string, number>();
  /** Low-pass on the acceleration terms (s): frame-rate differences are noisy. 0 = raw. */
  smoothing = 0.05;

  constructor(readonly mech: MechBlock = MECH) {
    this.order = jointOrder(mech);
    this.sub = subtrees(mech);
    this.links = mech.links
      .filter((l) => l.joint && l.kg > 0 && l.com)
      .map((l) => ({
        id: l.id, joint: l.joint!, kg: l.kg,
        com0: new THREE.Vector3(...l.com!).multiplyScalar(0.001),
        I0: inertia(l.inertia ?? [0, 0, 0, 0, 0, 0]),
      }));
    this.drives = mech.drives.filter((d) => d.stall_nm);
  }

  reset() {
    this.hist = [];
    this.dynSmooth.clear();
  }

  private sample(values: Record<string, number>, t: number): Sample {
    const jm = jointMatrices(values, this.order, this.mech);
    const com: THREE.Vector3[] = [];
    const R: THREE.Matrix3[] = [];
    for (const l of this.links) {
      const m = jm.get(l.joint)!;
      // body matrices are mm: rotate the COM (m), translate by the mm offset / 1000
      const r3 = new THREE.Matrix3().setFromMatrix4(m);
      const t3 = new THREE.Vector3().setFromMatrixPosition(m).multiplyScalar(0.001);
      com.push(l.com0.clone().applyMatrix3(r3).add(t3));
      R.push(r3);
    }
    return { t, values: { ...values }, com, R, jm };
  }

  /** Generalised force each joint must supply (N m, or N for the lift) at sample `s`. */
  private jointForces(s: Sample, acc: { a: THREE.Vector3; w: THREE.Vector3; al: THREE.Vector3 }[] | null): Map<string, number> {
    const out = new Map<string, number>();
    const f = new THREE.Vector3();
    const lever = new THREE.Vector3();
    const Iw = new THREE.Matrix3();
    const tmp = new THREE.Vector3();
    const tmp2 = new THREE.Vector3();
    for (const n of this.order) {
      const j = this.mech.joints[n];
      if (j.type === 'fixed') continue;
      const pm = j.parent ? s.jm.get(j.parent)! : new THREE.Matrix4();
      const pr = new THREE.Matrix3().setFromMatrix4(pm);
      const axis = new THREE.Vector3(...j.axis).applyMatrix3(pr).normalize();
      const pivot = new THREE.Vector3(...j.pivot).applyMatrix4(pm).multiplyScalar(0.001);
      const below = this.sub.get(n)!;
      let tau = 0;
      let weight = 0;
      this.links.forEach((l, i) => {
        if (!below.has(l.joint)) return;
        weight += l.kg * 9.80665;
        f.copy(G).multiplyScalar(-l.kg); // m (a - g), a added below
        if (acc) f.addScaledVector(acc[i].a, l.kg);
        if (j.type === 'prismatic') {
          tau += axis.dot(f);
          return;
        }
        lever.copy(s.com[i]).sub(pivot);
        tau += tmp.crossVectors(lever, f).dot(axis);
        if (acc) {
          // I_w = R I0 R^T
          Iw.copy(s.R[i]).multiply(l.I0).multiply(s.R[i].clone().transpose());
          const Ia = tmp.copy(acc[i].al).applyMatrix3(Iw);
          const Iww = tmp2.copy(acc[i].w).applyMatrix3(Iw);
          Ia.add(Iww.crossVectors(acc[i].w, Iww));
          tau += Ia.dot(axis);
        }
      });
      // Turntables on a lazy susan: rolling friction while they turn.
      if (acc && j.type === 'revolute' && Math.abs(axis.y) > 0.99 && this.hist.length >= 2) {
        const prev = this.hist[this.hist.length - 2];
        const dq = (s.values[n] ?? 0) - (prev.values[n] ?? 0);
        const dt = s.t - prev.t;
        if (dt > 0 && Math.abs(dq / dt) > 0.5) tau += Math.sign(dq) * MU_SUSAN * weight * R_SUSAN;
      }
      out.set(n, tau);
    }
    return out;
  }

  /** d(servo deg)/d(joint unit) for a drive's linkages over its joints (rows x cols), at a pose. */
  jacobian(linkages: string[], joints: string[], values: Record<string, number>, eps = 0.25): number[][] | null {
    const J = linkages.map(() => joints.map(() => 0));
    const lks = linkages.map((id) => this.mech.linkages[id]);
    if (lks.some((l) => !l)) return null;
    const linkOf = new Map(this.mech.links.map((l) => [l.id, l.joint]));
    for (let c = 0; c < joints.length; c++) {
      const ang: number[][] = [];
      for (const d of [eps, -eps]) {
        const v = { ...values, [joints[c]]: (values[joints[c]] ?? 0) + d };
        const jm = jointMatrices(v, this.order, this.mech);
        const I = new THREE.Matrix4();
        const lm = (id: string) => {
          const jn = linkOf.get(id);
          return jn ? jm.get(jn) ?? I : I;
        };
        const row: number[] = [];
        for (const lk of lks) {
          const s = solveRod(lk, lm(lk.horn.link), lm((lk.ground as { link: string }).link));
          if (!s) return null;
          row.push(s.servoDeg);
        }
        ang.push(row);
      }
      for (let r = 0; r < lks.length; r++) J[r][c] = (ang[0][r] - ang[1][r]) / (2 * eps);
    }
    return J;
  }

  /** Servo loads for joint forces `q` at pose `values`. */
  private servoLoads(q: Map<string, number>, qs: Map<string, number>, values: Record<string, number>): ServoLoad[] {
    const out: ServoLoad[] = [];
    const done = new Set<string>();
    for (const d of this.drives) {
      if (done.has(d.servo)) continue;
      const eta = d.eta || 1;
      const push = (dd: MechDrive, nm: number, st: number) => {
        done.add(dd.servo);
        const stall = dd.stall_nm!;
        out.push({ servo: dd.servo, model: dd.model, joints: dd.joints, nm, staticNm: st, stallNm: stall,
          load: Math.abs(nm) / stall, over: Math.abs(nm) / stall > RULE });
      };
      if (d.kind === 'push_rod_pair') {
        const pair = this.drives.filter((x) => x.kind === 'push_rod_pair' && x.joints.join() === d.joints.join());
        const J = this.jacobian(pair.map((x) => x.linkage!), d.joints, values);
        if (!J || pair.length !== 2 || d.joints.length !== 2) {
          pair.forEach((x) => push(x, NaN, NaN));
          continue;
        }
        // J^T tau_s = tau_j  (servo deg per joint deg, so torques convert per degree = per radian)
        const solve = (t: number[]) => {
          const [[a, b], [c, e]] = [[J[0][0], J[1][0]], [J[0][1], J[1][1]]]; // J^T
          const det = a * e - b * c;
          if (Math.abs(det) < 1e-6) return [NaN, NaN];
          return [(e * t[0] - b * t[1]) / det, (-c * t[0] + a * t[1]) / det];
        };
        const tj = d.joints.map((n) => q.get(n) ?? 0);
        const ts = d.joints.map((n) => qs.get(n) ?? 0);
        const r = solve(tj);
        const rs = solve(ts);
        pair.forEach((x, i) => push(x, r[i] / eta, rs[i] / eta));
        continue;
      }
      const n = d.joints[0];
      const tj = q.get(n) ?? 0;
      const ts = qs.get(n) ?? 0;
      let per: number; // joint effort per servo N m
      if (d.mm_per_servo_deg) {
        per = (d.mm_per_servo_deg * RAD) / 1000; // m of travel per servo radian: N -> N m
        push(d, (tj * per) / eta, (ts * per) / eta);
        continue;
      }
      if (d.kind === 'push_rod' && d.linkage) {
        const J = this.jacobian([d.linkage], [n], values);
        const g = J ? J[0][0] : NaN;
        push(d, tj / g / eta, ts / g / eta);
        continue;
      }
      const ratio = d.servo_deg_per_unit || 1;
      push(d, tj / ratio / eta, ts / ratio / eta);
    }
    return out;
  }

  /**
   * Feed one pose (performer frame) at time `t` (s); returns every servo's load. The first
   * two samples (and after a gap over 0.25 s) are gravity-only.
   */
  update(values: Record<string, number>, t: number): ServoLoad[] {
    const last = this.hist[this.hist.length - 1];
    if (last && (t <= last.t || t - last.t > 0.25)) this.reset();
    const s = this.sample(values, t);
    this.hist.push(s);
    if (this.hist.length > 3) this.hist.shift();
    const qs = this.jointForces(s, null);
    let q = qs;
    if (this.hist.length === 3) {
      const [s0, s1, s2] = this.hist;
      const h0 = s1.t - s0.t;
      const h1 = s2.t - s1.t;
      const acc = this.links.map((_, i) => {
        // non-uniform second difference; angular rates from relative rotations
        const v0 = s1.com[i].clone().sub(s0.com[i]).divideScalar(h0);
        const v1 = s2.com[i].clone().sub(s1.com[i]).divideScalar(h1);
        const a = v1.clone().sub(v0).divideScalar((h0 + h1) / 2);
        const w0 = logRot(s1.R[i].clone().multiply(s0.R[i].clone().transpose())).divideScalar(h0);
        const w1 = logRot(s2.R[i].clone().multiply(s1.R[i].clone().transpose())).divideScalar(h1);
        const al = w1.clone().sub(w0).divideScalar((h0 + h1) / 2);
        return { a, w: w1, al };
      });
      const qd = this.jointForces(s2, acc);
      const k = this.smoothing > 0 ? Math.min(1, h1 / (this.smoothing + h1)) : 1;
      q = new Map();
      for (const [n, v] of qd) {
        const dyn = v - (qs.get(n) ?? 0);
        const sm = (this.dynSmooth.get(n) ?? dyn) + k * (dyn - (this.dynSmooth.get(n) ?? dyn));
        this.dynSmooth.set(n, sm);
        q.set(n, (qs.get(n) ?? 0) + sm);
      }
    }
    return this.servoLoads(q, qs, values);
  }
}

// ------------------------------------------------------------------ clip analysis (Studio lint)

export interface ClipTrack { joint: string; value: (u: number) => number }

export interface ClipLoad { servo: string; joints: string[]; peak: number; at: number; peakNm: number; stallNm: number }

/**
 * Peak servo load over a clip played from the rest pose (tracks give each joint's value at
 * clip time u; joints without a track stay at 0). Sampled at `hz`, no smoothing.
 */
export function clipLoads(tracks: ClipTrack[], duration: number, hz = 100, base: Record<string, number> = {}): ClipLoad[] {
  const tm = new TorqueModel();
  tm.smoothing = 0;
  const peak = new Map<string, ClipLoad>();
  const n = Math.max(2, Math.ceil(duration * hz));
  for (let i = 0; i <= n + 2; i++) {
    const u = Math.min(duration, i / hz);
    const v: Record<string, number> = { ...base };
    for (const tr of tracks) v[tr.joint] = (base[tr.joint] ?? 0) + tr.value(u);
    for (const l of tm.update(v, i / hz)) {
      if (!Number.isFinite(l.load)) continue;
      const p = peak.get(l.servo);
      if (!p || l.load > p.peak) peak.set(l.servo, { servo: l.servo, joints: l.joints, peak: l.load, at: u, peakNm: Math.abs(l.nm), stallNm: l.stallNm });
    }
  }
  return [...peak.values()].sort((a, b) => b.peak - a.peak);
}
