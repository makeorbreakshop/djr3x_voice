/**
 * The mech model as the sim uses it: the `mech` block of profiles/r3x/robot.generated.json,
 * written by `mech/.venv/bin/python -m rigsync` from the built droid manifest (one rig, from
 * the mechanical model). Committed, so the torque model runs in every build; the meshes
 * (mech/out/) are only on the dev server.
 *
 * Body frame (show/SPEC.md): mm, +Y up, +Z front, +X the droid's left, rest pose. Joint
 * values are the performer's (profile names, deg or mm).
 */

import * as THREE from 'three';
import generated from '../../../../profiles/r3x/robot.generated.json';
import type { MLinkage } from '../workbench/manifest';

export type Vec3 = [number, number, number];

export interface MechJoint {
  gid: string;
  type: 'revolute' | 'prismatic' | 'fixed';
  parent: string | null;
  pivot: Vec3;
  axis: Vec3;
  child_link: string;
  mech_limits: { min: number; max: number };
  drive: {
    kind: string;
    servos: string[];
    servo_model: string | null;
    servo_deg_per_unit: number | null;
    mm_per_servo_deg: number | null;
    eta: number;
    linkages: string[];
  };
  first_contact: { min: number | null; max: number | null; source: string } | null;
  driven: boolean;
}

export interface MechDrive {
  servo: string;
  model: string | null;
  kind: string;
  joints: string[];
  linkage: string | null;
  servo_deg_per_unit?: number | null;
  mm_per_servo_deg?: number | null;
  eta: number;
  stall_nm: number | null;
  no_load_dps: number | null;
}

export interface MechLink {
  id: string;
  name: string;
  joint: string | null;
  kg: number;
  com: Vec3 | null;
  /** [Ixx, Iyy, Izz, Ixy, Ixz, Iyz] kg m^2 about the COM, body axes. */
  inertia: number[] | null;
}

export interface MechBlock {
  generated_at: string;
  supply_volts: number;
  joints: Record<string, MechJoint>;
  drives: MechDrive[];
  links: MechLink[];
  linkages: Record<string, MLinkage & { horn: MLinkage['horn'] & { link: string } }>;
  total_kg: number;
  manifest: { path: string; generated_at: string; assemblies: string[] };
}

export interface GeneratedProfile {
  joints: { name: string; animation: { min: number; max: number }; soft: { min: number; max: number }; v_max: number }[];
  mech: MechBlock;
}

export const GENERATED = generated as unknown as GeneratedProfile;
export const MECH: MechBlock = GENERATED.mech;

const DEG = Math.PI / 180;

/** Joint names parent-first (so a single pass computes every joint's matrix). */
export function jointOrder(mech: MechBlock = MECH): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const visit = (n: string) => {
    if (seen.has(n)) return;
    seen.add(n);
    const p = mech.joints[n]?.parent;
    if (p && mech.joints[p]) visit(p);
    out.push(n);
  };
  Object.keys(mech.joints).forEach(visit);
  return out;
}

/** The matrix a joint adds at value v (body frame, mm), as workbench/kinematics.ts. */
export function localMatrix(j: MechJoint, v: number, out = new THREE.Matrix4()): THREE.Matrix4 {
  if (j.type === 'revolute') {
    const [px, py, pz] = j.pivot;
    const ax = new THREE.Vector3(...j.axis).normalize();
    const r = new THREE.Matrix4().makeRotationAxis(ax, v * DEG);
    // T(p) R T(-p)
    const e = r.elements;
    const tx = px - (e[0] * px + e[4] * py + e[8] * pz);
    const ty = py - (e[1] * px + e[5] * py + e[9] * pz);
    const tz = pz - (e[2] * px + e[6] * py + e[10] * pz);
    return out.copy(r).setPosition(tx, ty, tz);
  }
  if (j.type === 'prismatic') {
    const a = new THREE.Vector3(...j.axis).normalize().multiplyScalar(v);
    return out.makeTranslation(a.x, a.y, a.z);
  }
  return out.identity();
}

/** Every mech joint's body-frame matrix (the transform of the links it moves) for a pose. */
export function jointMatrices(values: Record<string, number>, order = jointOrder(), mech: MechBlock = MECH): Map<string, THREE.Matrix4> {
  const out = new Map<string, THREE.Matrix4>();
  for (const n of order) {
    const j = mech.joints[n];
    const m = localMatrix(j, values[n] ?? 0);
    const p = j.parent ? out.get(j.parent) : undefined;
    out.set(n, p ? p.clone().multiply(m) : m);
  }
  return out;
}

/** Every link's body-frame matrix, keyed by link id (ground links: identity). */
export function linkMatricesFor(joints: Map<string, THREE.Matrix4>, mech: MechBlock = MECH): Map<string, THREE.Matrix4> {
  const I = new THREE.Matrix4();
  const out = new Map<string, THREE.Matrix4>();
  for (const l of mech.links) out.set(l.id, l.joint ? joints.get(l.joint) ?? I : I);
  return out;
}

/** Profile joint -> joints below it (itself included). */
export function subtrees(mech: MechBlock = MECH): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  for (const n of Object.keys(mech.joints)) {
    let cur: string | null = n;
    let guard = 0;
    while (cur && guard++ < 64) {
      if (!out.has(cur)) out.set(cur, new Set());
      out.get(cur)!.add(n);
      cur = mech.joints[cur]?.parent ?? null;
    }
  }
  return out;
}
