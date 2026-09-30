/**
 * Build mode's 3D side: loads a mech manifest (mech/out/<assembly>/manifest.json) into the
 * same three.js scene as the droid, at the droid's own head position, and poses it. Sim only:
 * nothing here talks to the gateway or the performer, so Build never drives hardware.
 *
 * Structure: one THREE.Group per assembly (child assemblies ride their parent's link), one
 * matrix-driven group per link (kinematics.ts), a mesh per part under its link, horn and rod
 * parts posed by the closed-form push-rod solve. How the parts are drawn (shell solid/x-ray/
 * hidden, explode, section, fasteners, selection, isolate, step and check highlights) is
 * recomputed from this object's state in `refresh()`, so every control is one setter.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeVertices, toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';
import type { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { tameHighlights } from '../look';
import { hornMatrix, linkMatrices, rodMatrix, solveRod, type Pose } from './kinematics';
import {
  defaultVariants, firstStep, hiddenByVariants, joinUrl, loadManifest,
  type MAssembly, type MCheck, type MFastener, type Manifest, type MLinkage, type MPart, type PartClass,
} from './manifest';

export type ShellMode = 'solid' | 'xray' | 'hidden';
export type Axis = 'x' | 'y' | 'z';

export interface BuildHost {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  renderer: THREE.WebGLRenderer;
  /** Draw at full rate for a moment (pacer). */
  interact(): void;
  /** The droid model (and its overlays) while Build is open. */
  setDroidVisible(on: boolean): void;
}

interface PartObj {
  part: MPart;
  node: AsmNode;
  holder: THREE.Object3D; // posed by a linkage (horns, rods), else the mesh itself
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
  base: THREE.Vector3;
}

interface FastObj { f: MFastener; node: AsmNode; obj: THREE.Mesh; base: THREE.Matrix4; mat: THREE.MeshStandardMaterial; holder: THREE.Object3D | null }

export interface AsmNode {
  asm: MAssembly;
  group: THREE.Group;
  links: Map<string, THREE.Group>;
  pose: Pose;
  parent: AsmNode | null;
  children: AsmNode[];
  /** Zero-pose ball centres of each linkage (for moving the rod meshes). */
  rodZero: Map<string, { a: THREE.Vector3; b: THREE.Vector3 }>;
  /** Last solve per linkage (null = out of reach at this pose). */
  rods: Map<string, { servoDeg: number } | null>;
}

const CLASS_LOOK: Record<PartClass, { color: number; metalness: number; roughness: number }> = {
  shell: { color: 0xd8d2c4, metalness: 0.0, roughness: 0.62 },
  mech: { color: 0xc9773d, metalness: 0.0, roughness: 0.55 },
  servo: { color: 0x2c3444, metalness: 0.2, roughness: 0.5 },
  hardware: { color: 0xb8b09a, metalness: 0.75, roughness: 0.35 },
  bearing: { color: 0x9aa1ab, metalness: 0.85, roughness: 0.3 },
  fastener: { color: 0x34353a, metalness: 0.7, roughness: 0.4 },
};
const CREASE = (30 * Math.PI) / 180;
const HIGHLIGHT = { step: 0x4aa3ff, selected: 0xe8762a, fail: 0xff4d4d, warn: 0xffb347 };

export class Workbench {
  readonly root = new THREE.Group();
  manifest: Manifest | null = null;
  top: AsmNode | null = null;
  /** The assembly the panel is focused on (breadcrumb); isolates its subtree. */
  focus: AsmNode | null = null;
  active = false;
  loading: Promise<void> | null = null;
  error = '';

  explode = 0;
  shell: ShellMode = 'solid';
  section = { on: false, axis: 'x' as Axis, at: 0.5, flip: false };
  fasteners = true;
  selected: string | null = null;
  hidden = new Set<string>();
  isolated: Set<string> | null = null;
  variants: Record<string, string> = {};
  /** Steps tab: the current step's index, or -1. */
  step = -1;
  /** A check being shown: its parts are marked, the pose applied. */
  check: MCheck | null = null;
  /** Parts marked by a sweep reaching the first-contact angle. */
  contact: { parts: string[]; status: 'fail' | 'warn' } | null = null;

  parts = new Map<string, PartObj>();
  fast = new Map<string, FastObj>();
  private readonly listeners = new Set<() => void>();
  private readonly plane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0);
  private readonly lights = new THREE.Group();
  private saved: { pos: THREE.Vector3; target: THREE.Vector3; min: number; max: number; polar: [number, number]; az: [number, number] } | null = null;
  private anim: { from: number; dur: number; step: number } | null = null;
  private sweep: { node: AsmNode; joint: string; t0: number; path: [number, number][]; contact: number | null } | null = null;
  private picking = { x: 0, y: 0, down: false };

  constructor(private host: BuildHost) {
    this.root.name = 'build';
    this.root.visible = false;
    this.root.scale.setScalar(0.001); // mm -> m
    host.scene.add(this.root);
    const hemi = new THREE.HemisphereLight(0xf2f4ff, 0x2a2622, 1.6);
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(0.8, 1.6, 1.4);
    const rim = new THREE.DirectionalLight(0xcfe0ff, 1.1);
    rim.position.set(-1.2, 1.0, -1.4);
    this.lights.add(hemi, key, key.target, rim);
    this.lights.visible = false;
    host.scene.add(this.lights);
    const el = host.renderer.domElement;
    el.addEventListener('pointerdown', (e) => {
      this.picking = { x: e.clientX, y: e.clientY, down: e.button === 0 };
    });
    el.addEventListener('pointerup', (e) => {
      if (!this.active || !this.picking.down) return;
      this.picking.down = false;
      if (Math.hypot(e.clientX - this.picking.x, e.clientY - this.picking.y) > 4) return;
      this.pick(e.clientX, e.clientY);
    });
  }

  onChange(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    for (const f of this.listeners) f();
    this.host.interact();
  }

  // ------------------------------------------------------------------ activation

  setActive(on: boolean) {
    if (on === this.active) return;
    this.active = on;
    this.root.visible = on;
    this.lights.visible = on;
    this.host.renderer.localClippingEnabled = on;
    this.host.setDroidVisible(!on);
    const c = this.host.controls;
    if (on) {
      this.saved = {
        pos: this.host.camera.position.clone(), target: c.target.clone(), min: c.minDistance, max: c.maxDistance,
        polar: [c.minPolarAngle, c.maxPolarAngle], az: [c.minAzimuthAngle, c.maxAzimuthAngle],
      };
      c.minDistance = 0.05;
      c.maxDistance = 4;
      c.minPolarAngle = 0;
      c.maxPolarAngle = Math.PI;
      c.minAzimuthAngle = -Infinity;
      c.maxAzimuthAngle = Infinity;
      if (this.top) this.frame();
    } else if (this.saved) {
      this.host.camera.position.copy(this.saved.pos);
      c.target.copy(this.saved.target);
      c.minDistance = this.saved.min;
      c.maxDistance = this.saved.max;
      [c.minPolarAngle, c.maxPolarAngle] = this.saved.polar;
      [c.minAzimuthAngle, c.maxAzimuthAngle] = this.saved.az;
      this.sweep = null;
    }
    this.emit();
  }

  // ------------------------------------------------------------------ loading

  load(url: string): Promise<void> {
    this.loading = this.doLoad(url).catch((e) => {
      this.error = String(e instanceof Error ? e.message : e);
      console.warn('build: load failed', e);
    }).finally(() => {
      this.loading = null;
      this.emit();
    });
    this.emit();
    return this.loading;
  }

  private loadSeq = 0;

  private async doLoad(url: string) {
    this.error = '';
    const seq = ++this.loadSeq;
    const m = await loadManifest(url);
    if (seq !== this.loadSeq) return; // a newer pick superseded this one
    this.clear();
    this.manifest = m;
    const loader = new GLTFLoader();
    const geoCache = new Map<string, Promise<THREE.BufferGeometry>>();
    const geometry = (u: string) => {
      let g = geoCache.get(u);
      if (!g) {
        g = loader.loadAsync(u).then((gltf) => {
          let found: THREE.BufferGeometry | null = null;
          gltf.scene.traverse((o) => {
            if (!found && (o as THREE.Mesh).isMesh) found = (o as THREE.Mesh).geometry as THREE.BufferGeometry;
          });
          if (!found) throw new Error(`${u}: no mesh`);
          // Round faces render round, hard edges stay sharp: weld, then crease at 30 deg.
          let geo = found as THREE.BufferGeometry;
          geo.deleteAttribute('normal');
          geo = toCreasedNormals(mergeVertices(geo, 1e-4), CREASE);
          saneNormals(geo);
          return geo;
        });
        geoCache.set(u, g);
      }
      return g;
    };
    const parts = new Map<string, PartObj>();
    const fast = new Map<string, FastObj>();
    const top = await this.buildNode(m.root, null, geometry, parts, fast);
    if (seq !== this.loadSeq) return;
    this.parts = parts;
    this.fast = fast;
    this.top = top;
    this.root.add(this.top.group);
    // The droid's highlight knee (look.ts): lit surfaces stay under the bloom threshold.
    tameHighlights(this.root);
    // The head mech's frame sits where the droid's head is (its mount, mm in the body frame).
    const mt = m.root.mount?.transform?.t ?? [0, 0, 0];
    this.root.position.set(mt[0] / 1000, mt[1] / 1000, mt[2] / 1000);
    this.focus = this.top;
    this.variants = {};
    this.forEachNode((n) => Object.assign(this.variants, defaultVariants(n.asm)));
    this.pose();
    this.refresh();
    if (this.active) this.frame();
  }

  private clear() {
    this.root.clear();
    this.parts.clear();
    this.fast.clear();
    this.top = this.focus = null;
    this.selected = null;
    this.hidden.clear();
    this.isolated = null;
    this.step = -1;
    this.check = null;
    this.contact = null;
  }

  private async buildNode(asm: MAssembly, parent: AsmNode | null, geometry: (u: string) => Promise<THREE.BufferGeometry>,
    parts: Map<string, PartObj>, fast: Map<string, FastObj>): Promise<AsmNode> {
    const group = new THREE.Group();
    group.name = `asm:${asm.id}`;
    const node: AsmNode = { asm, group, links: new Map(), pose: {}, parent, children: [], rodZero: new Map(), rods: new Map() };
    for (const l of asm.links) {
      const g = new THREE.Group();
      g.name = `link:${l.id}`;
      g.matrixAutoUpdate = false;
      group.add(g);
      node.links.set(l.id, g);
    }
    const base = asm.base ?? '/';
    await Promise.all(asm.parts.map(async (p) => {
      const geo = await geometry(joinUrl(base, p.mesh));
      const mat = this.material(p.class);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.name = p.id;
      mesh.userData.partId = p.id;
      mesh.castShadow = mesh.receiveShadow = false;
      const b = new THREE.Vector3(...p.transform.t);
      mesh.position.copy(b);
      let holder: THREE.Object3D = mesh;
      if (p.linkage) {
        holder = new THREE.Group();
        holder.matrixAutoUpdate = false;
        holder.add(mesh);
        group.add(holder);
      } else {
        (node.links.get(p.link) ?? group).add(mesh);
      }
      parts.set(p.id, { part: p, node, holder, mesh, mat, base: b });
    }));
    const fastMat = this.material('fastener');
    await Promise.all((asm.fasteners ?? []).filter((f) => f.placed && f.mesh && f.transform).map(async (f) => {
      const geo = await geometry(joinUrl(base, f.mesh!));
      const mat = fastMat.clone();
      const obj = new THREE.Mesh(geo, mat);
      obj.name = f.id;
      obj.userData.fastenerId = f.id;
      const q = f.transform!.q ?? [0, 0, 0, 1];
      const m = new THREE.Matrix4().compose(new THREE.Vector3(...f.transform!.t), new THREE.Quaternion(...q), new THREE.Vector3(1, 1, 1));
      obj.matrixAutoUpdate = false;
      obj.matrix.copy(m);
      // A ball stud on a servo arm turns with the horn: posed by its linkage like the arm.
      let holder: THREE.Object3D | null = null;
      if (f.linkage) {
        holder = new THREE.Group();
        holder.matrixAutoUpdate = false;
        holder.add(obj);
        group.add(holder);
      } else {
        (node.links.get(f.link) ?? group).add(obj);
      }
      fast.set(f.id, { f, node, obj, base: m, mat, holder });
    }));
    // Zero-pose rod balls: where the rod meshes were exported.
    const zero = linkMatrices(asm.links, asm.joints, {});
    for (const lk of asm.linkages ?? []) {
      const s = solveRod(lk, zero.get(lk.horn.link)!, zero.get(lk.ground.link)!);
      if (s) node.rodZero.set(lk.id, { a: s.a, b: s.b });
    }
    for (const c of (asm.children ?? []) as MAssembly[]) {
      const child = await this.buildNode(c, node, geometry, parts, fast);
      node.children.push(child);
      const mt = c.mount?.transform;
      if (mt) {
        child.group.position.set(...mt.t);
        if (mt.q) child.group.quaternion.set(...mt.q);
      }
      (node.links.get(c.mount?.parent_link ?? '') ?? group).add(child.group);
    }
    return node;
  }

  private material(cls: PartClass) {
    const look = CLASS_LOOK[cls];
    return new THREE.MeshStandardMaterial({ ...look, side: THREE.DoubleSide });
  }

  forEachNode(fn: (n: AsmNode) => void, from = this.top) {
    if (!from) return;
    fn(from);
    for (const c of from.children) this.forEachNode(fn, c);
  }

  nodeOf(asmId: string): AsmNode | null {
    let hit: AsmNode | null = null;
    this.forEachNode((n) => {
      if (n.asm.id === asmId) hit = n;
    });
    return hit;
  }

  // ------------------------------------------------------------------ pose

  setJoint(node: AsmNode, joint: string, value: number) {
    const j = node.asm.joints.find((x) => x.id === joint);
    if (!j) return;
    node.pose[joint] = Math.min(j.limits.max + 40, Math.max(j.limits.min - 40, value));
    this.pose();
    this.emit();
  }

  home() {
    this.sweep = null;
    this.forEachNode((n) => (n.pose = {}));
    this.contact = null;
    this.pose();
    this.refresh();
    this.emit();
  }

  /** Apply every node's pose: link matrices, horns and rods. */
  private pose() {
    this.forEachNode((n) => {
      const ms = linkMatrices(n.asm.links, n.asm.joints, n.pose);
      for (const [id, g] of n.links) g.matrix.copy(ms.get(id)!);
      for (const lk of n.asm.linkages ?? []) this.poseLinkage(n, lk, ms);
    });
    this.root.updateMatrixWorld(true);
  }

  private poseLinkage(n: AsmNode, lk: MLinkage, ms: Map<string, THREE.Matrix4>) {
    const s = solveRod(lk, ms.get(lk.horn.link)!, ms.get(lk.ground.link)!);
    n.rods.set(lk.id, s ? { servoDeg: s.servoDeg } : null);
    if (!s) return; // out of reach: the rod keeps its last pose, the panel says so
    const zero = n.rodZero.get(lk.id);
    for (const fo of this.fast.values()) {
      if (fo.node !== n || fo.f.linkage !== lk.id || !fo.holder) continue;
      fo.holder.matrix.copy(fo.f.role === 'horn' ? hornMatrix(lk, ms.get(lk.horn.link)!, s.servoDeg)
        : zero ? rodMatrix(zero.a, zero.b, s.a, s.b) : new THREE.Matrix4());
    }
    for (const pid of lk.parts) {
      const po = this.parts.get(pid);
      if (!po) continue;
      const role = po.part.role ?? '';
      const m = role === 'horn' ? hornMatrix(lk, ms.get(lk.horn.link)!, s.servoDeg)
        : zero ? rodMatrix(zero.a, zero.b, s.a, s.b) : new THREE.Matrix4();
      po.holder.matrix.copy(m);
    }
  }

  // ------------------------------------------------------------------ view state

  setExplode(v: number) { this.explode = v; this.refresh(); this.emit(); }
  setShell(m: ShellMode) { this.shell = m; this.refresh(); this.emit(); }
  setFasteners(on: boolean) { this.fasteners = on; this.refresh(); this.emit(); }
  setSection(s: Partial<Workbench['section']>) { Object.assign(this.section, s); this.refresh(); this.emit(); }

  select(id: string | null) {
    this.selected = id;
    this.refresh();
    this.emit();
  }

  toggleHidden(id: string) {
    if (this.hidden.has(id)) this.hidden.delete(id);
    else this.hidden.add(id);
    this.refresh();
    this.emit();
  }

  /** Isolate a set of parts (null = everything). */
  isolate(ids: Iterable<string> | null) {
    this.isolated = ids ? new Set(ids) : null;
    this.refresh();
    this.emit();
    if (this.active) this.frame(true);
  }

  setFocus(node: AsmNode) {
    this.focus = node;
    this.step = -1;
    const ids: string[] = [];
    this.forEachNode((n) => n.asm.parts.forEach((p) => ids.push(p.id)), node);
    this.isolated = node === this.top ? null : new Set(ids);
    this.refresh();
    this.emit();
    if (this.active) this.frame(true);
  }

  setVariant(group: string, id: string) {
    this.variants[group] = id;
    this.refresh();
    this.emit();
  }

  /** Steps tab: go to step `i` of the focused assembly (-1 leaves step mode). */
  setStep(i: number) {
    const steps = this.focus?.asm.steps ?? [];
    const prev = this.step;
    this.step = Math.max(-1, Math.min(steps.length - 1, i));
    this.check = null;
    if (this.step >= 0) {
      const s = steps[this.step];
      if (s.pose && this.focus) {
        this.focus.pose = { ...this.focus.pose, ...s.pose };
        this.pose();
      }
      if (this.step > prev && !reducedMotion()) this.anim = { from: performance.now(), dur: 700, step: this.step };
    }
    this.refresh();
    if (this.active && this.step >= 0) this.frameStep();
    this.emit();
  }

  /** Checks tab: pose the model to show a check (null clears). */
  showCheck(c: MCheck | null, node = this.focus) {
    this.check = c;
    this.step = -1;
    this.contact = null;
    if (c && node) {
      node.pose = { ...(c.pose ?? {}) };
      this.pose();
      if (c.kind === 'clearance' && this.shell === 'solid') this.shell = 'xray';
    }
    this.refresh();
    this.emit();
  }

  /** Sweep a joint through its range; the parts of its first contact (from the checks) light
   *  up when the sweep passes that angle. */
  startSweep(node: AsmNode, joint: string) {
    const j = node.asm.joints.find((x) => x.id === joint);
    if (!j) return;
    const chk = (node.asm.checks ?? []).find((c) => c.id === `interference_${joint}`);
    const { min, max } = j.limits;
    const start = node.pose[joint] ?? 0;
    const path: [number, number][] = [[0, start], [1, max], [2.2, min], [3, 0]];
    this.sweep = { node, joint, t0: performance.now(), path, contact: chk?.value ?? null };
    this.check = null;
    this.step = -1;
    this.emit();
  }

  get sweeping() {
    return this.sweep ? this.sweep.joint : null;
  }

  /** Per frame (main.ts). */
  tick(now = performance.now()) {
    if (!this.active) return;
    let moving = false;
    if (this.sweep) {
      const s = this.sweep;
      const t = (now - s.t0) / 1000;
      const seg = s.path.findIndex(([at]) => at > t);
      if (seg < 0) {
        s.node.pose[s.joint] = s.path[s.path.length - 1][1];
        this.sweep = null;
        this.contact = null;
      } else {
        const [t0, v0] = s.path[seg - 1] ?? s.path[0];
        const [t1, v1] = s.path[seg];
        const k = t1 > t0 ? (t - t0) / (t1 - t0) : 1;
        const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
        const v = v0 + (v1 - v0) * e;
        s.node.pose[s.joint] = v;
        const chk = (s.node.asm.checks ?? []).find((c) => c.id === `interference_${s.joint}`);
        const past = s.contact !== null && (s.contact > 0 ? v >= s.contact : v <= s.contact);
        this.contact = past && chk ? { parts: chk.parts ?? [], status: chk.status === 'fail' ? 'fail' : 'warn' } : null;
      }
      this.pose();
      this.refresh();
      for (const f of this.listeners) f();
      moving = true;
    }
    if (this.anim) {
      const k = Math.min(1, (now - this.anim.from) / this.anim.dur);
      this.applyOffsets(1 - k);
      if (k >= 1) this.anim = null;
      moving = true;
    }
    if (moving) this.host.interact();
  }

  // ------------------------------------------------------------------ drawing

  private stepIndex() {
    return this.focus ? firstStep(this.focus.asm) : new Map<string, number>();
  }

  /** Visibility, materials, clipping and offsets from the state above. */
  refresh() {
    if (!this.top) return;
    const hideV = new Set<string>();
    this.forEachNode((n) => hiddenByVariants(n.asm, this.variants).forEach((p) => hideV.add(p)));
    const steps = this.focus?.asm.steps ?? [];
    const cur = this.step >= 0 ? steps[this.step] : null;
    const first = this.stepIndex();
    const inStep = new Set(cur?.parts ?? []);
    const ctx = new Set(cur?.context ?? []);
    const checkParts = new Set(this.check?.parts ?? []);
    const contact = new Set(this.contact?.parts ?? []);
    const clip = this.section.on ? [this.sectionPlane()] : null;

    for (const [id, po] of this.parts) {
      const p = po.part;
      let visible = !hideV.has(id) && !this.hidden.has(id) && (!this.isolated || this.isolated.has(id));
      if (p.class === 'shell' && this.shell === 'hidden') visible = false;
      const f = first.get(id);
      if (cur && f !== undefined && f > this.step && !ctx.has(id)) visible = false;
      po.holder.visible = visible;
      po.mesh.visible = visible;
      const m = po.mat;
      const look = CLASS_LOOK[p.class];
      m.color.setHex(look.color);
      m.emissive.setHex(0x000000);
      m.emissiveIntensity = 0.55;
      let opacity = 1;
      if (p.class === 'shell' && this.shell === 'xray') opacity = 0.13;
      if (cur && !inStep.has(id) && !ctx.has(id)) opacity = Math.min(opacity, 0.16);
      if (this.check && checkParts.size && !checkParts.has(id)) opacity = Math.min(opacity, 0.16);
      if (cur && inStep.has(id)) m.emissive.setHex(HIGHLIGHT.step);
      if (this.check && checkParts.has(id)) {
        m.emissive.setHex(this.check.status === 'fail' ? HIGHLIGHT.fail : this.check.status === 'warn' ? HIGHLIGHT.warn : HIGHLIGHT.step);
        opacity = 1;
      }
      if (contact.has(id)) {
        m.emissive.setHex(this.contact?.status === 'fail' ? HIGHLIGHT.fail : HIGHLIGHT.warn);
        opacity = Math.max(opacity, 0.5);
      }
      if (this.selected === id) {
        m.emissive.setHex(HIGHLIGHT.selected);
        opacity = Math.max(opacity, 0.85);
      }
      setLook(m, opacity, clip);
      po.mesh.renderOrder = opacity < 1 ? 2 : 0;
    }
    const fastIn = new Set(cur?.fasteners ?? []);
    const stepIds = steps.map((s) => s.id);
    for (const [id, fo] of this.fast) {
      const joinsVisible = fo.f.joins.some((p) => this.parts.get(p)?.mesh.visible);
      let visible = this.fasteners && joinsVisible && !this.hidden.has(id);
      if (cur && stepIds.indexOf(fo.f.step) > this.step) visible = false;
      if (this.isolated && !fo.f.joins.some((p) => this.isolated!.has(p))) visible = false;
      fo.obj.visible = visible;
      fo.mat.emissive.setHex(cur && fastIn.has(id) ? HIGHLIGHT.step : this.selected === id ? HIGHLIGHT.selected : 0);
      fo.mat.emissiveIntensity = 0.7;
      setLook(fo.mat, cur && !fastIn.has(id) ? 0.25 : 1, clip);
    }
    this.applyOffsets(this.anim ? 1 - Math.min(1, (performance.now() - this.anim.from) / this.anim.dur) : 0);
  }

  /** Explode offsets, plus the insertion animation of the current step (`insert` 1 -> 0). */
  private applyOffsets(insert: number) {
    const cur = this.step >= 0 ? this.focus?.asm.steps?.[this.step] : null;
    const inStep = new Set(cur?.parts ?? []);
    const fastIn = new Set(cur?.fasteners ?? []);
    const tmp = new THREE.Vector3();
    for (const po of this.parts.values()) {
      const p = po.part;
      const dir = tmp.set(...(p.explode ?? [0, 0, 0]));
      const k = this.explode + (inStep.has(p.id) ? insert * 1.2 : 0);
      po.mesh.position.copy(po.base).addScaledVector(dir, (p.explode_mm ?? 0) * k);
    }
    for (const fo of this.fast.values()) {
      const owner = this.parts.get(fo.f.joins[0]);
      const back = 18 * this.explode + (fastIn.has(fo.f.id) ? insert * 30 : 0);
      const m = fo.base.clone().multiply(new THREE.Matrix4().makeTranslation(0, 0, -back));
      if (owner) {
        const d = new THREE.Vector3(...(owner.part.explode ?? [0, 0, 0])).multiplyScalar((owner.part.explode_mm ?? 0) * this.explode);
        m.premultiply(new THREE.Matrix4().makeTranslation(d.x, d.y, d.z));
      }
      fo.obj.matrix.copy(m);
    }
    this.root.updateMatrixWorld(true);
    this.host.interact();
  }

  /** The section plane in world space, across the visible model's bounds. */
  private sectionPlane() {
    const box = this.bounds(false);
    const ax = { x: 0, y: 1, z: 2 }[this.section.axis];
    const n = new THREE.Vector3().setComponent(ax, this.section.flip ? 1 : -1);
    const at = box.min.getComponent(ax) + (box.max.getComponent(ax) - box.min.getComponent(ax)) * this.section.at;
    return this.plane.set(n, this.section.flip ? -at : at);
  }

  /** World bounds of the visible parts (or of everything). */
  bounds(visibleOnly = true) {
    const box = new THREE.Box3();
    this.root.updateMatrixWorld(true);
    for (const po of this.parts.values()) {
      if (visibleOnly && !po.mesh.visible) continue;
      if (!po.mesh.geometry.boundingBox) po.mesh.geometry.computeBoundingBox();
      box.union(po.mesh.geometry.boundingBox!.clone().applyMatrix4(po.mesh.matrixWorld));
    }
    return box;
  }

  /** Point the camera at the visible model (3/4 front), or keep the view direction. */
  frame(keepDirection = false) {
    const box = this.bounds(true);
    if (!box.isEmpty()) this.frameBox(box, keepDirection);
  }

  private frameBox(box: THREE.Box3, keepDirection: boolean) {
    const c = box.getCenter(new THREE.Vector3());
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    const cam = this.host.camera;
    const dir = keepDirection
      ? cam.position.clone().sub(this.host.controls.target).normalize()
      : new THREE.Vector3(0.62, 0.38, 1).normalize();
    const dist = (r / Math.sin((cam.fov * Math.PI) / 360)) * 1.2;
    this.host.controls.target.copy(c);
    cam.position.copy(c).addScaledVector(dir, dist);
    this.host.interact();
  }

  /** Frame the current step's parts and their context, keeping the view direction. */
  private frameStep() {
    const s = this.focus?.asm.steps?.[this.step];
    const ids = new Set([...(s?.parts ?? []), ...(s?.context ?? [])]);
    const box = new THREE.Box3();
    this.root.updateMatrixWorld(true);
    for (const id of ids) {
      const po = this.parts.get(id);
      if (!po) continue;
      if (!po.mesh.geometry.boundingBox) po.mesh.geometry.computeBoundingBox();
      box.union(po.mesh.geometry.boundingBox!.clone().applyMatrix4(po.mesh.matrixWorld));
    }
    if (!box.isEmpty()) this.frameBox(box, true);
  }

  private pick(x: number, y: number) {
    const el = this.host.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((x - el.left) / el.width) * 2 - 1, -((y - el.top) / el.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.host.camera);
    const plane = this.section.on ? this.sectionPlane() : null;
    const hits = ray.intersectObject(this.root, true).filter((h) => {
      const o = h.object as THREE.Mesh;
      if (!o.visible || !(o.userData.partId || o.userData.fastenerId)) return false;
      const mat = o.material as THREE.MeshStandardMaterial;
      if (mat.transparent && mat.opacity < 0.3 && !(this.shell === 'xray' && this.parts.get(o.userData.partId)?.part.class !== 'shell')) return false;
      if (plane && plane.distanceToPoint(h.point) < 0) return false;
      let p: THREE.Object3D | null = o;
      while (p) {
        if (!p.visible) return false;
        p = p.parent;
      }
      return true;
    });
    const h = hits[0]?.object;
    this.select(h ? (h.userData.partId ?? h.userData.fastenerId) : null);
  }

  // ------------------------------------------------------------------ queries for the panel

  partInfo(id: string) {
    const po = this.parts.get(id);
    if (!po) return null;
    const n = po.node;
    const link = n.asm.links.find((l) => l.id === po.part.link);
    const joint = link?.joint ? n.asm.joints.find((j) => j.id === link.joint) : undefined;
    return { part: po.part, node: n, link, joint };
  }

  fastenerInfo(id: string) {
    return this.fast.get(id)?.f ?? this.focus?.asm.fasteners?.find((f) => f.id === id) ?? null;
  }
}

/** A zero-length normal (a degenerate triangle in a decimated mesh) shades as NaN, and one
 *  NaN pixel spreads through the bloom into a white screen: give those an arbitrary unit normal. */
function saneNormals(geo: THREE.BufferGeometry) {
  const n = geo.attributes.normal as THREE.BufferAttribute;
  for (let i = 0; i < n.count; i++) {
    const x = n.getX(i), y = n.getY(i), z = n.getZ(i);
    const l = Math.hypot(x, y, z);
    if (!Number.isFinite(l) || l < 1e-6) n.setXYZ(i, 0, 1, 0);
  }
}

/** Opacity and clipping; recompiles the material only when its program would change. */
function setLook(m: THREE.MeshStandardMaterial, opacity: number, clip: THREE.Plane[] | null) {
  const transparent = opacity < 1;
  const program = transparent !== m.transparent || (clip?.length ?? 0) !== (m.clippingPlanes?.length ?? 0);
  m.transparent = transparent;
  m.opacity = opacity;
  m.depthWrite = !transparent;
  m.clippingPlanes = clip;
  if (program) m.needsUpdate = true;
}

function reducedMotion() {
  try {
    return matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
