import * as THREE from 'three';
/**
 * The mech manifest (r3x.mech.manifest v1, mech/workbench/SCHEMA.md) as Build mode reads it,
 * plus the loader: the dev server serves mech/out/ at /mech/out/ (src/workbench/mech.mjs).
 * A ChildRef is fetched and inlined, with its paths rebased onto the parent's directory.
 */

export type Vec3 = [number, number, number];
export interface MTransform { t: Vec3; q?: [number, number, number, number] }

/** How exact a part's geometry is: its own source file, the vendor's CAD, a model to spec, or a sized box. */
export type CadStatus = 'mesh' | 'source' | 'vendor' | 'parametric' | 'placeholder';

export type PartClass = 'shell' | 'mech' | 'servo' | 'fastener' | 'bearing' | 'hardware';

export interface MLink { id: string; name: string; joint: string | null }

export interface MPart {
  id: string;
  name: string;
  class: PartClass;
  link: string;
  transform: MTransform;
  mesh: string;
  export?: { stl?: string; '3mf'?: string };
  source?: { file?: string; kind?: string; entity?: string; placement?: string; fit?: string; [k: string]: unknown };
  material?: string;
  printed?: boolean;
  explode?: Vec3;
  explode_mm?: number;
  mass_g?: number;
  mass_note?: string;
  linkage?: string;
  role?: string;
  bbox: [Vec3, Vec3];
  triangles?: { display: number; source: number };
  inferred?: boolean;
  inferred_note?: string;
  note?: string;
  cad?: CadStatus;
  catalog?: string;
  /** Scaled along +Y about `anchor` by (rest_mm + joint value) / rest_mm (the neck spring with head_lift). */
  stretch?: { joint: string; axis?: Vec3; anchor: Vec3; rest_mm: number };
}

export interface MJoint {
  id: string;
  name: string;
  type: 'revolute' | 'prismatic' | 'fixed';
  parent_link: string;
  child_link: string;
  pivot: Vec3;
  axis: Vec3;
  unit: string;
  limits: { min: number; max: number };
  profile_joint: string | null;
  profile_limits?: { min: number; max: number };
  drive?: { kind?: string; servos?: string[]; linkages?: string[]; gear_ratio?: number | null; note?: string };
  zero?: { how?: string; step?: string };
  inferred?: boolean;
  inferred_note?: string;
}

export interface MLinkage {
  id: string;
  kind: 'push_rod';
  servo: string;
  horn: { link: string; centre: Vec3; axis: Vec3; radius: number; zero_dir: Vec3; ball_offset: number };
  ground: { link: string; point: Vec3 };
  rod_length: number;
  servo_range_deg?: [number, number];
  parts: string[];
  inferred?: boolean;
  inferred_note?: string;
}

export interface MSpec { type: string; thread?: string; length_mm?: number; standard?: string; mcmaster?: string; [k: string]: unknown }

export interface MFastener {
  id: string;
  spec: MSpec;
  key: string;
  joins: string[];
  link: string;
  placed: boolean;
  transform?: MTransform;
  mesh?: string;
  step: string;
  linkage?: string;
  role?: string;
  cad?: CadStatus;
  catalog?: string;
  inferred?: boolean;
  inferred_note?: string;
}

export interface MUnplaced { key: string; spec?: MSpec; count: number; note?: string }

export interface MStep {
  id: string;
  n: number;
  title: string;
  parts?: string[];
  context?: string[];
  fasteners?: string[];
  unplaced?: MUnplaced[];
  tools?: string[];
  notes?: string[];
  joint?: string;
  pose?: Record<string, number>;
  guide_page?: number;
  inferred?: boolean;
  inferred_note?: string;
}

export interface MBomLine {
  key: string;
  item: string;
  qty: number;
  category: string;
  spec?: MSpec;
  source?: string;
  parts?: string[];
  fasteners?: string[];
  inferred?: boolean;
  inferred_note?: string;
}

export interface MCheck {
  id: string;
  kind: string;
  status: 'pass' | 'warn' | 'fail' | 'explained';
  title: string;
  summary: string;
  joint?: string;
  value?: number;
  pose?: Record<string, number>;
  parts?: string[];
  assumptions?: string[];
  /** Suite timing: how long this check took (s). */
  seconds?: number;
}

export interface MVariant {
  id: string;
  group: string;
  name: string;
  default?: boolean;
  parts?: string[];
  steps?: string[];
  children?: string[];
}

export interface MBoard {
  id: string;
  name: string;
  package?: string;
  link: string;
  transform: MTransform;
  size_mm: Vec3;
  mesh?: string;
  connectors?: { id: string; kind: string; at?: Vec3; to?: Record<string, string>; note?: string }[];
}

export interface MChildRef { ref: string; id: string; name?: string; mount?: MAssembly['mount'] }

export interface MAssembly {
  id: string;
  name: string;
  description?: string;
  frame_note?: string;
  mount?: {
    parent_link?: string; transform?: MTransform; mount_node?: string; inferred?: boolean; note?: string;
    /** This child is one option of a variant group of its parent (only the selected option shows). */
    variant?: { group: string; id: string; default?: boolean };
  };
  guide?: { title?: string; pages?: number[] };
  links: MLink[];
  parts: MPart[];
  joints: MJoint[];
  linkages?: MLinkage[];
  fasteners?: MFastener[];
  steps?: MStep[];
  bom?: MBomLine[];
  bom_rollup?: MBomLine[];
  checks?: MCheck[];
  children?: (MAssembly | MChildRef)[];
  variants?: MVariant[];
  electronics?: MBoard[];
  notes?: string[];
  /** Set by the loader: the URL directory every relative path in this node resolves against. */
  base?: string;
}

export interface Manifest {
  schema: string;
  version: number;
  generated_at: string;
  generator?: string;
  root: MAssembly;
}

export interface IndexEntry { id: string; name: string; manifest: string; built?: string }

export const MECH_BASE = '/mech/out/';

const isRef = (c: MAssembly | MChildRef): c is MChildRef => 'ref' in c && !('parts' in c);

/** Resolve `rel` against a directory URL. */
export function joinUrl(base: string, rel: string): string {
  return new URL(rel, new URL(base, 'http://x')).pathname;
}

/** Stamp every node with its base URL and inline ChildRefs (depth-limited). */
export async function resolveTree(node: MAssembly, base: string, fetchJson: (url: string) => Promise<Manifest>, depth = 0): Promise<MAssembly> {
  node.base = base;
  // Optional lists default to empty: writers may omit them (SCHEMA.md "Unknown keys"; empty = omitted).
  node.links ??= [];
  node.parts ??= [];
  node.joints ??= [];
  const kids: MAssembly[] = [];
  for (const c of node.children ?? []) {
    if (isRef(c)) {
      if (depth > 6) continue;
      const url = joinUrl(base, c.ref);
      try {
        const m = await fetchJson(url);
        const child = m.root;
        if (c.mount) child.mount = c.mount;
        if (c.name) child.name = c.name;
        kids.push(await resolveTree(child, url.replace(/[^/]*$/, ''), fetchJson, depth + 1));
      } catch {
        kids.push({ id: c.id, name: `${c.name ?? c.id} (not built)`, links: [], parts: [], joints: [], base, mount: c.mount });
      }
    } else {
      kids.push(await resolveTree(c, base, fetchJson, depth + 1));
    }
  }
  node.children = kids;
  return node;
}

export async function loadManifest(url: string): Promise<Manifest> {
  const get = async (u: string) => {
    const r = await fetch(u, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${u}: ${r.status}`);
    return (await r.json()) as Manifest;
  };
  const m = await get(url);
  if (m.schema !== 'r3x.mech.manifest') throw new Error(`${url}: not a mech manifest`);
  if (m.version !== 1) throw new Error(`${url}: manifest version ${m.version}, this panel reads 1`);
  m.root = await resolveTree(m.root, url.replace(/[^/]*$/, ''), get);
  return m;
}

export async function loadIndex(): Promise<IndexEntry[]> {
  try {
    const r = await fetch(`${MECH_BASE}index.json`, { cache: 'no-store' });
    if (!r.ok) return [];
    return ((await r.json()) as { assemblies: IndexEntry[] }).assemblies ?? [];
  } catch {
    return [];
  }
}

/** Depth-first list of every assembly with its path from the root (for the tree and breadcrumb). */
export function flatten(root: MAssembly): { node: MAssembly; path: MAssembly[] }[] {
  const out: { node: MAssembly; path: MAssembly[] }[] = [];
  const walk = (n: MAssembly, path: MAssembly[]) => {
    out.push({ node: n, path: [...path, n] });
    for (const c of (n.children ?? []) as MAssembly[]) walk(c, [...path, n]);
  };
  walk(root, []);
  return out;
}

/** Every variant option on `a`: its own part variants plus children mounted as options
 * (`mount.variant`, e.g. the droid's head_mech: Hunter's gimbal or Anderson's tilt). */
export function variantOptions(a: MAssembly): { group: string; id: string; name: string; default: boolean; child?: MAssembly }[] {
  const out: { group: string; id: string; name: string; default: boolean; child?: MAssembly }[] = [];
  for (const v of a.variants ?? []) out.push({ group: v.group, id: v.id, name: v.name, default: !!v.default });
  for (const c of (a.children ?? []) as MAssembly[]) {
    const v = c.mount?.variant;
    if (v) out.push({ group: v.group, id: v.id, name: c.name ?? v.id, default: !!v.default, child: c });
  }
  return out;
}

/** Variant groups -> the selected option id (defaults applied). */
export function defaultVariants(a: MAssembly): Record<string, string> {
  const pick: Record<string, string> = {};
  for (const v of variantOptions(a)) {
    if (!(v.group in pick) || v.default) pick[v.group] = v.id;
  }
  return pick;
}

/** Parts hidden by the current variant picks (part options on this assembly). Child options are
 * whole assemblies: see hiddenAssemblies (by node, since part ids may repeat across options). */
export function hiddenByVariants(a: MAssembly, pick: Record<string, string>): Set<string> {
  const hide = new Set<string>();
  for (const v of a.variants ?? []) if (pick[v.group] !== v.id) for (const p of v.parts ?? []) hide.add(p);
  for (const v of a.variants ?? []) if (pick[v.group] === v.id) for (const p of v.parts ?? []) hide.delete(p);
  return hide;
}

/** Assemblies under an unpicked child option (they and their subtrees are out of the model). */
export function hiddenAssemblies(root: MAssembly, pick: Record<string, string>): Set<MAssembly> {
  const out = new Set<MAssembly>();
  for (const { node } of flatten(root)) {
    for (const o of variantOptions(node)) {
      if (o.child && pick[o.group] !== o.id) for (const { node: n } of flatten(o.child)) out.add(n);
    }
  }
  return out;
}

/** The step in which each part first appears (parts in no step: -1). */
export function firstStep(a: MAssembly): Map<string, number> {
  const out = new Map<string, number>();
  (a.steps ?? []).forEach((s, i) => {
    for (const p of s.parts ?? []) if (!out.has(p)) out.set(p, i);
  });
  return out;
}


/** The one mesh of a display GLB as plain float geometry in the part's frame: the workbench writes
 * positions quantized (KHR_mesh_quantization: int16, dequantized by the node's translation + scale),
 * so the node's transform is baked in here; normals are left to the reader. */
export function meshGeometry(scene: THREE.Object3D, url = ''): THREE.BufferGeometry {
  let mesh: THREE.Mesh | null = null;
  scene.traverse((o) => {
    if (!mesh && (o as THREE.Mesh).isMesh) mesh = o as THREE.Mesh;
  });
  if (!mesh) throw new Error(`${url}: no mesh`);
  const m = mesh as THREE.Mesh;
  m.updateWorldMatrix(true, false);
  const src = m.geometry as THREE.BufferGeometry;
  const p = src.getAttribute('position');
  const out = new Float32Array(p.count * 3);
  for (let i = 0; i < p.count; i++) {
    out[i * 3] = p.getX(i);
    out[i * 3 + 1] = p.getY(i);
    out[i * 3 + 2] = p.getZ(i);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(out, 3));
  if (src.index) g.setIndex(src.index.clone());
  g.applyMatrix4(m.matrixWorld);
  return g;
}
