/**
 * The mechanical assembly as a rig for Show, Bench and Studio: the built droid manifest
 * (mech/out/r3x_droid, Hunter's head by ChildRef) posed every frame from the performer's joint
 * values - the same frames the visual rig takes - with the closed linkages (head push-rod
 * pair, visor rod) solved per frame by the workbench's own kinematics (workbench/kinematics.ts).
 *
 * Draw cost is kept flat: every static part of a link is merged into one mesh per material
 * class, each linkage's horn and rod parts into one mesh each, and fasteners are instanced per
 * (link, fastener mesh). The full droid is ~60 draw calls whatever its part count.
 *
 * Sim only, like Build: it reads frames, never sends a command. The meshes come from the dev
 * server's /mech/out/ (gitignored third-party geometry), so a static build shows a notice.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { tameHighlights } from '../look';
import { gearMatrix, hornMatrix, linkMatrices, rodMatrix, solveRod } from '../workbench/kinematics';
import { hiddenByVariants, defaultVariants, joinUrl, loadManifest, meshGeometry, MECH_BASE, type MAssembly, type MGear, type MLinkage, type PartClass } from '../workbench/manifest';

export type ModelView = 'visual' | 'mechanical' | 'xray';

const LOOK: Record<PartClass, { color: number; metalness: number; roughness: number }> = {
  shell: { color: 0xd8d2c4, metalness: 0.0, roughness: 0.62 },
  mech: { color: 0xc9773d, metalness: 0.0, roughness: 0.55 },
  servo: { color: 0x2c3444, metalness: 0.2, roughness: 0.5 },
  hardware: { color: 0xb8b09a, metalness: 0.75, roughness: 0.35 },
  bearing: { color: 0x9aa1ab, metalness: 0.85, roughness: 0.3 },
  fastener: { color: 0x34353a, metalness: 0.7, roughness: 0.4 },
};

interface Node {
  asm: MAssembly;
  group: THREE.Group;
  links: Map<string, THREE.Group>;
  /** profile joint -> this assembly's joint id */
  byProfile: Map<string, string>;
  pose: Record<string, number>;
  rodZero: Map<string, { a: THREE.Vector3; b: THREE.Vector3 }>;
  holders: { lk: MLinkage; role: 'horn' | 'rod'; obj: THREE.Object3D }[];
  /** Gear parts (pinions, splines), turned about their axle by their joint (SCHEMA.md "Gear"). */
  gears: { gear: MGear; obj: THREE.Object3D }[];
}

export class MechView {
  readonly root = new THREE.Group();
  view: ModelView = 'visual';
  status: 'idle' | 'loading' | 'ready' | 'error' = 'idle';
  error = '';
  stats = { parts: 0, fasteners: 0, drawCalls: 0, triangles: 0, loadMs: 0 };
  /** Linkages out of reach at the last pose (the pose is past what the rods can do). */
  unreachable: string[] = [];
  private nodes: Node[] = [];
  private shellMat = new THREE.MeshStandardMaterial({ ...LOOK.shell, side: THREE.DoubleSide });
  private mats = new Map<PartClass, THREE.MeshStandardMaterial>();
  private lastKey = '';
  private listeners = new Set<() => void>();

  constructor(scene: THREE.Scene, readonly url = `${MECH_BASE}r3x_droid/manifest.json`) {
    this.root.name = 'mechview';
    this.root.scale.setScalar(0.001); // mm -> m
    this.root.visible = false;
    scene.add(this.root);
    for (const c of Object.keys(LOOK) as PartClass[]) {
      this.mats.set(c, c === 'shell' ? this.shellMat : new THREE.MeshStandardMaterial({ ...LOOK[c], side: THREE.DoubleSide }));
    }
  }

  onChange(fn: () => void) {
    this.listeners.add(fn);
  }

  private emit() {
    for (const f of this.listeners) f();
  }

  /** Visual hides the mech; Mechanical draws it solid; X-ray ghosts its shells. Loads on first use. */
  setView(v: ModelView) {
    this.view = v;
    this.root.visible = v !== 'visual' && this.status === 'ready';
    const x = v === 'xray';
    this.shellMat.transparent = x;
    this.shellMat.opacity = x ? 0.12 : 1;
    this.shellMat.depthWrite = !x;
    this.shellMat.needsUpdate = true;
    if (v !== 'visual' && this.status === 'idle') void this.load();
    this.emit();
  }

  /** True while the mech stands in for the droid. */
  get showing() {
    return this.view !== 'visual' && this.status === 'ready';
  }

  async load() {
    this.status = 'loading';
    this.emit();
    const t0 = performance.now();
    try {
      const m = await loadManifest(this.url);
      const loader = new GLTFLoader();
      const cache = new Map<string, Promise<THREE.BufferGeometry>>();
      const geo = (u: string) => {
        let g = cache.get(u);
        if (!g) {
          g = loader.loadAsync(u).then((gltf) => clean(meshGeometry(gltf.scene, u)));
          cache.set(u, g);
        }
        return g;
      };
      const top = await this.build(m.root, geo);
      this.root.add(top.group);
      // A mount names a link of the parent, or (the droid does) of another assembly: re-hang.
      const all = new Map<string, THREE.Group>();
      for (const n of this.nodes) for (const [id, g] of n.links) if (!all.has(id)) all.set(id, g);
      for (const n of this.nodes) {
        const pl = n.asm.mount?.parent_link;
        const g = pl ? all.get(pl) : undefined;
        if (g && n.group.parent !== g) g.add(n.group);
      }
      tameHighlights(this.root);
      this.status = 'ready';
      this.stats.loadMs = Math.round(performance.now() - t0);
      this.lastKey = '';
      this.apply({});
      this.root.visible = this.view !== 'visual';
    } catch (e) {
      this.status = 'error';
      this.error = String(e instanceof Error ? e.message : e);
      console.warn('mechview: load failed', e);
    }
    this.emit();
  }

  private async build(asm: MAssembly, geo: (u: string) => Promise<THREE.BufferGeometry>): Promise<Node> {
    const group = new THREE.Group();
    group.name = `asm:${asm.id}`;
    const mt = asm.mount?.transform;
    if (mt) {
      group.position.set(...mt.t);
      if (mt.q) group.quaternion.set(...mt.q);
    }
    const node: Node = {
      asm, group, links: new Map(), pose: {}, rodZero: new Map(), holders: [], gears: [],
      byProfile: new Map(asm.joints.filter((j) => j.profile_joint).map((j) => [j.profile_joint!, j.id])),
    };
    this.nodes.push(node);
    for (const l of asm.links) {
      const g = new THREE.Group();
      g.name = `link:${l.id}`;
      g.matrixAutoUpdate = false;
      group.add(g);
      node.links.set(l.id, g);
    }
    const hide = hiddenByVariants(asm, defaultVariants(asm));
    const base = asm.base ?? '/';
    // Static parts: one merged mesh per (link, class). Linkage parts: per (linkage, role). Gear parts: per gear.
    const gearOf = new Map<string, MGear>();
    for (const g of asm.gears ?? []) for (const id of [...g.parts, ...(g.fasteners ?? [])]) gearOf.set(id, g);
    const buckets = new Map<string, { geos: THREE.BufferGeometry[]; cls: PartClass; link?: string; lk?: MLinkage; role?: 'horn' | 'rod'; gear?: MGear }>();
    await Promise.all(asm.parts.filter((p) => !hide.has(p.id)).map(async (p) => {
      const g = (await geo(joinUrl(base, p.mesh))).clone();
      const q = p.transform.q ?? [0, 0, 0, 1];
      g.applyMatrix4(new THREE.Matrix4().compose(new THREE.Vector3(...p.transform.t), new THREE.Quaternion(...q), new THREE.Vector3(1, 1, 1)));
      const lk = p.linkage ? asm.linkages?.find((x) => x.id === p.linkage) : undefined;
      const role = lk ? (p.role === 'horn' ? 'horn' : 'rod') : undefined;
      const gear = lk ? undefined : gearOf.get(p.id);
      const key = lk ? `lk:${lk.id}:${role}:${p.class}` : gear ? `g:${gear.id}:${p.class}` : `l:${p.link}:${p.class}`;
      if (!buckets.has(key)) buckets.set(key, { geos: [], cls: p.class, link: lk ? undefined : gear?.link ?? p.link, lk, role, gear });
      buckets.get(key)!.geos.push(g);
      this.stats.parts++;
    }));
    for (const b of buckets.values()) {
      const merged = mergeGeometries(b.geos, false);
      b.geos.forEach((g) => g.dispose());
      if (!merged) continue;
      const mesh = new THREE.Mesh(merged, this.mats.get(b.cls)!);
      mesh.name = b.lk ? `${b.lk.id}:${b.role}` : `${b.link}:${b.cls}`;
      this.stats.drawCalls++;
      this.stats.triangles += (merged.index?.count ?? merged.attributes.position.count) / 3;
      if (b.lk) {
        const h = new THREE.Group();
        h.matrixAutoUpdate = false;
        h.add(mesh);
        group.add(h);
        node.holders.push({ lk: b.lk, role: b.role!, obj: h });
      } else if (b.gear) {
        const h = new THREE.Group();
        h.matrixAutoUpdate = false;
        h.add(mesh);
        (node.links.get(b.link!) ?? group).add(h);
        node.gears.push({ gear: b.gear, obj: h });
      } else {
        (node.links.get(b.link!) ?? group).add(mesh);
      }
    }
    // Fasteners: instanced per (link or linkage role, mesh).
    const fb = new Map<string, { url: string; ms: THREE.Matrix4[]; link?: string; lk?: MLinkage; role?: 'horn' | 'rod'; gear?: MGear }>();
    for (const f of asm.fasteners ?? []) {
      if (!f.placed || !f.mesh || !f.transform || !f.joins.every((p) => !hide.has(p))) continue;
      const lk = f.linkage ? asm.linkages?.find((x) => x.id === f.linkage) : undefined;
      const role = lk ? (f.role === 'horn' ? 'horn' : 'rod') : undefined;
      const gear = lk ? undefined : gearOf.get(f.id);
      const key = `${lk ? `lk:${lk.id}:${role}` : gear ? `g:${gear.id}` : `l:${f.link}`}|${f.mesh}`;
      if (!fb.has(key)) fb.set(key, { url: joinUrl(base, f.mesh), ms: [], link: lk ? undefined : gear?.link ?? f.link, lk, role, gear });
      const q = f.transform.q ?? [0, 0, 0, 1];
      fb.get(key)!.ms.push(new THREE.Matrix4().compose(new THREE.Vector3(...f.transform.t), new THREE.Quaternion(...q), new THREE.Vector3(1, 1, 1)));
      this.stats.fasteners++;
    }
    await Promise.all([...fb.values()].map(async (b) => {
      const g = await geo(b.url);
      const im = new THREE.InstancedMesh(g, this.mats.get('fastener')!, b.ms.length);
      b.ms.forEach((m, i) => im.setMatrixAt(i, m));
      im.instanceMatrix.needsUpdate = true;
      im.computeBoundingSphere();
      this.stats.drawCalls++;
      this.stats.triangles += ((g.index?.count ?? g.attributes.position.count) / 3) * b.ms.length;
      if (b.lk) {
        const h = new THREE.Group();
        h.matrixAutoUpdate = false;
        h.add(im);
        group.add(h);
        node.holders.push({ lk: b.lk, role: b.role!, obj: h });
      } else if (b.gear) {
        const h = new THREE.Group();
        h.matrixAutoUpdate = false;
        h.add(im);
        (node.links.get(b.link!) ?? group).add(h);
        node.gears.push({ gear: b.gear, obj: h });
      } else {
        (node.links.get(b.link!) ?? group).add(im);
      }
    }));
    const zero = linkMatrices(asm.links, asm.joints, {});
    for (const lk of asm.linkages ?? []) {
      const s = solveRod(lk, zero.get(lk.horn.link)!, zero.get(lk.ground.link)!);
      if (s) node.rodZero.set(lk.id, { a: s.a, b: s.b });
    }
    for (const c of (asm.children ?? []) as MAssembly[]) {
      const v = (c.mount as { variant?: { default?: boolean } } | undefined)?.variant;
      if (v && !v.default) continue; // a non-default option (the other head, the other frame)
      if (!c.links?.length && !c.parts?.length) continue; // a ChildRef that was not built
      const child = await this.build(c, geo);
      (node.links.get(c.mount?.parent_link ?? '') ?? group).add(child.group);
    }
    return node;
  }

  /** Pose from the performer's joint values (profile names; deg, or mm for the lift). */
  apply(values: Record<string, number>) {
    if (this.status !== 'ready' || !this.root.visible) return;
    let key = '';
    for (const k in values) key += `${k}:${values[k].toFixed(3)},`;
    if (key === this.lastKey) return;
    this.lastKey = key;
    const bad: string[] = [];
    for (const n of this.nodes) {
      for (const [pj, jid] of n.byProfile) n.pose[jid] = values[pj] ?? 0;
      const ms = linkMatrices(n.asm.links, n.asm.joints, n.pose);
      for (const [id, g] of n.links) g.matrix.copy(ms.get(id)!);
      for (const h of n.holders) {
        const s = solveRod(h.lk, ms.get(h.lk.horn.link)!, ms.get(h.lk.ground.link)!);
        if (!s) {
          bad.push(h.lk.id);
          continue; // out of reach: keeps its last pose
        }
        const z = n.rodZero.get(h.lk.id);
        h.obj.matrix.copy(h.role === 'horn' ? hornMatrix(h.lk, ms.get(h.lk.horn.link)!, s.servoDeg)
          : z ? rodMatrix(z.a, z.b, s.a, s.b) : new THREE.Matrix4());
      }
    }
    // gears turn with their joint, which may be another assembly's (the column's ring pinions)
    for (const n of this.nodes) {
      for (const { gear, obj } of n.gears) {
        const src = gear.joint_assembly ? this.nodes.find((x) => x.asm.id === gear.joint_assembly) : n;
        gearMatrix(gear, src?.pose[gear.joint] ?? 0, obj.matrix);
      }
    }
    this.unreachable = [...new Set(bad)];
    this.root.updateMatrixWorld(true);
  }
}

/** Position + normal only, float32 and indexed, so every part merges with every other. */
function clean(src: THREE.BufferGeometry): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  const f32 = (a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute) => {
    const out = new Float32Array(a.count * 3);
    for (let i = 0; i < a.count; i++) {
      out[i * 3] = a.getX(i);
      out[i * 3 + 1] = a.getY(i);
      out[i * 3 + 2] = a.getZ(i);
    }
    return new THREE.BufferAttribute(out, 3);
  };
  g.setAttribute('position', f32(src.getAttribute('position')));
  const n = src.getAttribute('normal');
  if (n) g.setAttribute('normal', f32(n));
  const count = g.getAttribute('position').count;
  const idx = src.index ? Array.from(src.index.array as ArrayLike<number>) : null;
  const arr = new (count > 65535 ? Uint32Array : Uint16Array)(idx ? idx.length : count);
  if (idx) arr.set(idx);
  else for (let i = 0; i < count; i++) arr[i] = i;
  g.setIndex(new THREE.BufferAttribute(arr, 1));
  if (!n) g.computeVertexNormals();
  sane(g);
  return g;
}

/** Zero-length normals shade NaN and bloom to white (workbench.ts saneNormals). */
function sane(g: THREE.BufferGeometry) {
  const n = g.getAttribute('normal') as THREE.BufferAttribute;
  for (let i = 0; i < n.count; i++) {
    const l = Math.hypot(n.getX(i), n.getY(i), n.getZ(i));
    if (!Number.isFinite(l) || l < 1e-6) n.setXYZ(i, 0, 1, 0);
  }
}
