/**
 * Build mode's 3D side: loads a mech manifest (mech/out/<assembly>/manifest.json) into the
 * same three.js scene as the droid, at the droid's own head position, and poses it. Sim only:
 * nothing here talks to the gateway or the performer, so Build never drives hardware.
 *
 * Structure: one THREE.Group per assembly (child assemblies ride their parent's link), one
 * matrix-driven group per link (kinematics.ts), a mesh per part under its link, horn and rod
 * parts posed by the closed-form push-rod solve. How the parts are drawn (the look - Exterior,
 * Mechanism, Inspect -, the scope in focus and its context, explode, section, fasteners,
 * selection, isolate, the hovered joint's moving parts, step and check highlights) is
 * recomputed from this object's state in `refresh()`, so every control is one setter.
 *
 * Navigation (systems.ts): the whole build, or a scope - a motion system, an assembly, or one
 * library design - drawn solid while the rest ghosts or hides; Esc steps back out (`back()`).
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeVertices, toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { gearMatrix, hornMatrix, linkMatrices, rodMatrix, solveRod, type Pose } from './kinematics';
import {
  assemblyLabel, exposed, exteriorFinish, isKitPart, libraryParts, MATERIAL, mechanismFinish, motionSystems, movedBy, subtreeParts,
  type Finish, type LibraryItem, type Look, type MotionSystem, type SysJoint,
} from './systems';
import {
  driveFor, groundFor, meshGeometry, variantOptions, hiddenAssemblies, hiddenByVariants, joinUrl, loadManifest,
  type MAssembly, type MCheck, type MFastener, type MGear, type Manifest, type MLinkage, type MPart, type MStep,
} from './manifest';

export type { Look } from './systems';
export type ShellMode = 'solid' | 'xray' | 'hidden';
export type Axis = 'x' | 'y' | 'z';
export type Context = 'ghost' | 'hide';

/** What is in focus: drawn solid, the rest ghosted or hidden (`context`). */
export interface Scope {
  kind: 'system' | 'assembly' | 'library';
  id: string;
  label: string;
  parts: Set<string>;
  /** Whose steps, checks and BOM the panel shows. */
  owner: AsmNode;
  joints: SysJoint<AsmNode>[];
  /** Actuators and linkage parts of the scope's joints. */
  drive: Set<string>;
  item?: LibraryItem;
}

export interface BuildHost {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  renderer: THREE.WebGLRenderer;
  /** Draw at full rate for a moment (pacer). */
  interact(): void;
  /** The droid model (and its overlays) while Build is open. */
  setDroidVisible(on: boolean): void;
  /** The Quality setting: shadows scale with it (off on Performance). */
  quality?(): 'performance' | 'balanced' | 'high';
  /** A clean image for inspection while Build is open (no grain or lens fringing). */
  setCleanImage?(on: boolean): void;
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
  /** Unique: the path of assembly ids (an id may repeat under two variant options). */
  key: string;
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
  /** Gear parts and fasteners, each in a holder on its link, turned by their joint. */
  gearHolders: { gear: MGear; holder: THREE.Object3D }[];
}

const CREASE = (30 * Math.PI) / 180;
/** Feature edges drawn over the parts: folds sharper than this. */
const EDGE_ANGLE = 40;

/**
 * Build's lighting, "Inspection": a CAD viewport's neutral rig - a soft key from above front
 * right (casting the ground contact shadow), a fill from the left, a rim from behind and a
 * room environment for the metals to reflect - that replaces the set's lights while Build is
 * open. The Scene panel's Lighting levels still apply (key / fill / rim / ambient), by name.
 */
const INSPECTION = { key: 1.6, fill: 0.5, rim: 0.9, hemi: 0.22, env: 0.75 };
/** Status colours (checks, interference) stay apart from the accent, which means "selected" or
 *  "moves with the joint you are on". */
const HIGHLIGHT = { step: 0x4aa3ff, selected: 0xe8762a, fail: 0xff3b30, warn: 0xf2c230 };
/** The context around a scope, and the shells over the mechanism in Inspect. */
const GHOST = { color: 0x7f858e, mech: 0.08, shell: 0.045, inspectShell: 0.08 };
const KEY_DIR = new THREE.Vector3(0.9, 1.7, 1.3).normalize();

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
  /** Exterior (shells in paint), Mechanism (shells off, material colours), Inspect (ghosted, checks). */
  look: Look = 'exterior';
  /** Around a scope: the rest of the droid as a light ghost, or hidden. */
  context: Context = 'ghost';
  scope: Scope | null = null;
  /** A joint under the pointer in the panel: the parts it moves, and its drive. */
  hover: { node: AsmNode; joint: string; moved: Set<string>; drive: Set<string> } | null = null;
  /** The build's own variant picks while a library design borrows the model. */
  private buildPicks: Record<string, string> | null = null;
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
  private readonly edgeMat = new THREE.LineBasicMaterial({ color: 0x0b0c10, transparent: true, opacity: 0.42, depthWrite: false });
  /** Shells are dense kit meshes: their folds draw fainter. */
  private readonly shellEdgeMat = new THREE.LineBasicMaterial({ color: 0x0b0c10, transparent: true, opacity: 0.12, depthWrite: false });
  private readonly key: THREE.DirectionalLight;
  private shadowQ = '';
  private fly: { p0: THREE.Vector3; t0: THREE.Vector3; p1: THREE.Vector3; t1: THREE.Vector3; start: number; dur: number } | null = null;
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
    key.position.copy(KEY_DIR).multiplyScalar(2);
    // The ground contact shadow. The map is redrawn only when the model changes (pose, view
    // state), never for a camera move: see markShadow().
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.002;
    this.key = key;
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
    // Build's shadow only changes with the model, so it is not redrawn every frame.
    this.host.renderer.shadowMap.autoUpdate = !on;
    this.host.renderer.shadowMap.needsUpdate = true;
    this.host.setCleanImage?.(on);
    this.shadowQ = '';
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
      this.fly = null;
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
    this.forEachNode((n) => (poses[n.key] = { ...n.pose }));
    const sc = this.scope;
    return { poses, variants: { ...this.variants }, buildPicks: this.buildPicks, scope: sc ? { kind: sc.kind, id: sc.id, item: sc.item, owner: sc.owner.key } : null };
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
    this.buildPicks = keep?.buildPicks ?? null;
    this.lazyHidden = hiddenAssemblies(m.root, this.variants);
    this.geometryFn = geometry;
    const top = await this.buildNode(m.root, null, geometry, parts, fast);
    if (seq !== this.loadSeq) return;
    this.interference = [];
    this.ifGroup.clear();
    this.allParts = parts;
    this.allFast = fast;
    this.top = top;
    this.root.add(this.top.group);
    this.top.group.add(this.ifGroup);
    // No highlight knee here (look.ts tameHighlights): it squeezes every lit value above 0.7
    // into 0.85-1.0, which is what flattened the light shells. The Inspection rig keeps them
    // under the bloom threshold instead.
    // The head mech's frame sits where the droid's head is (its mount, mm in the body frame).
    this.focus = this.top; // (the root's position: applyVariantNodes, from the mount and the ground)
    if (keep) {
      this.forEachNode((n) => {
        const p = keep.poses[n.key];
        if (!p) return;
        for (const j of n.asm.joints ?? []) if (j.id in p) n.pose[j.id] = Math.min(j.limits.max, Math.max(j.limits.min, p[j.id]));
      });
    }
    this.applyVariantNodes();
    this.pose();
    // a live reload keeps the scope (rebuilt against the new tree), a fresh load opens on the whole build
    const sc = keep?.scope;
    const again = sc && (sc.kind === 'system' ? this.systemScope(sc.id)
      : sc.kind === 'library' && sc.item ? this.libraryScope(sc.item)
        : this.nodeByKey(sc.owner) ? this.assemblyScope(this.nodeByKey(sc.owner)!) : null);
    this.applyScope(again ?? null, false);
    this.fitShadow();
    if (this.active && !keep) this.frame();
    // for load-time measurement (scripts/build_load_time.mjs): when the open assembly is on screen
    (globalThis as { __r3xBuildLoaded?: { url: string; at: number } }).__r3xBuildLoaded = { url, at: performance.now() };
    // After the first view: feature edges (most of a cold load's CPU) and the suite's overlaps
    // (Inspect only) arrive in the background.
    this.drainEdges(seq);
    void this.loadInterference(url, geometry).then(() => {
      if (seq !== this.loadSeq) return;
      this.refresh();
      this.emit();
    });
  }

  /** The suite's interference.json next to the manifest: each pair's shared solid (a GLB) or,
   * for open meshes, a marker at its deepest point. Drawn only in Inspect, depth-tested (it sits
   * inside the two parts, which Inspect ghosts), each with an outline so it reads on any ground. */
  private async loadInterference(url: string, geometry: (u: string) => Promise<THREE.BufferGeometry>) {
    this.interference = [];
    this.ifGroup.clear();
    const dir = url.replace(/[^/]*$/, '');
    try {
      const r = await fetch(`${dir}interference.json`, { cache: 'no-store' });
      if (!r.ok) return;
      const doc = (await r.json()) as { pairs?: InterferencePair[] };
      if (this.url !== url) return;
      this.interference = doc.pairs ?? [];
    } catch {
      return;
    }
    const fill = (c: number) => new THREE.MeshStandardMaterial({ color: c, emissive: c, emissiveIntensity: 0.55, roughness: 0.6, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 });
    const hot = fill(HIGHLIGHT.fail);
    const known = fill(HIGHLIGHT.warn);
    const line = (c: number) => new THREE.LineBasicMaterial({ color: c });
    const hotLine = line(0x5c0b06);
    const knownLine = line(0x5a4706);
    const hull = (c: number) => new THREE.MeshBasicMaterial({ color: c, side: THREE.BackSide });
    await Promise.all(this.interference.map(async (it, k) => {
      const obj = new THREE.Group();
      if (it.mesh) {
        const g = await geometry(joinUrl(dir, it.mesh)).catch(() => null);
        if (g) {
          obj.add(new THREE.Mesh(g, it.explained ? known : hot));
          const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 25), it.explained ? knownLine : hotLine);
          edges.raycast = () => {};
          obj.add(edges);
        }
      } else {
        const r = Math.max(1.5, it.depth_mm);
        const ball = new THREE.Mesh(new THREE.SphereGeometry(r, 16, 12), it.explained ? known : hot);
        const rim = new THREE.Mesh(new THREE.SphereGeometry(r * 1.22, 16, 12), hull(it.explained ? 0x5a4706 : 0x5c0b06));
        obj.add(ball, rim);
        obj.position.set(...it.at);
      }
      obj.name = `interference:${k}`;
      obj.userData.interference = k;
      this.ifGroup.add(obj);
    }));
    // Shown only in Inspect (see refresh); the toggle there starts on.
    this.interferenceOn = true;
  }

  /** Overlap pairs in focus: those touching the scope's parts (every pair without a scope). */
  pairInScope(it: InterferencePair) {
    const sc = this.scope?.parts;
    return !sc || sc.has(this.pid(it.a)) || sc.has(this.pid(it.b));
  }

  /** Part id as the viewer knows it (the whole-droid suite prefixes ids that repeat). */
  private pid(id: string) {
    return this.parts.has(id) ? id : id.includes('/') ? id.slice(id.indexOf('/') + 1) : id;
  }

  setInterference(on: boolean) {
    this.interferenceOn = on;
    if (on && this.look !== 'inspect') this.look = 'inspect';
    if (on) this.home();
    this.refresh();
    this.emit();
  }

  /** Frame one pair: its shared solid (or its point) and the two parts. */
  frameInterference(k: number) {
    const it = this.interference[k];
    if (!it) return;
    if (!this.interferenceOn || this.look !== 'inspect') this.setInterference(true);
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3();
    const o = this.ifGroup.children.find((c) => c.userData.interference === k);
    if (o) box.expandByObject(o);
    // the shared solid with room around it (a ring shell would make the pair a whole-droid view);
    // the two parts only when there is no solid
    if (!box.isEmpty()) {
      const sz = box.getSize(new THREE.Vector3());
      box.expandByScalar(Math.max(0.03, Math.max(sz.x, sz.y, sz.z) * 1.5));
    }
    for (const id of box.isEmpty() ? [it.a, it.b] : []) {
      const po = this.parts.get(this.pid(id));
      if (!po) continue;
      if (!po.mesh.geometry.boundingBox) po.mesh.geometry.computeBoundingBox();
      box.union(po.mesh.geometry.boundingBox!.clone().applyMatrix4(po.mesh.matrixWorld));
    }
    this.refresh();
    this.emit();
    if (!box.isEmpty()) this.frameBox(box, true, true);
  }

  private clear() {
    this.root.clear();
    this.parts.clear();
    this.fast.clear();
    this.allParts = [];
    this.allFast = [];
    this.variantHidden.clear();
    this.top = this.focus = null;
    this.scope = null;
    this.hover = null;
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
    const key = parent ? `${parent.key}/${asm.id}` : asm.id;
    const node: AsmNode = { key, asm, group, links: new Map(), pose: {}, parent, children: [], rodZero: new Map(), rods: new Map(), gearHolders: [] };
    for (const l of asm.links) {
      const g = new THREE.Group();
      g.name = `link:${l.id}`;
      g.matrixAutoUpdate = false;
      group.add(g);
      node.links.set(l.id, g);
    }
    const base = asm.base ?? '/';
    const gearOf = new Map<string, MGear>();
    for (const g of asm.gears ?? []) for (const id of [...g.parts, ...(g.fasteners ?? [])]) gearOf.set(id, g);
    await Promise.all(asm.parts.map(async (p) => {
      // the mesh's content hash (workbench build) keys the geometry cache, so a live reload refetches only
      // the parts whose mesh changed
      const sig = (p as { mesh_sig?: string }).mesh_sig;
      const url = joinUrl(base, p.mesh) + (sig ? `?v=${sig}` : `?r=${this.loadSeq}`);
      const lazy = this.lazyHidden.has(asm);
      const geo = lazy ? new THREE.BufferGeometry() : await geometry(url);
      const mat = this.material();
      const mesh = new THREE.Mesh(geo, mat);
      mesh.name = p.id;
      mesh.userData.partId = p.id;
      mesh.castShadow = mesh.receiveShadow = false;
      // Feature edges, drawn over the faces (pushed back a hair by polygonOffset).
      if (!lazy) this.addEdges(mesh, geo, p.class === 'shell');
      const b = new THREE.Vector3(...p.transform.t);
      mesh.position.copy(b);
      let holder: THREE.Object3D = mesh;
      const gear = gearOf.get(p.id);
      if (p.linkage) {
        holder = new THREE.Group();
        holder.matrixAutoUpdate = false;
        holder.add(mesh);
        group.add(holder);
      } else if (gear) {
        // turns about its own axle on its link (pose(): gearMatrix)
        holder = new THREE.Group();
        holder.matrixAutoUpdate = false;
        holder.add(mesh);
        (node.links.get(gear.link) ?? node.links.get(p.link) ?? group).add(holder);
        node.gearHolders.push({ gear, holder });
      } else {
        (node.links.get(p.link) ?? group).add(mesh);
      }
      parts.push({ part: p, node, holder, mesh, mat, base: b, lazy: lazy ? url : undefined });
    }));
    const fastMat = this.material(MATERIAL.fastener);
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
      const fgear = gearOf.get(f.id);
      if (f.linkage) {
        holder = new THREE.Group();
        holder.matrixAutoUpdate = false;
        holder.add(obj);
        group.add(holder);
      } else if (fgear) {
        holder = new THREE.Group();
        holder.matrixAutoUpdate = false;
        holder.add(obj);
        (node.links.get(fgear.link) ?? node.links.get(f.link) ?? group).add(holder);
        node.gearHolders.push({ gear: fgear, holder });
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

  private material(f: Finish = MATERIAL.printed) {
    return new THREE.MeshStandardMaterial({ ...f, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
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
        this.addEdges(po.mesh, geo, po.part.class === 'shell');
        this.drainEdges(seq);
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

  /** Feature edges wait in a queue and are built a few milliseconds at a time (drainEdges). */
  private edgeQueue: { mesh: THREE.Mesh; geo: THREE.BufferGeometry; shell: boolean; seq: number }[] = [];
  private drainSeq = -1;

  private addEdges(mesh: THREE.Mesh, geo: THREE.BufferGeometry, shell: boolean) {
    this.edgeQueue.push({ mesh, geo, shell, seq: this.loadSeq });
  }

  private buildEdges({ mesh, geo, shell }: { mesh: THREE.Mesh; geo: THREE.BufferGeometry; shell: boolean }) {
    if (mesh.geometry !== geo || mesh.getObjectByName('edges')) return;
    let eg = this.edgeGeo.get(geo);
    if (!eg) this.edgeGeo.set(geo, (eg = new THREE.EdgesGeometry(geo, EDGE_ANGLE)));
    const edges = new THREE.LineSegments(eg, shell ? this.shellEdgeMat : this.edgeMat);
    edges.name = 'edges';
    edges.raycast = () => {};
    edges.visible = !(mesh.material as THREE.Material).transparent;
    mesh.add(edges);
  }

  /** Build queued edges in ~10 ms slices between frames, the parts in view first. One loop per
   *  load: a newer load's loop takes over and drops the older load's leftovers. */
  private drainEdges(seq = this.loadSeq) {
    if (this.drainSeq === seq) return;
    this.drainSeq = seq;
    const step = () => {
      if (this.drainSeq !== seq) return;
      this.edgeQueue = this.edgeQueue.filter((e) => e.seq === this.loadSeq);
      const t0 = performance.now();
      this.edgeQueue.sort((a, b) => Number(b.mesh.visible) - Number(a.mesh.visible));
      while (this.edgeQueue.length && performance.now() - t0 < 10) this.buildEdges(this.edgeQueue.shift()!);
      this.host.interact();
      if (this.edgeQueue.length) setTimeout(step, 0);
      else this.drainSeq = -1;
    };
    setTimeout(step, 0);
  }

  /** Child variants: hide the unpicked options' subtrees and index the shown parts by id first. */
  private applyVariantNodes() {
    this.variantHidden.clear();
    if (!this.top) return;
    // drives that depend on a pick (the rings' servos follow `internals`): the picked option's
    this.forEachNode((n) => n.asm.joints.forEach((j) => { if (j.drive?.variants) j.drive = driveFor(j, this.variants); }));
    // the floor where the picked internals put it (the model rises so its ground stands on y = 0)
    const g = groundFor(this.top.asm, this.variants);
    const mt = this.top.asm.mount?.transform?.t ?? [0, 0, 0];
    this.root.position.set(mt[0] / 1000, (mt[1] - (g ?? 0)) / 1000, mt[2] / 1000);
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

  /** A node by assembly id: the shown one first (ids repeat across variant options). */
  nodeOf(asmId: string): AsmNode | null {
    let hit: AsmNode | null = null;
    this.forEachNode((n) => {
      if (n.asm.id === asmId && (!hit || (!this.nodeShown(hit) && this.nodeShown(n)))) hit = n;
    });
    return hit;
  }

  nodeByKey(key: string): AsmNode | null {
    let hit: AsmNode | null = null;
    this.forEachNode((n) => {
      if (n.key === key) hit = n;
    });
    return hit ?? this.nodeOf(key);
  }

  // ------------------------------------------------------------------ scope (systems.ts)

  private sysCache: { sig: string; list: MotionSystem<AsmNode>[] } | null = null;

  /** The fitted model's motion systems (Head, Visor, Neck, the rings, the arms). */
  systems(): MotionSystem<AsmNode>[] {
    if (!this.top) return [];
    const sig = `${this.loadSeq}|${JSON.stringify(this.variants)}`;
    if (this.sysCache?.sig !== sig) this.sysCache = { sig, list: motionSystems(this.top, (n) => this.nodeShown(n)) };
    return this.sysCache.list;
  }

  systemScope(id: string): Scope | null {
    const s = this.systems().find((x) => x.id === id);
    return s ? { kind: 'system', id, label: s.name, parts: s.parts, owner: s.owner, joints: s.joints, drive: s.drive } : null;
  }

  assemblyScope(node: AsmNode): Scope {
    const joints: SysJoint<AsmNode>[] = [];
    const drive = new Set<string>();
    this.forEachNode((n) => {
      if (!this.nodeShown(n)) return;
      for (const j of n.asm.joints) {
        if (j.type === 'fixed' || !(j.limits.max > j.limits.min)) continue;
        joints.push({ node: n, joint: j });
        j.drive?.servos?.forEach((x) => drive.add(x));
      }
    }, node);
    return { kind: 'assembly', id: node.key, label: assemblyLabel(node.asm.name), parts: subtreeParts(node, (n) => this.nodeShown(n)), owner: node, joints, drive };
  }

  /** A library design: the variant picks that put it in the model, its parts isolated. */
  libraryScope(item: LibraryItem): Scope | null {
    if (!this.top) return null;
    const groups = new Set<string>();
    this.forEachNode((n) => variantOptions(n.asm).forEach((v) => groups.add(v.group)));
    this.buildPicks ??= { ...this.variants };
    this.variants = { ...this.buildPicks };
    for (const [g, id] of Object.entries(item.picks)) if (groups.has(g)) this.variants[g] = id;
    this.applyVariantNodes();
    this.loadShown();
    const { parts, nodes } = libraryParts(item, this.top, (n) => this.nodeShown(n));
    if (!nodes.length) return null;
    const joints: SysJoint<AsmNode>[] = [];
    const drive = new Set<string>();
    const seen = new Set<AsmNode>();
    for (const root of nodes) {
      this.forEachNode((n) => {
        if (!this.nodeShown(n) || seen.has(n) || (!item.deep && n !== root)) return;
        seen.add(n);
        for (const j of n.asm.joints) {
          if (j.type === 'fixed' || !(j.limits.max > j.limits.min)) continue;
          joints.push({ node: n, joint: j });
          j.drive?.servos?.forEach((x) => drive.add(x));
        }
      }, root);
    }
    return { kind: 'library', id: item.id, label: item.name, parts, owner: nodes[0], joints, drive, item };
  }

  /** Focus a scope (null: the whole build). Library designs borrow the variant picks; anything
   *  else gives the build its own picks back. */
  setScope(sc: Scope | null) {
    if (sc?.kind !== 'library' && this.buildPicks) {
      this.variants = this.buildPicks;
      this.buildPicks = null;
      this.applyVariantNodes();
      this.loadShown();
      this.pose();
      // a system or assembly from the build's own tree: rebuild it against the restored picks
      if (sc?.kind === 'system') sc = this.systemScope(sc.id);
    }
    if (sc?.kind === 'library') this.look = sc.item?.look ?? this.look;
    else if (sc && this.look === 'exterior') this.look = 'mechanism'; // into the mechanics
    this.applyScope(sc, true);
  }

  private applyScope(sc: Scope | null, frame: boolean) {
    this.scope = sc;
    this.focus = sc?.owner ?? this.top;
    this.hover = null;
    this.isolated = null;
    this.step = -1;
    this.check = null;
    this.refresh();
    this.fitShadow(); // a library design may have moved the ground (its picks)
    this.emit();
    if (frame && this.active) this.frame(sc !== null, true);
  }

  /** Esc: a selection first, then out one level (a scope -> the whole build). */
  back(): boolean {
    if (this.selected) {
      this.select(null);
      return true;
    }
    if (this.scope) {
      this.setScope(null);
      return true;
    }
    return false;
  }

  setLook(l: Look) {
    if (l === this.look) return;
    this.look = l;
    this.refresh();
    this.emit();
  }

  setContext(c: Context) {
    this.context = c;
    this.refresh();
    this.emit();
  }

  /** The joint under the pointer in the panel (null: none): tints what it moves. */
  setHover(node: AsmNode | null, joint: string | null) {
    if (!node || !joint) {
      if (!this.hover) return;
      this.hover = null;
    } else {
      if (this.hover?.node === node && this.hover.joint === joint) return;
      const j = node.asm.joints.find((x) => x.id === joint);
      const drive = new Set<string>(j?.drive?.servos ?? []);
      for (const lid of j?.drive?.linkages ?? []) node.asm.linkages?.find((l) => l.id === lid)?.parts.forEach((p) => drive.add(p));
      this.hover = { node, joint, moved: movedBy(node, joint, (n) => this.nodeShown(n), this.top), drive };
    }
    this.refresh();
    this.host.interact();
  }

  /** Shells as the look draws them (the panel's and picking's view of it). */
  get shell(): ShellMode {
    return this.look === 'exterior' ? 'solid' : this.look === 'inspect' ? 'xray' : 'hidden';
  }

  /** Steps of the focused assembly, those touching the scope's parts when a scope is set. */
  get steps(): MStep[] {
    const all = this.focus?.asm.steps ?? [];
    const sc = this.scope;
    if (!sc || sc.kind === 'assembly') return all;
    const hit = all.filter((s) => (s.parts ?? []).some((p) => sc.parts.has(p)));
    return hit.length ? hit : all;
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
    // gears: a pinion turns with its joint, which may be another assembly's (a ring's sector)
    this.forEachNode((n) => {
      for (const { gear, holder } of n.gearHolders) {
        const src = gear.joint_assembly ? this.nodeOf(gear.joint_assembly) : n;
        gearMatrix(gear, src?.pose[gear.joint] ?? 0, holder.matrix);
      }
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
    this.markShadow();
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

  /** Focus an assembly's subtree (the root: the whole build). */
  setFocus(node: AsmNode) {
    this.setScope(node === this.top ? null : this.assemblyScope(node));
  }

  setVariant(group: string, id: string) {
    this.variants[group] = id;
    this.applyVariantNodes();
    this.loadShown();
    this.pose();
    this.fitShadow();
    // the scope follows the picks: a system is rebuilt from the new tree, a scope that left it closes
    const sc = this.scope;
    if (sc && sc.kind !== 'library') this.applyScope(sc.kind === 'system' ? this.systemScope(sc.id) : this.variantHidden.has(sc.owner) ? null : this.assemblyScope(sc.owner), false);
    else {
      this.refresh();
      this.emit();
    }
  }

  /** Steps tab: go to step `i` of the focused assembly (-1 leaves step mode). */
  setStep(i: number) {
    const steps = this.steps;
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
      if (this.look === 'exterior') this.look = 'inspect'; // a check is about the inside
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
    if (this.fly) {
      const f = this.fly;
      const k = Math.min(1, (now - f.start) / f.dur);
      const e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
      this.host.camera.position.lerpVectors(f.p0, f.p1, e);
      this.host.controls.target.lerpVectors(f.t0, f.t1, e);
      if (k >= 1) this.fly = null;
      moving = true;
    }
    if ((this.host.quality?.() ?? 'balanced') !== this.shadowQ) this.fitShadow();
    if (this.anim) {
      const k = Math.min(1, (now - this.anim.from) / this.anim.dur);
      this.applyOffsets(1 - k);
      if (k >= 1) this.anim = null;
      moving = true;
    }
    if (moving) this.host.interact();
  }

  // ------------------------------------------------------------------ drawing

  /** The step (in `steps`) where each part first appears. */
  private stepIndex() {
    const out = new Map<string, number>();
    this.steps.forEach((st, i) => (st.parts ?? []).forEach((p) => { if (!out.has(p)) out.set(p, i); }));
    return out;
  }

  /** Visibility, finishes, clipping and offsets from the state above. */
  refresh() {
    if (!this.top) return;
    const hideV = new Set<string>();
    this.forEachNode((n) => hiddenByVariants(n.asm, this.variants).forEach((p) => hideV.add(p)));
    const steps = this.steps;
    const cur = this.step >= 0 ? steps[this.step] : null;
    const first = this.stepIndex();
    const inStep = new Set(cur?.parts ?? []);
    const ctx = new Set(cur?.context ?? []);
    const checkParts = new Set(this.check?.parts ?? []);
    const contact = new Set(this.contact?.parts ?? []);
    const clip = this.section.on ? [this.sectionPlane()] : null;
    const look = this.look;
    const scope = this.scope?.parts ?? null;
    // a library design stands alone; a system or assembly keeps the droid around it (or not)
    const ghosts = !!scope && this.context === 'ghost' && this.scope?.kind !== 'library';
    const intf = look === 'inspect' && this.interferenceOn && !cur && !this.check;
    const ifHot = new Set<string>();
    const ifKnown = new Set<string>();
    const pairOn = (it: InterferencePair) => this.pairInScope(it);
    if (intf) for (const it of this.interference) if (pairOn(it)) for (const id of [it.a, it.b]) (it.explained ? ifKnown : ifHot).add(this.pid(id));
    this.ifGroup.visible = intf;
    for (const o of this.ifGroup.children) o.visible = pairOn(this.interference[o.userData.interference as number]);
    const hover = this.hover;

    for (const [id, po] of this.parts) {
      const p = po.part;
      const shell = p.class === 'shell';
      let visible = !hideV.has(id) && !this.hidden.has(id) && (!this.isolated || this.isolated.has(id)) && !this.variantHidden.has(po.node);
      const inScope = !scope || scope.has(id);
      // the look: Exterior is the shells, Mechanism what is inside them, Inspect both (shells ghosted)
      const outside = exposed(p);
      if (look === 'exterior' && !outside) visible = false;
      if (look === 'mechanism' && shell && !scope) visible = false; // in a scope its own shells stay, ghosted
      if (!inScope && !ghosts) visible = false;
      const f = first.get(id);
      if (cur && f !== undefined && f > this.step && !ctx.has(id)) visible = false;
      po.holder.visible = visible;
      po.mesh.visible = visible;
      const m = po.mat;
      let finish: Finish;
      let opacity = 1;
      if (!inScope) {
        finish = MATERIAL.neutral;
        opacity = shell ? GHOST.shell : GHOST.mech;
      } else if (look === 'exterior' && outside && !shell) {
        finish = isKitPart(p) ? exteriorFinish(p, po.node.asm.id) : mechanismFinish(p);
      } else if (shell) {
        finish = exteriorFinish(p, po.node.asm.id);
        if (look === 'inspect') opacity = GHOST.inspectShell;
        if (look === 'mechanism') opacity = GHOST.shell;
      } else {
        finish = look === 'inspect' ? MATERIAL.neutral : mechanismFinish(p);
      }
      if (!inScope) m.color.setHex(GHOST.color);
      else m.color.setHex(finish.color);
      m.metalness = inScope ? finish.metalness : 0;
      m.roughness = inScope ? finish.roughness : 1;
      m.emissive.setHex(0x000000);
      m.emissiveIntensity = 0.55;
      if (cur && !inStep.has(id) && !ctx.has(id)) opacity = Math.min(opacity, 0.16);
      if (this.check && checkParts.size && !checkParts.has(id)) opacity = Math.min(opacity, 0.16);
      if (cur && inStep.has(id)) m.emissive.setHex(HIGHLIGHT.step);
      if (this.check && checkParts.has(id)) {
        m.emissive.setHex(this.check.status === 'fail' ? HIGHLIGHT.fail : this.check.status === 'warn' ? HIGHLIGHT.warn : HIGHLIGHT.step);
        opacity = 1;
      }
      if (intf && (ifHot.has(id) || ifKnown.has(id))) {
        // the pair's parts: a tinted ghost, so the shared solid inside them shows
        m.color.setHex(MATERIAL.neutral.color);
        m.emissive.setHex(ifHot.has(id) ? HIGHLIGHT.fail : HIGHLIGHT.warn);
        m.emissiveIntensity = 0.22;
        opacity = 0.32;
      }
      if (contact.has(id)) {
        m.emissive.setHex(this.contact?.status === 'fail' ? HIGHLIGHT.fail : HIGHLIGHT.warn);
        opacity = Math.max(opacity, 0.5);
      }
      // the joint under the pointer: what it moves in the accent, its drive fainter
      if (hover && visible) {
        if (hover.moved.has(id)) {
          m.emissive.setHex(HIGHLIGHT.selected);
          m.emissiveIntensity = 0.42;
          opacity = Math.max(opacity, shell ? 0.35 : 1);
        } else if (hover.drive.has(id)) {
          m.emissive.setHex(HIGHLIGHT.selected);
          m.emissiveIntensity = 0.18;
        }
      }
      if (this.selected === id) {
        m.emissive.setHex(HIGHLIGHT.selected);
        m.emissiveIntensity = 0.55;
        opacity = Math.max(opacity, 0.85);
      }
      setLook(m, opacity, clip);
      po.mesh.renderOrder = opacity < 1 ? 2 : 0;
      po.mesh.castShadow = visible && opacity >= 0.99;
      const edges = po.mesh.getObjectByName('edges');
      if (edges) edges.visible = opacity >= 0.99;
    }
    for (const em of [this.edgeMat, this.shellEdgeMat]) {
      if ((clip?.length ?? 0) !== (em.clippingPlanes?.length ?? 0)) em.needsUpdate = true;
      em.clippingPlanes = clip;
    }
    const fastIn = new Set(cur?.fasteners ?? []);
    const stepIds = steps.map((st) => st.id);
    for (const [id, fo] of this.fast) {
      const joinsVisible = fo.f.joins.some((p) => this.parts.get(p)?.mesh.visible && (!scope || scope.has(p)));
      let visible = this.fasteners && look !== 'exterior' && joinsVisible && !this.hidden.has(id);
      if (cur && stepIds.indexOf(fo.f.step) > this.step) visible = false;
      if (this.isolated && !fo.f.joins.some((p) => this.isolated!.has(p))) visible = false;
      fo.obj.visible = visible;
      fo.mat.color.setHex(look === 'inspect' ? MATERIAL.neutral.color : MATERIAL.fastener.color);
      fo.mat.emissive.setHex(cur && fastIn.has(id) ? HIGHLIGHT.step : this.selected === id ? HIGHLIGHT.selected : 0);
      fo.mat.emissiveIntensity = 0.7;
      setLook(fo.mat, cur && !fastIn.has(id) ? 0.25 : 1, clip);
      fo.obj.castShadow = visible;
    }
    this.applyOffsets(this.anim ? 1 - Math.min(1, (performance.now() - this.anim.from) / this.anim.dur) : 0);
  }

  /** Explode offsets, plus the insertion animation of the current step (`insert` 1 -> 0). */
  private applyOffsets(insert: number) {
    const cur = this.step >= 0 ? this.steps[this.step] : null;
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
    this.markShadow();
    this.host.interact();
  }

  /** The model changed: redraw the shadow map once (it is not redrawn per frame in Build). */
  private markShadow() {
    if (this.active) this.host.renderer.shadowMap.needsUpdate = true;
  }

  /** Fit the key's shadow camera to the model in view, at the Quality setting's size. */
  private fitShadow() {
    const key = this.key;
    const q = this.host.quality?.() ?? 'balanced';
    this.shadowQ = q;
    key.castShadow = q !== 'performance';
    const size = q === 'high' ? 2048 : 1024;
    if (key.shadow.mapSize.x !== size) {
      key.shadow.mapSize.set(size, size);
      key.shadow.map?.dispose();
      key.shadow.map = null as unknown as THREE.WebGLRenderTarget;
    }
    key.shadow.radius = q === 'high' ? 4 : 2.5;
    const box = this.bounds(true);
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(0.05, box.getSize(new THREE.Vector3()).length() / 2);
    // the floor too: a part above it shadows it
    const reach = r + Math.max(0, c.y);
    key.target.position.copy(c);
    key.position.copy(c).addScaledVector(KEY_DIR, reach * 2 + 0.2);
    key.target.updateMatrixWorld();
    key.updateMatrixWorld();
    const cam = key.shadow.camera;
    cam.left = cam.bottom = -reach * 1.1;
    cam.right = cam.top = reach * 1.1;
    cam.near = 0.01;
    cam.far = reach * 4 + 0.4;
    cam.updateProjectionMatrix();
    this.markShadow();
  }

  /** The section plane in world space, across the visible model's bounds. */
  private sectionPlane() {
    const box = this.bounds(false);
    const ax = { x: 0, y: 1, z: 2 }[this.section.axis];
    const n = new THREE.Vector3().setComponent(ax, this.section.flip ? 1 : -1);
    const at = box.min.getComponent(ax) + (box.max.getComponent(ax) - box.min.getComponent(ax)) * this.section.at;
    return this.plane.set(n, this.section.flip ? -at : at);
  }

  /** World bounds of the visible parts (or of everything), optionally only `ids`. */
  bounds(visibleOnly = true, ids: Set<string> | null = null) {
    const box = new THREE.Box3();
    this.root.updateMatrixWorld(true);
    for (const [id, po] of this.parts) {
      if (visibleOnly && !po.mesh.visible) continue;
      if (ids && !ids.has(id)) continue;
      if (!po.mesh.geometry.boundingBox) po.mesh.geometry.computeBoundingBox();
      if (po.mesh.geometry.boundingBox!.isEmpty()) continue;
      box.union(po.mesh.geometry.boundingBox!.clone().applyMatrix4(po.mesh.matrixWorld));
    }
    return box;
  }

  /** Point the camera at the model in view - the scope's parts when there is a scope - from
   *  3/4 front, or keeping the view direction. `animate`: fly there. */
  frame(keepDirection = false, animate = false) {
    let box = this.scope ? this.bounds(true, this.scope.parts) : this.bounds(true);
    if (box.isEmpty() && this.scope) box = this.bounds(false, this.scope.parts);
    if (!box.isEmpty()) this.frameBox(box, keepDirection, animate);
  }

  private frameBox(box: THREE.Box3, keepDirection: boolean, animate = false) {
    const c = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).max(new THREE.Vector3(0.03, 0.03, 0.03));
    const cam = this.host.camera;
    const dir = keepDirection
      ? cam.position.clone().sub(this.host.controls.target).normalize()
      : new THREE.Vector3(0.62, 0.38, 1).normalize();
    // fit the height and the widest plan extent (any view direction) in the lens as main.ts
    // fits it between the panels (zoom < 1 widens it), with a margin
    const tanV = Math.tan((cam.fov * Math.PI) / 360) / Math.max(0.2, cam.zoom);
    const halfW = Math.hypot(size.x, size.z) / 2;
    const dist = Math.max(size.y / 2 / tanV, halfW / (tanV * 0.95)) * 1.12 + halfW;
    const p1 = c.clone().addScaledVector(dir, dist);
    if (animate && this.active && !reducedMotion()) {
      this.fly = { p0: cam.position.clone(), t0: this.host.controls.target.clone(), p1, t1: c, start: performance.now(), dur: 520 };
    } else {
      this.fly = null;
      this.host.controls.target.copy(c);
      cam.position.copy(p1);
    }
    this.host.interact();
  }

  /** Frame the current step's parts and their context, keeping the view direction. */
  private frameStep() {
    const s = this.steps[this.step];
    const ids = new Set([...(s?.parts ?? []), ...(s?.context ?? [])]);
    const box = new THREE.Box3();
    this.root.updateMatrixWorld(true);
    for (const id of ids) {
      const po = this.parts.get(id);
      if (!po) continue;
      if (!po.mesh.geometry.boundingBox) po.mesh.geometry.computeBoundingBox();
      box.union(po.mesh.geometry.boundingBox!.clone().applyMatrix4(po.mesh.matrixWorld));
    }
    if (!box.isEmpty()) this.frameBox(box, true, true);
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
  // a faint ghost draws its outer faces only: two layers per shell piled up into a white fog
  m.side = opacity < 0.3 ? THREE.FrontSide : THREE.DoubleSide;
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
