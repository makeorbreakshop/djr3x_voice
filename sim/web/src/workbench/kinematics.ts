/**
 * Build mode's kinematics, the same two functions as mech/workbench/kinematics.py: a link's
 * matrix for a pose (joint pivots/axes are in the assembly frame at the zero pose), and the
 * closed-form push-rod solve (mech/workbench/SCHEMA.md "Linkage"). Keep them in step.
 */

import * as THREE from 'three';
import type { MJoint, MLink, MLinkage } from './manifest';

export type Pose = Record<string, number>;

const DEG = Math.PI / 180;

export function jointMatrix(j: MJoint, value: number, out = new THREE.Matrix4()): THREE.Matrix4 {
  if (j.type === 'revolute') {
    const p = new THREE.Vector3(...j.pivot);
    const axis = new THREE.Vector3(...j.axis).normalize();
    return out
      .makeTranslation(p.x, p.y, p.z)
      .multiply(new THREE.Matrix4().makeRotationAxis(axis, value * DEG))
      .multiply(new THREE.Matrix4().makeTranslation(-p.x, -p.y, -p.z));
  }
  if (j.type === 'prismatic') {
    const a = new THREE.Vector3(...j.axis).normalize().multiplyScalar(value);
    return out.makeTranslation(a.x, a.y, a.z);
  }
  return out.identity();
}

/** Every link's matrix (assembly frame) for a pose; missing joints are at 0. */
export function linkMatrices(links: MLink[], joints: MJoint[], pose: Pose): Map<string, THREE.Matrix4> {
  const out = new Map<string, THREE.Matrix4>();
  const byLink = new Map(links.map((l) => [l.id, l]));
  const byJoint = new Map(joints.map((j) => [j.id, j]));
  const get = (id: string): THREE.Matrix4 => {
    const have = out.get(id);
    if (have) return have;
    const l = byLink.get(id);
    const j = l?.joint ? byJoint.get(l.joint) : undefined;
    const m = j ? get(j.parent_link).clone().multiply(jointMatrix(j, pose[j.id] ?? 0)) : new THREE.Matrix4();
    out.set(id, m);
    return m;
  };
  for (const l of links) get(l.id);
  return out;
}

export interface RodSolution {
  /** Servo angle, degrees from the horn's zero direction (right hand about the horn axis). */
  servoDeg: number;
  /** Ball centres: on the horn, and on the ground side. */
  a: THREE.Vector3;
  b: THREE.Vector3;
}

/** The horn's in-plane basis: axis n, zero direction u, w = n x u. */
export function hornBasis(lk: MLinkage) {
  const n = new THREE.Vector3(...lk.horn.axis).normalize();
  const u = new THREE.Vector3(...lk.horn.zero_dir);
  u.addScaledVector(n, -u.dot(n)).normalize();
  const w = new THREE.Vector3().crossVectors(n, u);
  return { n, u, w };
}

/** Closed form: a cos th + b sin th = c. Null when the pose is out of the rod's reach. */
export function solveRod(lk: MLinkage, hornM: THREE.Matrix4, groundM: THREE.Matrix4): RodSolution | null {
  const { n, u, w } = hornBasis(lk);
  const rot = new THREE.Matrix3().setFromMatrix4(hornM);
  const c = new THREE.Vector3(...lk.horn.centre).applyMatrix4(hornM);
  const n2 = n.clone().applyMatrix3(rot);
  const u2 = u.clone().applyMatrix3(rot);
  const w2 = w.clone().applyMatrix3(rot);
  const b = new THREE.Vector3(...lk.ground.point).applyMatrix4(groundM);
  const d = c.clone().addScaledVector(n2, lk.horn.ball_offset).sub(b);
  const r = lk.horn.radius;
  const L = lk.rod_length;
  const A = 2 * r * d.dot(u2);
  const B = 2 * r * d.dot(w2);
  const C = L * L - d.lengthSq() - r * r;
  const h = Math.hypot(A, B);
  if (h < 1e-9 || Math.abs(C) > h) return null;
  const base = Math.atan2(B, A);
  const off = Math.acos(C / h);
  const wrap = (t: number) => Math.atan2(Math.sin(t), Math.cos(t));
  const th = [wrap(base + off), wrap(base - off)].sort((p, q) => Math.abs(p) - Math.abs(q))[0];
  const a = c
    .clone()
    .addScaledVector(n2, lk.horn.ball_offset)
    .addScaledVector(u2, r * Math.cos(th))
    .addScaledVector(w2, r * Math.sin(th));
  return { servoDeg: th / DEG, a, b };
}

/** The horn's matrix: its link's, turned about the horn axis by the servo angle. */
export function hornMatrix(lk: MLinkage, hornLinkM: THREE.Matrix4, servoDeg: number): THREE.Matrix4 {
  const c = new THREE.Vector3(...lk.horn.centre);
  const n = new THREE.Vector3(...lk.horn.axis).normalize();
  return hornLinkM
    .clone()
    .multiply(new THREE.Matrix4().makeTranslation(c.x, c.y, c.z))
    .multiply(new THREE.Matrix4().makeRotationAxis(n, servoDeg * DEG))
    .multiply(new THREE.Matrix4().makeTranslation(-c.x, -c.y, -c.z));
}

/** Moves the rod's zero-pose placement (ball a0 -> b0) onto the solved balls (a -> b). */
export function rodMatrix(a0: THREE.Vector3, b0: THREE.Vector3, a: THREE.Vector3, b: THREE.Vector3): THREE.Matrix4 {
  const q = new THREE.Quaternion().setFromUnitVectors(b0.clone().sub(a0).normalize(), b.clone().sub(a).normalize());
  return new THREE.Matrix4()
    .makeTranslation(a.x, a.y, a.z)
    .multiply(new THREE.Matrix4().makeRotationFromQuaternion(q))
    .multiply(new THREE.Matrix4().makeTranslation(-a0.x, -a0.y, -a0.z));
}
