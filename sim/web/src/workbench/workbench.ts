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
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { hornMatrix, linkMatrices, rodMatrix, solveRod, type Pose } from './kinematics';
import {
  firstStep, meshGeometry, variantOptions, hiddenAssemblies, hiddenByVariants, joinUrl, loadManifest,
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
  /** A part of an unpicked variant option: its mesh is fetched when the option is picked. */
  lazy?: string;
  node: AsmNode;
  holder: THREE.Object3D; // posed by a linkage (horns, rods), else the mesh itself
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
  base: THREE.Vector3;
  /** Where the mesh sits at the current stretch (parts with `stretch`), else `base`. */
  sBase?: THREE.Vector3;
}

export interface InterferencePair { a: string; b: string; depth_mm: number; at: [number, number, number]; explained: boolean; volume_mm3?: number | null; mesh?: string }

interface FastObj { f: MFastener; node: AsmNode; obj: THREE.Mesh; base: THREE.Matrix4; mat: THREE.MeshStandardMaterial; holder: THREE.Object3D | null; lazy?: string }

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

/**
 * One readable colour per material class, CAD-viewport style: printed shell a warm grey (not
 * white: a near-white albedo under a key light clips and loses its form), printed mechanism
 * parts orange, vendor metal grey steel, servos slate, fasteners dark steel.
 */
const CLASS_LOOK: Record<PartClass, { color: number; metalness: number; roughness: number }> = {
  shell: { color: 0xb9b3a6, metalness: 0.0, roughness: 0.6 },
  mech: { color: 0xc26a30, metalness: 0.0, roughness: 0.55 },
  servo: { color: 0x3b4658, metalness: 0.15, roughness: 0.5 },
  hardware: { color: 0x9ea2a8, metalness: 0.7, roughness: 0.38 },
  bearing: { color: 0x8d949e, metalness: 0.8, roughness: 0.32 },
  fastener: { color: 0x55585e, metalness: 0.7, roughness: 0.4 },
};
const CREASE = (30 * Math.PI) / 180;
/** Feature edges drawn over the parts: folds sharper than this (clear edges on light parts). */
const EDGE_ANGLE = 40;

/**
 * Build's lighting, "Inspection": a CAD viewport's neutral rig - a soft key from above front
 * right, a fill from the left, a rim from behind and a low neutral room environment - that
 * replaces the set's lights while Build is open (the booth key alone is 110 lm-ish on top of
 * the work light, which is what blew the printed shells to white). Intensities are chosen so a
 * light albedo peaks under 1.0 at exposure 1, with no highlight knee: form reads from shading.
 * The Scene panel's Lighting levels still apply (key / fill / rim / ambient), by these names.
 */
const INSPECTION = { key: 1.7, fill: 0.55, rim: 0.9, hemi: 0.3, env: 0.35 };
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
  /** Every part/fastener object, including repeats of an id across variant options (the maps
   * above index the ones in shown assemblies first). */
  private allParts: PartObj[] = [];
  private allFast: FastObj[] = [];
  /** The suite's overlapping pairs at rest (out/<id>/interference.json), and the overlay's state. */
  interference: InterferencePair[] = [];
  interferenceOn = false;
  private ifGroup = new THREE.Group();
  /** Nodes out of the model: under an unpicked child variant (e.g. the droid's other head). */
  private variantHidden = new Set<AsmNode>();
  private readonly listeners = new Set<() => void>();
  private readonly plane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 0);
  private readonly lights = new THREE.Group();
  /** The set's own lights, switched off while Build is open (restored on leaving). */
  private setLights: THREE.Light[] = [];
  private env: THREE.Texture | null = null;
  private savedEnv: { env: THREE.Texture | null; intensity: number } | null = null;
  private readonly edgeMat = new THREE.LineBasicMaterial({ color: 0x0b0c10, transparent: true, opacity: 0.38, depthWrite: false });
  private readonly edgeGeo = new WeakMap<THREE.BufferGeometry, THREE.EdgesGeometry>();
  private saved: { pos: THREE.Vector3; target: THREE.Vector3; min: number; max: number; polar: [number, number]; az: [number, number] } | null = null;
  private anim: { from: number; dur: number; step: number } | null = null;
  private sweep: { node: AsmNode; joint: string; t0: number; path: [number, number][]; contact: number | null } | null = null;
  private picking = { x: 0, y: 0, down: false };

  constructor(private host: BuildHost) {
    this.root.name = 'build';
    this.root.visible = false;
    this.root.scale.setScalar(0.001); // mm -> m
    host.scene.add(this.root);
    const hemi = new THREE.HemisphereLight(0xf4f5f8, 0x3a3834, INSPECTION.hemi);
    const key = new THREE.DirectionalLight(0xfffaf2, INSPECTION.key);
    key.name = 'build_key';
    key.position.set(0.9, 1.7, 1.3);
    const fill = new THREE.DirectionalLight(0xe8eefc, INSPECTION.fill);
    fill.name = 'build_fill';
    fill.position.set(-1.5, 0.5, 0.9);
    const rim = new THREE.DirectionalLight(0xe6eeff, INSPECTION.rim);
    rim.name = 'build_rim';
    rim.position.set(-0.6, 1.2, -1.6);
    this.lights.add(hemi, key, key.target, fill, fill.target, rim, rim.target);
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
    this.inspection(on);
    this.host.renderer.localClippingEnabled = on;
    if (on) this.host.setDroidVisible(false);
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
    // After the camera limits are back: showing the droid may bring the booth (and its limits) back.
    if (!on) this.host.setDroidVisible(true);
    this.emit();
  }

  /** Swap the set's lights and environment for the Inspection rig (see INSPECTION). */
  private inspection(on: boolean) {
    const scene = this.host.scene;
    if (on) {
      this.setLights = [];
      scene.traverse((o) => {
        const l = o as THREE.Light;
        if (!l.isLight || !l.visible || this.isOurs(l)) return;
        this.setLights.push(l);
        l.visible = false;
      });
      this.env ??= new THREE.PMREMGenerator(this.host.renderer).fromScene(new RoomEnvironment(), 0.04).texture;
      this.savedEnv = { env: scene.environment, intensity: scene.environmentIntensity };
      this.holdInspection();
    } else {
      for (const l of this.setLights) l.visible = true;
      this.setLights = [];
      if (this.savedEnv) {
        scene.environment = this.savedEnv.env;
        scene.environmentIntensity = this.savedEnv.intensity;
      }
      this.savedEnv = null;
    }
  }

  /** The set may re-light itself while Build is open (a backdrop pick, the booth desk): undo it. */
  private holdInspection() {
    const scene = this.host.scene;
    for (const l of this.setLights) l.visible = false;
    if (this.savedEnv && scene.environment !== this.env) this.savedEnv.env = scene.environment;
    scene.environment = this.env;
    scene.environmentIntensity = INSPECTION.env;
  }

  private isOurs(o: THREE.Object3D) {
    for (let p: THREE.Object3D | null = o; p; p = p.parent) if (p === this.lights || p === this.root) return true;
    return false;
  }

  // ------------------------------------------------------------------ loading

  /** The open manifest's URL (Build reloads it when the workbench rewrites it). */
  url = '';

  load(url: string, keep = false): Promise<void> {
    this.url = url;
    this.loading = this.doLoad(url, keep ? this.snapshot() : null).catch((e) => {
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
  private geoCache = new Map<string, Promise<THREE.BufferGeometry>>();

  /** Reload the open assembly in place (a rebuilt manifest): the camera, the joint values, the
   * variant picks and the focused sub-assembly stay as they were. */
  reload(): Promise<void> {
    return this.url ? this.load(this.url, true) : Promise.resolve();
  }

  private snapshot() {
    const poses: Record<string, Pose> = {};
    this.forEachNode((n) => (poses[n.asm.id] = { ...n.pose }));
    return { poses, variants: { ...this.variants }, focus: this.focus?.asm.id ?? null };
  }

  private async doLoad(url: string, keep: ReturnType<Workbench['snapshot']> | null = null) {
    this.error = '';
    const seq = ++this.loadSeq;
    const m = await loadManifest(url);
    if (seq !== this.loadSeq) return; // a newer pick superseded this one
    this.clear();
    this.manifest = m;
    const loader = new GLTFLoader();
    // a live reload keeps the meshes it already has (keyed by URL + content hash); a fresh load starts over
    const geoCache = keep ? this.geoCache : new Map<string, Promise<THREE.BufferGeometry>>();
    this.geoCache = geoCache;
    const geometry = (u: string) => {
      let g = geoCache.get(u);
      if (!g) {
        g = loader.loadAsync(u).then((gltf) => {
          // Round faces render round, hard edges stay sharp: weld, then crease at 30 deg.
          let geo = meshGeometry(gltf.scene, u);
          geo.deleteAttribute('normal');
          geo = toCreasedNormals(mergeVertices(geo, 1e-4), CREASE);
          saneNormals(geo);
          return geo;
        });
        geoCache.set(u, g);
      }
      return g;
    };
    const parts: PartObj[] = [];
    const fast: FastObj[] = [];
    // only the picked variant options' meshes are fetched now; the others when they are picked
    this.variants = this.defaultPicks(m.root, keep?.variants);
    this.lazyHidden = hiddenAssemblies(m.root, this.variants);
    this.geometryFn = geometry;
    const top = await this.buildNode(m.root, null, geometry, parts, fast);
    if (seq !== this.loadSeq) return;
    await this.loadInterference(url, geometry);
    if (seq !== this.loadSeq) return;
    this.allParts = parts;
    this.allFast = fast;
    this.top = top;
    this.root.add(this.top.group);
    this.top.group.add(this.ifGroup);
    // No highlight knee here (look.ts tameHighlights): it squeezes every lit value above 0.7
    // into 0.85-1.0, which is what flattened the light shells. The Inspection rig keeps them
    // under the bloom threshold instead.
    // The head mech's frame sits where the droid's head is (its mount, mm in the body frame).
    const mt = m.root.mount?.transform?.t ?? [0, 0, 0];
    this.root.position.set(mt[0] / 1000, mt[1] / 1000, mt[2] / 1000);
    this.focus = this.top;
    if (keep) {
      this.forEachNode((n) => {
        const p = keep.poses[n.asm.id];
        if (!p) return;
        for (const j of n.asm.joints ?? []) if (j.id in p) n.pose[j.id] = Math.min(j.limits.max, Math.max(j.limits.min, p[j.id]));
        if (keep.focus === n.asm.id) this.focus = n;
      });
    }
    this.applyVariantNodes();
    this.pose();
    this.refresh();
    if (this.active && !keep) this.frame();
    // for load-time measurement (scripts/build_load_time.mjs): when the open assembly is on screen
    (globalThis as { __r3xBuildLoaded?: { url: string; at: number } }).__r3xBuildLoaded = { url, at: performance.now() };
  }

  /** The suite's interference.json next to the manifest: each pair's shared solid (a GLB) or,
   * for open meshes, a marker at its deepest point. On by itself when any pair is unexplained. */
  private async loadInterference(url: string, geometry: (u: string) => Promise<THREE.BufferGeometry>) {
    this.interference = [];
    this.ifGroup.clear();
    const dir = url.replace(/[^/]*$/, '');
    try {
      const r = await fetch(`${dir}interference.json`, { cache: 'no-store' });
      if (!r.ok) return;
      const doc = (await r.json()) as { pairs?: InterferencePair[] };
      this.interference = doc.pairs ?? [];
    } catch {
      return;
    }
    const hot = new THREE.MeshBasicMaterial({ color: 0xff2a1a, transparent: true, opacity: 0.9, depthTest: false });
    const known = new THREE.MeshBasicMaterial({ color: 0xffa21a, transparent: true, opacity: 0.8, depthTest: false });
    await Promise.all(this.interference.map(async (it, k) => {
      let obj: THREE.Object3D;
      if (it.mesh) {
        const g = await geometry(joinUrl(dir, it.mesh)).catch(() => null);
        obj = g ? new THREE.Mesh(g, it.explained ? known : hot) : new THREE.Object3D();
      } else {
        const m = new THREE.Mesh(new THREE.SphereGeometry(Math.max(1.5, it.depth_mm), 16, 12), it.explained ? known : hot);
        m.position.set(...it.at);
        obj = m;
      }
      obj.renderOrder = 10;
      obj.name = `interference:${k}`;
      obj.userData.interference = k;
      this.ifGroup.add(obj);
    }));
    this.interferenceOn = this.interference.some((p) => !p.explained);
  }

  /** Part id as the viewer knows it (the whole-droid suite prefixes ids that repeat). */
  private pid(id: string) {
    return this.parts.has(id) ? id : id.includes('/') ? id.slice(id.indexOf('/') + 1) : id;
  }

  setInterference(on: boolean) {
    this.interferenceOn = on;
    if (on) this.home();
    this.refresh();
    this.emit();
  }

  /** Frame one pair: its shared solid (or its point) and the two parts. */
  frameInterference(k: number) {
    const it = this.interference[k];
    if (!it) return;
    if (!this.interferenceOn) this.setInterference(true);
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3();
    const o = this.ifGroup.children.find((c) => c.userData.interference === k);
    if (o) box.expandByObject(o);
    for (const id of [it.a, it.b]) {  // and the two parts, so the pair reads in context
      const po = this.parts.get(this.pid(id));
      if (!po) continue;
      if (!po.mesh.geometry.boundingBox) po.mesh.geometry.computeBoundingBox();
      box.union(po.mesh.geometry.boundingBox!.clone().applyMatrix4(po.mesh.matrixWorld));
    }
    this.selected = this.pid(it.a);
    this.refresh();
    this.emit();
    if (!box.isEmpty()) this.frameBox(box, true);
  }

  private clear() {
    this.root.clear();
    this.parts.clear();
    this.fast.clear();
    this.allParts = [];
    this.allFast = [];
    this.variantHidden.clear();
    this.top = this.focus = null;
    this.selected = null;
    this.hidden.clear();
    this.isolated = null;
    this.step = -1;
    this.check = null;
    this.contact = null;
  }

  private async buildNode(asm: MAssembly, parent: AsmNode | null, geometry: (u: string) => Promise<THREE.BufferGeometry>,
    parts: PartObj[], fast: FastObj[]): Promise<AsmNode> {
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
      // the mesh's content hash (workbench build) keys the geometry cache, so a live reload refetches only
      // the parts whose mesh changed
      const sig = (p as { mesh_sig?: string }).mesh_sig;
      const url = joinUrl(base, p.mesh) + (sig ? `?v=${sig}` : `?r=${this.loadSeq}`);
      const lazy = this.lazyHidden.has(asm);
      const geo = lazy ? new THREE.BufferGeometry() : await geometry(url);
      const mat = this.material(p.class);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.name = p.id;
      mesh.userData.partId = p.id;
      mesh.castShadow = mesh.receiveShadow = false;
      // Feature edges, drawn over the faces (pushed back a hair by polygonOffset).
      if (!lazy) this.addEdges(mesh, geo);
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
      parts.push({ part: p, node, holder, mesh, mat, base: b, lazy: lazy ? url : undefined });
    }));
    const fastMat = this.material('fastener');
    await Promise.all((asm.fasteners ?? []).filter((f) => f.placed && f.mesh && f.transform).map(async (f) => {
      const furl = joinUrl(base, f.mesh!);
      const flazy = this.lazyHidden.has(asm);
      const geo = flazy ? new THREE.BufferGeometry() : await geometry(furl);
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
      fast.push({ f, node, obj, base: m, mat, holder, lazy: flazy ? furl : undefined });
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
    return new THREE.MeshStandardMaterial({ ...look, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
  }

  private lazyHidden = new Set<MAssembly>();
  private geometryFn: ((u: string) => Promise<THREE.BufferGeometry>) | null = null;

  /** Variant picks for a tree: a group can span nodes (the droid's `internals`: the column under the
   * base, Anderson's ring drives under the rings), so a default option anywhere wins over another
   * node's first option; `keep` (a live reload) overrides where the group still exists. */
  private defaultPicks(root: MAssembly, keep?: Record<string, string>) {
    const picks: Record<string, string> = {};
    const defaulted = new Set<string>();
    const walk = (a: MAssembly) => {
      for (const v of variantOptions(a)) {
        if (defaulted.has(v.group)) continue;
        if (v.default) { picks[v.group] = v.id; defaulted.add(v.group); } else if (!(v.group in picks)) picks[v.group] = v.id;
      }
      for (const c of (a.children ?? []) as MAssembly[]) walk(c);
    };
    walk(root);
    for (const [g, id] of Object.entries(keep ?? {})) if (g in picks) picks[g] = id;
    return picks;
  }

  /** Fetch the meshes of parts that became visible (a variant just picked). */
  private loadShown() {
    const geometry = this.geometryFn;
    if (!geometry) return;
    const seq = this.loadSeq;
    for (const po of this.allParts) {
      if (!po.lazy || this.variantHidden.has(po.node)) continue;
      const u = po.lazy;
      po.lazy = undefined;
      void geometry(u).then((geo) => {
        if (seq !== this.loadSeq) return;
        po.mesh.geometry = geo;
        this.addEdges(po.mesh, geo);
        this.refresh();
        this.emit();
      });
    }
    for (const fo of this.allFast) {
      if (!fo.lazy || this.variantHidden.has(fo.node)) continue;
      const u = fo.lazy;
      fo.lazy = undefined;
      void geometry(u).then((geo) => {
        if (seq !== this.loadSeq) return;
        fo.obj.geometry = geo;
        this.refresh();
      });
    }
  }

  private addEdges(mesh: THREE.Mesh, geo: THREE.BufferGeometry) {
    let eg = this.edgeGeo.get(geo);
    if (!eg) this.edgeGeo.set(geo, (eg = new THREE.EdgesGeometry(geo, EDGE_ANGLE)));
    const edges = new THREE.LineSegments(eg, this.edgeMat);
    edges.name = 'edges';
    edges.raycast = () => {};
    mesh.add(edges);
  }

  /** Child variants: hide the unpicked options' subtrees and index the shown parts by id first. */
  private applyVariantNodes() {
    this.variantHidden.clear();
    if (!this.top) return;
    const hidden = hiddenAssemblies(this.top.asm, this.variants);
    this.forEachNode((n) => {
      if (hidden.has(n.asm)) this.variantHidden.add(n);
    });
    this.forEachNode((n) => (n.group.visible = !hidden.has(n.asm)));
    this.parts = new Map();
    this.fast = new Map();
    for (const shown of [true, false]) {
      for (const po of this.allParts) if (this.variantHidden.has(po.node) !== shown && !this.parts.has(po.part.id)) this.parts.set(po.part.id, po);
      for (const fo of this.allFast) if (this.variantHidden.has(fo.node) !== shown && !this.fast.has(fo.f.id)) this.fast.set(fo.f.id, fo);
    }
  }

  /** Whether a node is in the model under the current variant picks. */
  nodeShown(n: AsmNode) {
    return !this.variantHidden.has(n);
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
    for (const po of this.allParts) {
      const st = po.part.stretch;
      if (!st) continue;
      const s = Math.max(0.3, (st.rest_mm + (po.node.pose[st.joint] ?? 0)) / st.rest_mm);
      po.mesh.scale.set(1, s, 1);
      po.sBase = po.base.clone();
      po.sBase.y = st.anchor[1] + (po.base.y - st.anchor[1]) * s;
      po.mesh.position.copy(po.sBase).addScaledVector(new THREE.Vector3(...(po.part.explode ?? [0, 0, 0])), (po.part.explode_mm ?? 0) * this.explode);
    }
    this.root.updateMatrixWorld(true);
  }

  private poseLinkage(n: AsmNode, lk: MLinkage, ms: Map<string, THREE.Matrix4>) {
    const s = solveRod(lk, ms.get(lk.horn.link)!, ms.get(lk.ground.link)!);
    n.rods.set(lk.id, s ? { servoDeg: s.servoDeg } : null);
    if (!s) return; // out of reach: the rod keeps its last pose, the panel says so
    const zero = n.rodZero.get(lk.id);
    for (const fo of this.allFast) {
      if (fo.node !== n || fo.f.linkage !== lk.id || !fo.holder) continue;
      fo.holder.matrix.copy(fo.f.role === 'horn' ? hornMatrix(lk, ms.get(lk.horn.link)!, s.servoDeg)
        : zero ? rodMatrix(zero.a, zero.b, s.a, s.b) : new THREE.Matrix4());
    }
    for (const pid of lk.parts) {
      const po = this.allParts.find((x) => x.node === n && x.part.id === pid);
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
    this.applyVariantNodes();
    this.loadShown();
    if (this.focus && this.variantHidden.has(this.focus)) this.focus = this.top;
    this.pose();
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
    this.holdInspection();
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
    const ifHot = new Set<string>();
    const ifKnown = new Set<string>();
    if (this.interferenceOn) {
      for (const it of this.interference) for (const id of [it.a, it.b]) (it.explained ? ifKnown : ifHot).add(this.pid(id));
    }
    this.ifGroup.visible = this.interferenceOn;

    for (const [id, po] of this.parts) {
      const p = po.part;
      let visible = !hideV.has(id) && !this.hidden.has(id) && (!this.isolated || this.isolated.has(id)) && !this.variantHidden.has(po.node);
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
      if (this.interferenceOn && !cur && !this.check) {
        if (ifHot.has(id)) { m.emissive.setHex(HIGHLIGHT.fail); opacity = Math.min(opacity, 0.55); }
        else if (ifKnown.has(id)) { m.emissive.setHex(HIGHLIGHT.warn); opacity = Math.min(opacity, 0.55); }
        else opacity = Math.min(opacity, 0.14);
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
      const edges = po.mesh.getObjectByName('edges');
      if (edges) edges.visible = opacity >= 0.99;
    }
    if ((clip?.length ?? 0) !== (this.edgeMat.clippingPlanes?.length ?? 0)) this.edgeMat.needsUpdate = true;
    this.edgeMat.clippingPlanes = clip;
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
      po.mesh.position.copy(po.sBase ?? po.base).addScaledVector(dir, (p.explode_mm ?? 0) * k);
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
