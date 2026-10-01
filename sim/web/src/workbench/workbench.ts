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
  assemblyLabel, exposed, exteriorFinish, finishProblem, jointLabel, libraryParts, MATERIAL, mechanismFinish, motionSystems, movedBy, subtreeParts,
  type Finish, type LibraryItem, type Look, type MotionSystem, type SysJoint,
} from './systems';
import { fastenerPose, fastenerTravel, itemU, planSequence, type SeqFastener, type SeqItem, type SeqPart } from './sequence';
import {
  driveFor, groundFor, meshGeometry, variantOptions, hiddenAssemblies, hiddenByVariants, joinUrl, loadManifest,
  type MAssembly, type MCheck, type MFastener, type MGear, type MJoint, type Manifest, type MLinkage, type MPart, type MStep,
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
  /** The full-detail GLB (manifest `mesh_full`), swapped in when in focus or close; `fullState`. */
  full?: string;
  fullState?: 'loading' | 'done';
}

/** A build step as Instructions show it: the manifest's (limited to the focus) or derived from the structure. */
export type AStep = MStep & {
  derived?: boolean; node?: AsmNode; assembly?: string;
  /** Parts placed earlier that this step works on (hardware into them, glue): the step's own `parts`
   *  minus those already placed. */
  targets?: string[];
};

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
const GHOST = { color: 0x7f858e, mech: 0.1, shell: 0.065, inspectShell: 0.1 };
const GHOST_FINISH: Finish = { color: GHOST.color, metalness: 0, roughness: 1 };
const KEY_DIR = new THREE.Vector3(0.9, 1.7, 1.3).normalize();
/** Instructions (guide.ts): what a step adds, in one accent - blue, the one hue no paint on the droid is near (the
 *  kit guide's red sits on top of the orange) - and what the pointer is on, in the same hue with the rest faded. */
export const GUIDE_COLOR = { add: 0x1f6fe5, focus: 0x1f6fe5 };
/** The guide's ground: a light warm grey, close to the page's paper once tone-mapped. */
const GUIDE_BG = 0xe4e2de;
/** The guide floats the section over the floor, like the kit guide's renders: room for a part to come in from below. */
const GUIDE_LIFT = 0.12;
/** How far a step's new parts start out from their seats (x their arrival distance). Fasteners: sequence.ts. */
const GUIDE_PULL = { part: 0.55 };

/** The guide's view of a section: its own parts (not its child assemblies'), framed in the model area. */
export interface GuideState {
  node: AsmNode;
  parts: Set<string>;
  /** The section's title card: the finished sub-assembly, nothing marked. */
  title: boolean;
  /** Parts or fasteners under the pointer in the tray: marked in blue. */
  hover: Set<string> | null;
}

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
  /** Instructions: the current step's index in the section, or -1 (its title card). */
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
  private sweep: { node: AsmNode; joint: string; t0: number; path: [number, number][]; contact: number | null } | null = null;
  private picking = { x: 0, y: 0, down: false };
  /** Instructions open on a section (null: Build as usual). */
  guide: GuideState | null = null;
  /** The screen the model gets in the guide: what the text column (right) or sheet (bottom) and the bars leave. */
  guideRect = { right: 0, bottom: 0, top: 0 };
  /** The step playing (sequence.ts): its items, the time into it (s), whether it runs. `force`: every item
   *  out (1) for framing, else null. */
  private seq: { items: SeqItem[]; total: number; t: number; playing: boolean; of: Map<string, SeqItem>; force: number | null } | null = null;
  /** Playback speed (1 or 2), kept from step to step. */
  seqSpeed = 1;
  private readonly seqListeners = new Set<() => void>();
  private readonly guideLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({
    color: GUIDE_COLOR.add, dashSize: 0.0035, gapSize: 0.0025, transparent: true, depthTest: false, depthWrite: false,
  }));

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
    this.guideLines.name = 'guide_paths';
    this.guideLines.renderOrder = 40;
    this.guideLines.frustumCulled = false;
    this.guideLines.visible = false;
    this.guideLines.raycast = () => {};
    host.scene.add(this.guideLines);
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
    // ghosts (renderOrder 2) draw nearest first and write depth: only the nearest ghost surface shows,
    // so overlapping shells read as one faint layer instead of a haze; the rest back to front as usual
    this.host.renderer.setTransparentSort(on ? ghostSort : null);
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
      this.stopDemo();
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
    this.reportFinishes(m.root, parts);
    this.root.add(this.top.group);
    this.top.group.add(this.ifGroup);
    // No highlight knee here (look.ts tameHighlights): it squeezes every lit value above 0.7
    // into 0.85-1.0, which is what flattened the light shells. The Inspection rig keeps them
    // under the bloom threshold instead.
    // The head mech's frame sits where the droid's head is (its mount, mm in the body frame).
    this.focus = this.top; // (the root's position: applyVariantNodes, from the mount and the ground)
    this.pose(); // rest
    this.recordRest();
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
    // the overlaps of parts our build replaced (the kit's hero arm under Anderson's) are not ours
    const out = (id: string) => !!this.parts.get(this.pid(id))?.part.replaced_by;
    this.interference = this.interference.filter((it) => !out(it.a) && !out(it.b));
    // markers draw over whatever look is on (Checks shows them), each with an outline
    const fill = (c: number) => new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: 0.85, depthTest: false, depthWrite: false });
    const hot = fill(HIGHLIGHT.fail);
    const known = fill(HIGHLIGHT.warn);
    const line = (c: number) => new THREE.LineBasicMaterial({ color: c, depthTest: false, transparent: true });
    const hotLine = line(0x5c0b06);
    const knownLine = line(0x5a4706);
    const hull = (c: number) => new THREE.MeshBasicMaterial({ color: c, side: THREE.BackSide, depthTest: false, transparent: true });
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
      obj.traverse((o) => (o.renderOrder = 30));
      // placed by updateMarkers (it rides part a's link); its rest placement is its own transform
      obj.updateMatrix();
      obj.userData.rest = obj.matrix.clone();
      obj.matrixAutoUpdate = false;
      this.ifGroup.add(obj);
    }));
    this.updateMarkers();
  }

  /** Where each part's link frame was at the rest pose (relative to the droid's frame): the
   *  overlaps were computed there, so a marker follows its part by the link's move since. */
  private restLink = new Map<string, THREE.Matrix4>();
  private recordRest() {
    this.restLink.clear();
    const inv = this.top!.group.matrixWorld.clone().invert();
    for (const po of this.allParts) this.restLink.set(po.part.id, inv.clone().multiply(po.mesh.parent!.matrixWorld));
  }

  /** How part `id`'s link has moved since rest (droid frame), or null. */
  private linkMove(id: string, inv: THREE.Matrix4): THREE.Matrix4 | null {
    const po = this.parts.get(this.pid(id));
    const rest = po && this.restLink.get(po.part.id);
    if (!po || !rest) return null;
    return inv.clone().multiply(po.mesh.parent!.matrixWorld).multiply(rest.clone().invert());
  }

  /** Overlap pair `k` at the current pose: 'rest' (as computed), 'moved' with its parts (still valid),
   *  or 'apart' - the two parts moved relative to each other, so the rest-pose result does not apply. */
  pairState(k: number): 'rest' | 'moved' | 'apart' {
    const it = this.interference[k];
    if (!it || !this.top) return 'rest';
    const inv = this.top.group.matrixWorld.clone().invert();
    const a = this.linkMove(it.a, inv);
    const b = this.linkMove(it.b, inv);
    if (!a || !b) return 'rest';
    const same = (x: THREE.Matrix4, y: THREE.Matrix4) => x.elements.every((v, i) => Math.abs(v - y.elements[i]) < (i >= 12 ? 0.5 : 1e-3));
    if (!same(a, b)) return 'apart';
    return same(a, new THREE.Matrix4()) ? 'rest' : 'moved';
  }

  /** Markers ride part a's link; a pair whose parts moved apart (or an exploded view) shows none. */
  private updateMarkers() {
    if (!this.top || !this.ifGroup.children.length) return;
    this.top.group.updateMatrixWorld(true);
    const inv = this.top.group.matrixWorld.clone().invert();
    for (const o of this.ifGroup.children) {
      const k = o.userData.interference as number;
      const it = this.interference[k];
      const mv = it && this.linkMove(it.a, inv);
      const on = !!it && this.interferenceOn && this.explode < 0.01 && (this.pairSel === null ? this.pairInScope(it) : this.pairSel === k)
        && this.pairState(k) !== 'apart';
      o.visible = on;
      if (on && mv) o.matrix.copy(mv).multiply(o.userData.rest as THREE.Matrix4);
    }
    this.ifGroup.updateMatrixWorld(true);
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

  /** Checks tab open: the overlap markers draw (the scope's, or only the selected pair's). Looks never do. */
  setMarkers(on: boolean) {
    if (on === this.interferenceOn) return;
    this.interferenceOn = on;
    if (!on) this.pairSel = null;
    this.refresh();
    this.emit();
  }

  /** The overlap pair picked in Checks (-1/null: none). */
  pairSel: number | null = null;

  /** Frame one pair: its shared solid (or its point) and the two parts. */
  frameInterference(k: number) {
    const it = this.interference[k];
    if (!it) return;
    this.interferenceOn = true;
    this.pairSel = k;
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
    this.guide = null;
    this.guideLines.visible = false;
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
      const full = p.mesh_full ? joinUrl(base, p.mesh_full) + (sig ? `?v=${sig}` : `?r=${this.loadSeq}`) : undefined;
      parts.push({ part: p, node, holder, mesh, mat, base: b, lazy: lazy ? url : undefined, full });
    }));
    const fastMat = this.material(MATERIAL.fastener);
    await Promise.all((asm.fasteners ?? []).filter((f) => f.placed && f.mesh && f.transform).map(async (f) => {
      const furl = joinUrl(base, f.mesh!);
      const flazy = this.lazyHidden.has(asm);
      const geo = flazy ? new THREE.BufferGeometry() : await geometry(furl);
      const mat = withRim(fastMat.clone());
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

  /** Parts that should be painted but are not (SCHEMA.md "Finish"): drawn in MISSING_FINISH, listed in the
   * console and, when the manifest's own `finish` check does not already fail on them, in Checks. */
  private reportFinishes(root: MAssembly, parts: PartObj[]) {
    const bad = parts.map((po) => ({ p: po.part, why: finishProblem(po.part) })).filter((x) => x.why);
    if (!bad.length) return;
    console.warn(`[build] ${bad.length} parts without a finish (shown magenta):`, bad.map((x) => `${x.p.id}: ${x.why}`));
    const known = new Set(root.checks?.filter((c) => c.kind === 'finish' && c.status === 'fail').flatMap((c) => c.parts ?? []));
    const fresh = bad.filter((x) => !known.has(x.p.id));
    if (!fresh.length) return;
    (root.checks ??= []).push({
      id: 'finish_viewer', kind: 'finish', status: 'fail', title: 'Finishes: missing paint',
      summary: `${fresh.length} parts seen from outside have no usable paint; ${fresh.map((x) => `${x.p.id}: ${x.why}`).join('; ')}`,
      parts: fresh.map((x) => x.p.id),
    });
  }

  private material(f: Finish = MATERIAL.printed) {
    return withRim(new THREE.MeshStandardMaterial({ ...f, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
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

  /** Swap the full-detail meshes in for these parts (those that have one, are loaded and shown). */
  upgrade(ids: Iterable<string>) {
    const geometry = this.geometryFn;
    if (!geometry) return;
    const seq = this.loadSeq;
    for (const id of ids) {
      const po = this.parts.get(id);
      if (!po?.full || po.fullState || po.lazy || this.variantHidden.has(po.node)) continue;
      po.fullState = 'loading';
      void geometry(po.full).then((geo) => {
        if (seq !== this.loadSeq) return;
        po.fullState = 'done';
        po.mesh.getObjectByName('edges')?.removeFromParent();
        po.mesh.geometry = geo;
        this.addEdges(po.mesh, geo, po.part.class === 'shell');
        this.drainEdges(seq);
        this.markShadow();
        this.host.interact();
      }, () => (po.fullState = undefined));
    }
  }

  /** Parts big on screen get their full detail (checked a few times a second while the view moves). */
  private lastDetail = { at: 0, sig: '' };
  private detailByView(now: number) {
    if (now - this.lastDetail.at < 400) return;
    const cam = this.host.camera;
    const sig = cam.position.toArray().map((v) => v.toFixed(3)).join() + this.host.controls.target.toArray().map((v) => v.toFixed(3)).join();
    if (sig === this.lastDetail.sig) return;
    this.lastDetail = { at: now, sig };
    const tanV = Math.tan((cam.fov * Math.PI) / 360) / Math.max(0.2, cam.zoom);
    const want: string[] = [];
    const c = new THREE.Vector3();
    for (const [id, po] of this.parts) {
      if (!po.full || po.fullState || !po.mesh.visible) continue;
      const g = po.mesh.geometry;
      if (!g.boundingSphere) g.computeBoundingSphere();
      const bs = g.boundingSphere!;
      c.copy(bs.center).applyMatrix4(po.mesh.matrixWorld);
      const r = bs.radius * po.mesh.matrixWorld.getMaxScaleOnAxis();
      const d = Math.max(1e-3, c.distanceTo(cam.position));
      if (r / (d * tanV) > 0.6) want.push(id); // spans over 60 % of the view's height: close up
    }
    if (want.length) this.upgrade(want);
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
    this.root.position.set(mt[0] / 1000, (mt[1] - (g ?? 0)) / 1000 + (this.guide ? GUIDE_LIFT : 0), mt[2] / 1000);
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
    this.stopDemo();
    this.buildPlan();
    this.focus = sc?.owner ?? this.top;
    this.hover = null;
    this.isolated = null;
    this.step = -1;
    this.check = null;
    this.refresh();
    this.fitShadow(); // a library design may have moved the ground (its picks)
    if (sc) this.upgrade(sc.parts); // in focus: full detail
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
  get steps(): AStep[] {
    return this.assembly().list;
  }

  private asmCache: { sig: string; list: AStep[]; fast: Map<string, number> } | null = null;

  /**
   * Instructions: the build order of what is in focus (the whole droid without a focus). Each assembly's
   * own `steps` (SCHEMA.md "Step") in tree order, limited to the focus's parts; where an assembly has
   * none (or leaves parts out), steps derived from its structure - the fixed frame first, then each
   * moving group as it hangs on the one before, its fasteners with the step that places their last
   * part - marked `derived`. `fast` is each fastener's step.
   */
  assembly(sc: Pick<Scope, 'kind' | 'id' | 'parts'> | null = this.scope): { list: AStep[]; fast: Map<string, number> } {
    if (!this.top) return { list: [], fast: new Map() };
    const sig = `${this.loadSeq}|${JSON.stringify(this.variants)}|${sc ? `${sc.kind}:${sc.id}` : ''}`;
    if (this.asmCache?.sig === sig) return this.asmCache;
    const other = sc !== this.scope;
    const inScope = (id: string) => {
      const po = this.parts.get(id);
      return !!po && !this.variantHidden.has(po.node) && !po.part.replaced_by && (!sc || sc.parts.has(id));
    };
    const list: AStep[] = [];
    const fast = new Map<string, number>();
    const placed = new Set<string>();
    this.forEachNode((n) => {
      if (!this.nodeShown(n)) return;
      const mine = n.asm.parts.filter((p) => inScope(p.id));
      if (!mine.length) return;
      const nf = (n.asm.fasteners ?? []).filter((f) => f.placed && f.joins.some((p) => inScope(p)));
      for (const st of n.asm.steps ?? []) {
        const parts = (st.parts ?? []).filter((p) => inScope(p) && !placed.has(p));
        // (the manifest names a part once in `parts`, where it is placed; a later step that works on it has it in `context`)
        const targets = [...new Set([...(st.parts ?? []), ...(st.context ?? [])])].filter((p) => inScope(p) && placed.has(p));
        const fs = (st.fasteners ?? []).filter((f) => nf.some((x) => x.id === f) && !fast.has(f));
        // a step that only adds hardware, glue or a check to parts already in place still counts (the
        // kit's inserts and magnets), as long as it works on something in focus
        // (or, with the whole assembly in focus, hardware it names nowhere else: the kit's magnets)
        const whole = mine.length === n.asm.parts.filter((p) => !p.replaced_by).length;
        const work = (targets.length > 0 || whole) && (!!st.unplaced?.length || !!st.text || !!st.notes?.length || !!st.tools?.length);
        if (!parts.length && !fs.length && !work) continue;
        parts.forEach((p) => placed.add(p));
        fs.forEach((f) => fast.set(f, list.length));
        list.push({ ...st, parts, targets, fasteners: fs, context: (st.context ?? []).filter((p) => inScope(p)), node: n, assembly: assemblyLabel(n.asm.name) });
      }
      // what the steps leave out: the frame, then each moving group in the order it hangs
      const left = mine.filter((p) => !placed.has(p.id));
      if (left.length) {
        const order: string[] = [];
        const roots = n.asm.links.filter((l) => !n.asm.joints.some((j) => j.child_link === l.id && j.type !== 'fixed'));
        const visit = (id: string) => {
          if (order.includes(id)) return;
          order.push(id);
          for (const j of n.asm.joints) if (j.parent_link === id) visit(j.child_link);
        };
        roots.forEach((l) => visit(l.id));
        n.asm.links.forEach((l) => visit(l.id));
        for (const link of order) {
          const parts = left.filter((p) => p.link === link).map((p) => p.id);
          if (!parts.length) continue;
          const l = n.asm.links.find((x) => x.id === link);
          parts.forEach((p) => placed.add(p));
          list.push({
            id: `derived:${n.key}:${link}`, n: list.length + 1, title: assemblyLabel(l?.name ?? link), parts, fasteners: [],
            derived: true, node: n, assembly: assemblyLabel(n.asm.name),
          });
        }
      }
      // fasteners no step names: with the step that places the last part they join
      for (const f of nf) {
        if (fast.has(f.id)) continue;
        const at = Math.max(...f.joins.map((p) => list.findIndex((st) => st.parts?.includes(p))));
        if (at >= 0) {
          fast.set(f.id, at);
          (list[at].fasteners ??= []).push(f.id);
        }
      }
    });
    list.forEach((st, i) => (st.n = i + 1));
    if (other) return { list, fast };
    this.asmCache = { sig, list, fast };
    return this.asmCache;
  }

  // ------------------------------------------------------------------ pose

  setJoint(node: AsmNode, joint: string, value: number) {
    if (this.demo) this.stopDemo();
    const j = node.asm.joints.find((x) => x.id === joint);
    if (!j) return;
    node.pose[joint] = Math.min(j.limits.max + 40, Math.max(j.limits.min - 40, value));
    this.pose();
    this.emit();
  }

  home() {
    this.sweep = null;
    this.demo = null;
    this.labels(null);
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
      po.mesh.position.copy(po.sBase).add(this.offsetOf(po, this.explode));
    }
    this.root.updateMatrixWorld(true);
    this.updateMarkers();
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
    if (this.active && this.guide && this.isolated) this.frameIsolated();
    else if (this.active && !this.guide) this.frame(true);
  }

  /** The guide's inspector: frame the isolated parts and fasteners, keeping the view direction. */
  private frameIsolated() {
    const ids = this.isolated;
    if (!ids) return;
    const box = this.bounds(true, ids);
    for (const id of ids) {
      const fo = this.fast.get(id);
      if (!fo?.obj.visible) continue;
      if (!fo.obj.geometry.boundingBox) fo.obj.geometry.computeBoundingBox();
      box.union(fo.obj.geometry.boundingBox!.clone().applyMatrix4(fo.obj.matrixWorld));
    }
    if (box.isEmpty()) return;
    // a lone screw still reads at a sensible size
    const sz = box.getSize(new THREE.Vector3()).length();
    if (sz < 0.04) box.expandByScalar((0.04 - sz) / 2);
    this.frameBox(box, true, true);
  }

  /** The camera as it is (the guide puts Build's view back on Done). */
  cameraState() {
    return { pos: this.host.camera.position.clone(), target: this.host.controls.target.clone() };
  }

  setCameraState(s: { pos: THREE.Vector3; target: THREE.Vector3 }) {
    this.fly = null;
    this.host.camera.position.copy(s.pos);
    this.host.controls.target.copy(s.target);
    this.host.interact();
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
      this.buildPlan();
      this.refresh();
      this.emit();
    }
  }

  /** Checks tab: pose the model to show a check (null clears). */
  showCheck(c: MCheck | null, node = this.focus) {
    this.check = c;
    this.step = -1;
    this.contact = null;
    if (c && node) {
      node.pose = { ...(c.pose ?? {}) };
      this.pose();
    }
    this.refresh();
    this.emit();
  }

  // ------------------------------------------------------------------ instructions (guide.ts)

  /** A section's own parts: its assembly's, in the model under the current picks, not replaced. */
  sectionParts(node: AsmNode): Set<string> {
    const out = new Set<string>();
    if (!this.nodeShown(node)) return out;
    for (const p of node.asm.parts) {
      const po = this.parts.get(p.id);
      if (po && po.node === node && !po.part.replaced_by) out.add(p.id);
    }
    return out;
  }

  /** Open the guide on a section (null: back to Build). The section becomes the scope (its own parts),
   *  so `steps` are the section's, numbered from 1, and the explode plan its own. */
  setGuide(node: AsmNode | null) {
    this.stopDemo();
    this.sweep = null;
    this.seq = null;
    this.hover = null;
    this.check = null;
    this.contact = null;
    this.isolated = null;
    this.selected = null;
    this.guideBackdrop(!!node);
    if (!node) {
      const was = !!this.guide;
      this.guide = null;
      this.guideLines.visible = false;
      this.step = -1;
      if (was) {
        this.applyVariantNodes(); // back down on the floor
        this.pose();
        this.fitShadow();
      }
      this.refresh();
      this.emit();
      return;
    }
    const parts = this.sectionParts(node);
    this.guide = { node, parts, title: true, hover: null };
    this.applyVariantNodes(); // the root's height: lifted off the floor (GUIDE_LIFT)
    this.fitShadow();
    this.scope = { kind: 'assembly', id: `guide:${node.key}`, label: assemblyLabel(node.asm.name), parts, owner: node, joints: [], drive: new Set() };
    this.focus = node;
    this.buildPlan();
    this.step = -1;
    this.upgrade(parts);
    this.refresh();
    this.emit();
  }

  /** The guide's page: a plain light ground, the floor fading into it (the set's own put back after). */
  private savedBackdrop: { bg: THREE.Scene['background']; fog: THREE.Scene['fog'] } | null = null;
  private readonly guideBg = new THREE.Color(GUIDE_BG);
  private readonly guideFog = new THREE.Fog(GUIDE_BG, 1.6, 5);
  private guideBackdrop(on: boolean) {
    const sc = this.host.scene;
    if (on && !this.savedBackdrop) {
      this.savedBackdrop = { bg: sc.background, fog: sc.fog };
      sc.background = this.guideBg;
      sc.fog = this.guideFog;
    } else if (on && (sc.background !== this.guideBg || sc.fog !== this.guideFog)) {
      // the set re-lit itself meanwhile (a backdrop pick, the booth's bake): keep its newest, show ours
      this.savedBackdrop = { bg: sc.background, fog: sc.fog };
      sc.background = this.guideBg;
      sc.fog = this.guideFog;
      this.host.interact();
    } else if (!on && this.savedBackdrop) {
      sc.background = this.savedBackdrop.bg;
      sc.fog = this.savedBackdrop.fog;
      this.savedBackdrop = null;
    }
  }

  /** In the guide: the title card (-1) or step `i` of the section. */
  setGuideStep(i: number, animate = true) {
    const g = this.guide;
    if (!g) return;
    const steps = this.steps;
    this.step = Math.max(-1, Math.min(steps.length - 1, i));
    g.title = this.step < 0;
    this.selected = null;
    this.isolated = null;
    const s = this.step >= 0 ? steps[this.step] : null;
    const node = s?.node ?? this.focus;
    if (s?.pose && node) {
      node.pose = { ...node.pose, ...s.pose };
      this.pose();
    }
    this.guideArrivals(s);
    this.seq = null;
    if (s) {
      const { items, total } = this.planStep(s);
      const of = new Map<string, SeqItem>();
      for (const it of items) for (const id of it.ids) of.set(id, it);
      const still = !animate || reducedMotion();
      this.seq = { items, total, t: still ? total : 0, playing: !still, of, force: null };
      this.seqLast = performance.now();
    }
    this.refresh();
    this.emit();
    this.seqEmit();
  }

  /** The step's sequence from its parts and fasteners, positions in the section's frame. */
  private planStep(s: AStep) {
    this.root.updateMatrixWorld(true);
    const first = this.stepIndex();
    const parts: SeqPart[] = [];
    for (const id of s.parts ?? []) {
      const po = this.parts.get(id);
      if (!po) continue;
      parts.push({ id, group: partStem(id), at: (po.sBase ?? po.base).clone() });
    }
    const fasteners: SeqFastener[] = [];
    for (const fid of s.fasteners ?? []) {
      const fo = this.fast.get(fid);
      if (!fo) continue;
      const at = new THREE.Vector3().setFromMatrixPosition(fo.base);
      const axis = new THREE.Vector3(0, 0, 1).transformDirection(fo.base);
      fasteners.push({ id: fid, spec: fo.f.spec, joins: fo.f.joins, at, axis });
    }
    return planSequence(parts, fasteners, (id) => (first.get(id) ?? -1) < this.step);
  }

  // ---- the step's playback (guide.ts's transport)
  private seqLast = 0;
  onSeq(fn: () => void) {
    this.seqListeners.add(fn);
    return () => this.seqListeners.delete(fn);
  }
  private seqEmit() {
    for (const f of this.seqListeners) f();
  }
  get seqState() {
    const q = this.seq;
    return q ? { t: q.t, total: q.total, playing: q.playing, speed: this.seqSpeed, items: q.items.length } : null;
  }
  seqPlay(on: boolean) {
    const q = this.seq;
    if (!q) return;
    if (on && q.t >= q.total) q.t = 0;
    q.playing = on && !reducedMotion();
    if (!q.playing && on) q.t = q.total;
    this.seqLast = performance.now();
    this.applyOffsets(0);
    this.seqEmit();
    this.host.interact();
  }
  seqReplay() {
    if (!this.seq) return;
    this.seq.t = 0;
    this.seqPlay(true);
  }
  /** To a point in the step (0..1), paused. */
  seqScrub(frac: number) {
    const q = this.seq;
    if (!q) return;
    q.playing = false;
    q.t = Math.min(1, Math.max(0, frac)) * q.total;
    this.applyOffsets(0);
    this.seqEmit();
    this.host.interact();
  }
  /** Everything in: the step as it ends. */
  seqFinish() {
    const q = this.seq;
    if (!q) return;
    q.t = q.total;
    q.playing = false;
    this.applyOffsets(0);
    this.seqEmit();
  }
  setSeqSpeed(v: number) {
    this.seqSpeed = v;
    this.seqEmit();
  }
  /** How far out an id is (1 out, 0 seated) in the step playing; 0 for anything not in it. */
  private seqU(id: string): number {
    const q = this.seq;
    if (!q) return 0;
    const it = q.of.get(id);
    if (!it) return 0;
    return q.force ?? itemU(it, q.t);
  }

  /** Where each of a step's new parts comes from (link frame, mm): straight out from what is already built -
   *  from the nearest face of its bounds (a plate under the foot plate comes from below), or from its centre
   *  when the part sits inside it - as far as the part is big, 50-120 mm. */
  private guideFrom = new Map<string, THREE.Vector3>();
  private guideArrivals(st: AStep | null) {
    this.guideFrom.clear();
    const g = this.guide;
    if (!g || !st?.parts?.length) return;
    this.root.updateMatrixWorld(true);
    const first = this.stepIndex();
    const seatBox = (po: PartObj) => {
      const geo = po.mesh.geometry;
      if (!geo.boundingBox) geo.computeBoundingBox();
      if (geo.boundingBox!.isEmpty() || !po.mesh.parent) return null;
      return geo.boundingBox!.clone().translate(po.sBase ?? po.base).applyMatrix4(po.mesh.parent.matrixWorld);
    };
    const built = new THREE.Box3();
    for (const id of g.parts) {
      const f = first.get(id);
      const po = this.parts.get(id);
      if (po && f !== undefined && f < this.step) {
        const b = seatBox(po);
        if (b) built.union(b);
      }
    }
    const fresh = new THREE.Box3();
    const boxes = new Map<string, THREE.Box3>();
    for (const id of st.parts) {
      const po = this.parts.get(id);
      const b = po && seatBox(po);
      if (b) {
        boxes.set(id, b);
        fresh.union(b);
      }
    }
    const inv = new THREE.Matrix4();
    const unit = (v: THREE.Vector3) => (v.length() < 0.002 ? v.set(0, 0, 0) : v.normalize());
    // built: placed in an earlier step, or earlier in this one (a bearing goes into the U-joint put on just before)
    const order = st.parts;
    const before = (id: string, me?: string) => {
      const f = first.get(id);
      if (f !== undefined && f < this.step) return true;
      return !!me && order.includes(id) && order.indexOf(id) < order.indexOf(me);
    };
    // each built part's box: a way in that runs through fewer of them is the way in
    const builtBoxes: THREE.Box3[] = [];
    for (const id of g.parts) {
      const po = this.parts.get(id);
      if (po && before(id)) {
        const b = seatBox(po);
        if (b) builtBoxes.push(b);
      }
    }
    for (const [id, b] of boxes) {
      const po = this.parts.get(id)!;
      const c = b.getCenter(new THREE.Vector3());
      // away from what is built (its nearest face, else its centre), and apart from the step's other new parts
      const away = new THREE.Vector3();
      if (!built.isEmpty()) {
        away.copy(c).sub(built.clampPoint(c, new THREE.Vector3()));
        if (away.length() < 0.002) away.copy(c).sub(built.getCenter(new THREE.Vector3()));
      }
      const apart = boxes.size > 1 ? c.clone().sub(fresh.getCenter(new THREE.Vector3())) : new THREE.Vector3();
      let d = unit(away.clone()).multiplyScalar(0.7).add(unit(apart.clone()));
      // ... but along the way it goes on when the data says: its mate's axis with something already built (a body
      // sliding down a post, a bearing into its bore), else the screws that hold it to something built (a servo
      // drops into its pocket), signed to come from outside
      const ax = this.insertAxis(po, (x) => before(x, id));
      if (ax) {
        ax.transformDirection(po.mesh.parent!.matrixWorld);
        const out = away.lengthSq() > 1e-10 ? away : apart.lengthSq() > 1e-10 ? apart : new THREE.Vector3(0, 1, 0);
        // which end of the axis: the one whose way out runs through fewer built parts (the U-joint comes down
        // the free end of its post, not up through the hub), else away from what is built
        const len = Math.min(120, Math.max(50, b.getSize(new THREE.Vector3()).length() * 1000 * 0.7)) * GUIDE_PULL.part / 1000;
        const hits = (sg: number) => builtBoxes.filter((bb) => [0.35, 0.7, 1].some((f) => b.clone().translate(ax.clone().multiplyScalar(sg * len * f)).intersectsBox(bb))).length;
        const up = hits(1);
        const dn = hits(-1);
        d = ax.multiplyScalar(up !== dn ? (up < dn ? 1 : -1) : ax.dot(out) < 0 ? -1 : 1);
      }
      if (d.length() < 0.05) d.set(0, 1, 0);
      // into the part's link frame (a direction: no translation; the mm scale is undone by normalising)
      inv.copy(po.mesh.parent!.matrixWorld).invert();
      d.transformDirection(inv);
      const size = b.getSize(new THREE.Vector3()).length() * 1000; // mm
      this.guideFrom.set(id, d.multiplyScalar(Math.min(120, Math.max(50, size * 0.7))));
    }
  }

  /** The axis a part goes on along (its link frame), from a mate with a part already built, else from the
   *  fasteners that join it to one; null when the data says nothing. */
  private insertAxis(po: PartObj, built: (id: string) => boolean): THREE.Vector3 | null {
    const feats = (po.part as MPart & { features?: Record<string, { type?: string; d?: number[]; n?: number[] }> }).features ?? {};
    const mates = (po.node.asm as MAssembly & { mates?: { type: string; a: { part: string; feature: string }; b: { part: string; feature: string } }[] }).mates ?? [];
    const dirOf = (f?: { d?: number[]; n?: number[] }) => (f?.d ?? f?.n) ? new THREE.Vector3(...((f.d ?? f.n) as [number, number, number])).normalize() : null;
    const id = po.part.id;
    for (const m of mates) {
      const mine = m.a.part === id ? m.a : m.b.part === id ? m.b : null;
      const other = mine === m.a ? m.b : m.a;
      if (!mine || !built(other.part)) continue;
      const d = dirOf(feats[mine.feature]);
      if (d && feats[mine.feature]?.type !== 'point') return d;
    }
    for (const fo of this.fast.values()) {
      if (fo.node !== po.node || !fo.f.joins.includes(id) || !fo.f.joins.some((j) => j !== id && built(j))) continue;
      return new THREE.Vector3(0, 0, 1).transformDirection(fo.base);
    }
    return null;
  }

  /** Tray hover: mark these parts or fasteners (null clears). */
  setGuideHover(ids: Iterable<string> | null) {
    if (!this.guide) return;
    this.guide.hover = ids ? new Set(ids) : null;
    this.refresh();
    this.host.interact();
  }

  /** Frame the guide's page: the finished section from 3/4 front on its title card, else the step's new
   *  parts with what they go onto, keeping the view direction the builder left it at. */
  frameGuide(animate = true) {
    const g = this.guide;
    if (!g) return;
    // framed with everything the step adds still out (where it comes from) and its seat
    if (this.seq) this.seq.force = 1;
    this.applyOffsets(0);
    try {
      this.frameGuideNow(g, animate);
    } finally {
      if (this.seq) this.seq.force = null;
      this.applyOffsets(0);
    }
  }

  private frameGuideNow(g: GuideState, animate: boolean) {
    if (g.title || this.step < 0) {
      const box = this.bounds(true, g.parts);
      if (!box.isEmpty()) this.frameBox(box, false, animate);
      return;
    }
    const s = this.steps[this.step];
    // the new parts (where they sit pulled out) and the parts they go onto; the fasteners' holes
    const ids = new Set([...(s.parts ?? []), ...(s.targets ?? []), ...(s.context ?? [])]);
    const box = this.bounds(true, ids);
    for (const fid of s.fasteners ?? []) {
      const fo = this.fast.get(fid);
      if (fo?.obj.visible) box.expandByPoint(new THREE.Vector3().setFromMatrixPosition(fo.obj.matrixWorld));
    }
    // a small addition reads in place: at least a third of the section around it
    const all = this.bounds(true, g.parts);
    if (!all.isEmpty() && !box.isEmpty()) {
      const want = all.getSize(new THREE.Vector3()).length() / 3;
      const have = box.getSize(new THREE.Vector3()).length();
      if (have < want) box.expandByScalar((want - have) / 2);
    }
    if (!box.isEmpty()) this.frameBox(box, true, animate);
    else if (!all.isEmpty()) this.frameBox(all, true, animate);
  }

  /** The guide's dashed insertion paths: each new part's centre and each new fastener back to its seat. */
  private updateGuidePaths() {
    const g = this.guide;
    const cur = g && !g.title && this.step >= 0 ? this.steps[this.step] : null;
    if (!cur || this.isolated) {
      this.guideLines.visible = false;
      return;
    }
    const pts: number[] = [];
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const push = () => {
      if (a.distanceToSquared(b) > 1e-8) pts.push(a.x, a.y, a.z, b.x, b.y, b.z);
    };
    const hv = g?.hover ?? null;
    const shown = (id: string) => !hv || hv.has(id);
    for (const id of cur.parts ?? []) {
      const po = this.parts.get(id);
      if (!po?.mesh.visible || !po.mesh.parent || !shown(id)) continue;
      const geo = po.mesh.geometry;
      if (!geo.boundingBox) geo.computeBoundingBox();
      if (geo.boundingBox!.isEmpty()) continue;
      const c = geo.boundingBox!.getCenter(new THREE.Vector3());
      const pm = po.mesh.parent.matrixWorld;
      a.copy(c).add(po.mesh.position).applyMatrix4(pm);
      b.copy(c).add(po.sBase ?? po.base).applyMatrix4(pm);
      push();
    }
    for (const fid of cur.fasteners ?? []) {
      const fo = this.fast.get(fid);
      if (!fo?.obj.visible || !fo.obj.parent || !shown(fid)) continue;
      a.setFromMatrixPosition(fo.obj.matrixWorld);
      b.setFromMatrixPosition(fo.base).applyMatrix4(fo.obj.parent.matrixWorld);
      push();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    this.guideLines.geometry.dispose();
    this.guideLines.geometry = geo;
    this.guideLines.computeLineDistances();
    (this.guideLines.material as THREE.LineDashedMaterial).opacity = 0.95;
    this.guideLines.visible = pts.length > 0;
  }

  /** The guide's model area as a share of the canvas, and the view offset that centres it (px). */
  private guideArea() {
    const el = this.host.renderer.domElement;
    const w = el.clientWidth || innerWidth;
    const h = el.clientHeight || innerHeight;
    const r = this.guideRect;
    const aw = Math.max(120, w - r.right);
    const ah = Math.max(120, h - r.bottom - r.top);
    return { w, h, sx: aw / w, sy: ah / h, ox: r.right / 2, oy: (r.bottom - r.top) / 2 };
  }

  /** Keep the camera's view offset on the guide's model area (main.ts fits it between the panels). */
  private holdGuideView() {
    const cam = this.host.camera;
    const { w, h, ox, oy } = this.guideArea();
    const v = cam.view;
    if (v && v.enabled && v.fullWidth === w && v.fullHeight === h && Math.abs(v.offsetX - ox) < 0.5 && Math.abs(v.offsetY - oy) < 0.5 && cam.zoom === 1) return;
    cam.aspect = w / h;
    cam.setViewOffset(w, h, ox, oy, w, h);
    cam.zoom = 1;
    cam.updateProjectionMatrix();
    this.host.interact();
  }

  /** A small picture of one part or fastener, as the current look draws it, from the 3/4 front (data URL). */
  private thumbs = new Map<string, string>();
  private thumbKit: { scene: THREE.Scene; cam: THREE.PerspectiveCamera; rt: THREE.WebGLRenderTarget; mat: THREE.MeshStandardMaterial; mesh: THREE.Mesh } | null = null;
  thumbnail(id: string, px = 96): string | null {
    const po = this.parts.get(id);
    const fo = po ? null : this.fast.get(id);
    const obj = po?.mesh ?? fo?.obj;
    if (!obj) return null;
    const geo = obj.geometry;
    if (!geo.attributes.position?.count) return null;
    const finish = po ? (this.look === 'exterior' ? exteriorFinish(po.part) : mechanismFinish(po.part)) : MATERIAL.fastener;
    const key = `${id}|${geo.uuid}|${finish.color}|${px}`;
    const hit = this.thumbs.get(key);
    if (hit) return hit;
    const r = this.host.renderer;
    const S = px * 2; // drawn at twice the size, then scaled down: smooth edges without MSAA
    if (!this.thumbKit) {
      const scene = new THREE.Scene();
      scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a5a, 1.6));
      const key2 = new THREE.DirectionalLight(0xffffff, 2.2);
      key2.position.set(0.9, 1.7, 1.3);
      const fill = new THREE.DirectionalLight(0xffffff, 0.7);
      fill.position.set(-1.4, 0.4, 0.8);
      scene.add(key2, fill);
      const mat = new THREE.MeshStandardMaterial({ side: THREE.DoubleSide });
      const mesh = new THREE.Mesh(new THREE.BufferGeometry(), mat);
      scene.add(mesh);
      const rt = new THREE.WebGLRenderTarget(S, S);
      rt.texture.colorSpace = THREE.SRGBColorSpace;
      this.thumbKit = { scene, cam: new THREE.PerspectiveCamera(24, 1, 0.1, 1e5), rt, mat, mesh };
    }
    const k = this.thumbKit;
    if (k.rt.width !== S) k.rt.setSize(S, S);
    k.mat.color.setHex(finish.color);
    k.mat.metalness = Math.min(0.5, finish.metalness);
    k.mat.roughness = Math.max(0.45, finish.roughness);
    k.mesh.geometry = geo;
    // as it sits in the model (its link's turn), centred
    const q = new THREE.Quaternion();
    obj.updateWorldMatrix(true, false);
    obj.matrixWorld.decompose(new THREE.Vector3(), q, new THREE.Vector3());
    k.mesh.quaternion.copy(q);
    k.mesh.position.set(0, 0, 0);
    k.mesh.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(k.mesh);
    const c = box.getCenter(new THREE.Vector3());
    k.mesh.position.sub(c);
    k.mesh.updateMatrixWorld(true);
    const rad = Math.max(1e-3, box.getSize(new THREE.Vector3()).length() / 2);
    const dist = rad / Math.sin((k.cam.fov * Math.PI) / 360) * 1.02;
    k.cam.position.copy(new THREE.Vector3(0.62, 0.38, 1).normalize().multiplyScalar(dist));
    k.cam.near = dist / 50;
    k.cam.far = dist * 4;
    k.cam.lookAt(0, 0, 0);
    k.cam.updateProjectionMatrix();
    const prevRT = r.getRenderTarget();
    const prevClear = r.getClearColor(new THREE.Color());
    const prevAlpha = r.getClearAlpha();
    const prevClip = r.localClippingEnabled;
    r.localClippingEnabled = false;
    r.setRenderTarget(k.rt);
    r.setClearColor(0x000000, 0);
    r.clear();
    r.render(k.scene, k.cam);
    const buf = new Uint8Array(S * S * 4);
    r.readRenderTargetPixels(k.rt, 0, 0, S, S, buf);
    r.setRenderTarget(prevRT);
    r.setClearColor(prevClear, prevAlpha);
    r.localClippingEnabled = prevClip;
    // flip (GL rows run bottom up) and scale down
    const big = document.createElement('canvas');
    big.width = big.height = S;
    const img = new ImageData(S, S);
    for (let y = 0; y < S; y++) img.data.set(buf.subarray((S - 1 - y) * S * 4, (S - y) * S * 4), y * S * 4);
    big.getContext('2d')!.putImageData(img, 0, 0);
    const out = document.createElement('canvas');
    out.width = out.height = px;
    const cx = out.getContext('2d')!;
    cx.imageSmoothingQuality = 'high';
    cx.drawImage(big, 0, 0, px, px);
    const url = out.toDataURL('image/png');
    this.thumbs.set(key, url);
    return url;
  }

  // ------------------------------------------------------------------ how it works

  /** A motion system running its joints through their range, one after another, on a loop. */
  private demo: { id: string; t0: number; segs: { node: AsmNode; joint: string; from: number; dur: number; min: number; max: number }[]; total: number } | null = null;

  get demoing() {
    return this.demo?.id ?? null;
  }

  /** "How it works": focus the system and loop its joints slowly (up, down, home; then the next),
   *  keeping the explode. Any joint move, Home or a new focus stops it. */
  startDemo(id: string) {
    if (this.scope?.kind !== 'system' || this.scope.id !== id) {
      const sc = this.systemScope(id);
      if (!sc) return;
      this.setScope(sc);
    }
    const sc = this.scope!;
    let t = 0;
    const segs = sc.joints.map(({ node, joint: j }) => {
      const span = j.limits.max - j.limits.min;
      const dur = 2.4 + Math.min(3.6, span / (j.unit === 'mm' ? 25 : 30)); // slow enough to follow the gears
      const seg = { node, joint: j.id, from: t, dur, min: j.limits.min, max: j.limits.max };
      t += dur + 0.6;
      return seg;
    });
    if (!segs.length) return;
    this.sweep = null;
    this.forEachNode((n) => { for (const s2 of segs) if (s2.node === n) n.pose[s2.joint] = 0; });
    this.demo = { id, t0: performance.now(), segs, total: t };
    this.emit();
  }

  stopDemo() {
    if (!this.demo) return;
    this.demo = null;
    this.labels(null);
    this.emit();
  }

  private stepDemo(now: number) {
    const d = this.demo!;
    const t = ((now - d.t0) / 1000) % d.total;
    for (const sg of d.segs) {
      const u = (t - sg.from) / sg.dur;
      let v = 0;
      if (u > 0 && u < 1) {
        // 0 -> max -> min -> 0, eased
        const w = u * 4;
        const e = (x: number) => (1 - Math.cos(Math.PI * Math.min(1, Math.max(0, x)))) / 2;
        v = w < 1 ? sg.max * e(w) : w < 3 ? sg.max + (sg.min - sg.max) * e((w - 1) / 2) : sg.min * (1 - e(w - 3));
      }
      sg.node.pose[sg.joint] = v;
    }
    this.pose();
    this.labels(this.demoLabels());
  }

  /** A few terse labels on the parts that make the motion: the servos, gears and what they mesh with,
   *  the wheels, a bearing (at most six). */
  private demoLabels(): { id: string; text: string }[] {
    const sc = this.scope;
    if (!sc) return [];
    const out: { id: string; text: string }[] = [];
    const add = (id: string | undefined, text: string) => {
      if (!id || out.length >= 6 || out.some((x) => x.id === id || x.text === text) || !this.parts.get(id)?.mesh.visible) return;
      out.push({ id, text });
    };
    for (const { node, joint } of sc.joints) {
      const word = jointLabel(joint.name).toLowerCase().split(/\s+/).filter((w) => !['head', 'arm', 'ring'].includes(w)).pop() ?? 'drive';
      add(joint.drive?.servos?.[0], `${word} servo`);
      this.forEachNode((n) => {
        for (const g of n.asm.gears ?? []) {
          if (g.joint !== joint.id || (g.joint_assembly ?? n.asm.id) !== node.asm.id) continue;
          add(g.parts[0], g.kind === 'direct' ? 'coupler' : 'pinion');
          if (g.mesh_with) add(g.mesh_with, g.kind === 'rack_pinion' ? 'rack' : g.kind === 'internal' ? 'sector' : 'gear');
        }
      });
    }
    const wheel = [...sc.parts].find((id) => /v-wheel/i.test(this.parts.get(id)?.part.name ?? ''));
    add(wheel, 'V-wheels');
    const brg = [...sc.parts].find((id) => this.parts.get(id)?.part.class === 'bearing' && !/wheel/i.test(this.parts.get(id)!.part.name));
    add(brg, 'bearing');
    return out;
  }

  /** The label layer over the viewport (null: none). */
  private labelEl: HTMLDivElement | null = null;
  private labels(list: { id: string; text: string }[] | null) {
    if (!list?.length) {
      this.labelEl?.replaceChildren();
      return;
    }
    if (!this.labelEl) {
      this.labelEl = document.createElement('div');
      this.labelEl.className = 'bb-labels';
      this.labelEl.setAttribute('aria-hidden', 'true');
      document.body.append(this.labelEl);
    }
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const cam = this.host.camera;
    const html: string[] = [];
    for (const { id, text } of list) {
      const po = this.parts.get(id)!;
      const g = po.mesh.geometry;
      if (!g.boundingBox) g.computeBoundingBox();
      const p = g.boundingBox!.getCenter(new THREE.Vector3()).applyMatrix4(po.mesh.matrixWorld).project(cam);
      if (p.z > 1) continue;
      html.push(`<span style="left:${(r.left + ((p.x + 1) / 2) * r.width).toFixed(0)}px;top:${(r.top + ((1 - p.y) / 2) * r.height).toFixed(0)}px">${text}</span>`);
    }
    this.labelEl.innerHTML = html.join('');
  }

  /** Joints outside the focus that are off rest and move it (a turned ring under the hero arm): the
   *  focus looks wrong until they are home. */
  upstream(): { node: AsmNode; joint: MJoint; value: number }[] {
    const sc = this.scope;
    if (!sc || !this.top) return [];
    const mine = new Set(sc.joints.map((x) => x.joint));
    const out: { node: AsmNode; joint: MJoint; value: number }[] = [];
    this.forEachNode((n) => {
      if (!this.nodeShown(n)) return;
      for (const j of n.asm.joints) {
        const v = n.pose[j.id] ?? 0;
        if (mine.has(j) || Math.abs(v) < 0.05) continue;
        const moved = movedBy(n, j.id, (x) => this.nodeShown(x), this.top);
        if ([...sc.parts].some((id) => moved.has(id))) out.push({ node: n, joint: j, value: v });
      }
    });
    return out;
  }

  /** One joint back to rest. */
  rest(node: AsmNode, joint: string) {
    node.pose[joint] = 0;
    this.pose();
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
    if (this.demo) {
      this.stepDemo(now);
      for (const f of this.listeners) f();
      moving = true;
    }
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
    this.detailByView(now);
    if (this.guide) {
      this.holdGuideView();
      this.guideBackdrop(true);
      const q = this.seq;
      if (q?.playing) {
        q.t = Math.min(q.total, q.t + ((now - this.seqLast) / 1000) * this.seqSpeed);
        if (q.t >= q.total) q.playing = false;
        this.applyOffsets(0);
        this.seqEmit();
        moving = true;
      }
      this.seqLast = now;
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
    // overlay marks (rims): the selected pair's two parts while Checks shows it
    const pairParts = new Set<string>();
    const sel = this.pairSel !== null && this.interferenceOn ? this.interference[this.pairSel] : null;
    if (sel) for (const id of [sel.a, sel.b]) pairParts.add(this.pid(id));
    const hover = this.hover;
    const g = this.guide;
    const insideShells = !!g && !!cur && (cur.parts ?? []).length > 0 && (cur.parts ?? []).every((id) => this.parts.get(id)?.part.class !== 'shell');

    // 1. The look: one finish and opacity per part from the look, the focus and the mode (steps)
    //    alone. 2. Overlays: a rim on top (withRim), never a change of colour, opacity or side.
    for (const [id, po] of this.parts) {
      const p = po.part;
      const shell = p.class === 'shell';
      let visible = !hideV.has(id) && !this.hidden.has(id) && (!this.isolated || this.isolated.has(id)) && !this.variantHidden.has(po.node);
      const inScope = !scope || scope.has(id);
      // a part our build replaces (the kit's hero elbow under Anderson's arm) is out of the build; a library
      // design (the kit as published) still shows it
      if (p.replaced_by && !(inScope && this.scope?.kind === 'library')) visible = false;
      // Exterior: what is seen from outside, painted. Mechanism: what is inside (a focus keeps its own
      // shells as a ghost). X-ray: the mechanism under ghosted shells.
      const outside = exposed(p);
      if (look === 'exterior' && !outside) visible = false;
      if (look === 'mechanism' && shell && !scope) visible = false;
      if (!inScope && !ghosts) visible = false;
      // a step: what is still to come is hidden; the step's context lightly ghosted
      const f = first.get(id);
      const toCome = !!cur && f !== undefined && f > this.step;
      if (toCome && !ctx.has(id)) visible = false;
      // Instructions: the section alone - all of it on its title card, else what is placed so far (and the
      // step's context), whatever the look (a look changes how parts are drawn, not which)
      if (g) {
        visible = g.parts.has(id) && !hideV.has(id) && !this.variantHidden.has(po.node) && (!this.isolated || this.isolated.has(id))
          && (g.title || !cur || (f !== undefined && f <= this.step) || ctx.has(id));
      }
      po.holder.visible = visible;
      po.mesh.visible = visible;
      const m = po.mat;
      // ghosts are one neutral grey, whatever the part's paint (no pink X-ray, no purple cups)
      let finish: Finish;
      let opacity = 1;
      if (!inScope) {
        finish = GHOST_FINISH;
        opacity = shell ? GHOST.shell : GHOST.mech;
      } else if (look === 'exterior') {
        finish = exteriorFinish(p); // its paint; bare metal its material; a missing paint, MISSING_FINISH
      } else if (shell && !(g && (look === 'mechanism' || (!g.title && inStep.has(id)) || this.isolated?.has(id)))) {
        // (the guide's X-ray ghosts the shells around what the step adds, never the new part itself)
        finish = GHOST_FINISH;
        opacity = look === 'inspect' ? GHOST.inspectShell : GHOST.shell;
      } else {
        finish = mechanismFinish(p);
      }
      if (toCome) {
        finish = GHOST_FINISH;
        opacity = Math.min(opacity, ctx.has(id) ? 0.22 : 0.06);
      }
      // the guide on a step that works inside the shells (a mechanism under the kit's head): the shells already on
      // fade back, as X-ray would, so what goes in stays in sight
      if (g && cur && !g.title && shell && !inStep.has(id) && insideShells && !this.isolated) {
        finish = GHOST_FINISH;
        opacity = Math.min(opacity, GHOST.inspectShell);
      }
      // the guide's tray under the pointer: what it names stays, the rest of the section fades back
      if (g?.hover && !g.hover.has(id)) {
        finish = GHOST_FINISH;
        opacity = Math.min(opacity, 0.12);
      }
      m.color.setHex(finish.color);
      // Instructions: what the step adds, in the accent at the part's own lightness (a dark servo a deep blue, bare
      // aluminium a pale one) - never mixed with its paint, which turns the orange shells purple. A part being
      // inspected shows as it is, not as new.
      const adds = !!g && !g.title && !!cur && inStep.has(id) && !(g.hover && !g.hover.has(id)) && !this.isolated;
      if (adds) {
        const own = m.color.getHSL({ h: 0, s: 0, l: 0 }).l;
        const acc = new THREE.Color(GUIDE_COLOR.add).getHSL({ h: 0, s: 0, l: 0 });
        m.color.setHSL(acc.h, acc.s * 0.9, Math.min(0.62, Math.max(0.3, own)));
      }
      m.metalness = finish.metalness;
      m.roughness = finish.roughness;
      m.emissive.setHex(0x000000);
      setLook(m, opacity, clip);
      po.mesh.renderOrder = opacity < 1 ? 2 : 0;
      po.mesh.castShadow = visible && opacity >= 0.99;
      const edges = po.mesh.getObjectByName('edges');
      if (edges) edges.visible = opacity >= 0.99;
      // overlays, strongest first; a ghost (the look made it faint) is not lit up
      let mark: [number, number] | null = null;
      if (g) {
        // inspecting (isolated): the part alone, as it is; hovering the tray: what it names, outlined
        if (g.hover?.has(id) || (this.selected === id && !this.isolated)) mark = [GUIDE_COLOR.focus, 0.8];
        else if (adds) mark = [GUIDE_COLOR.add, 0.45];
      } else if (this.selected === id) mark = [HIGHLIGHT.selected, 0.9];
      else if (this.check && checkParts.has(id)) mark = [this.check.status === 'fail' ? HIGHLIGHT.fail : this.check.status === 'warn' ? HIGHLIGHT.warn : HIGHLIGHT.step, 0.8];
      else if (contact.has(id)) mark = [this.contact?.status === 'fail' ? HIGHLIGHT.fail : HIGHLIGHT.warn, 0.8];
      else if (pairParts.has(id)) mark = [sel!.explained ? HIGHLIGHT.warn : HIGHLIGHT.fail, 0.7];
      else if (cur && inStep.has(id)) mark = [HIGHLIGHT.step, 0.7];
      else if (hover?.moved.has(id)) mark = [HIGHLIGHT.selected, 0.5];
      else if (hover?.drive.has(id)) mark = [HIGHLIGHT.selected, 0.25];
      rim(m, mark?.[0] ?? 0, mark && opacity >= 0.3 ? mark[1] : 0);
    }
    for (const em of [this.edgeMat, this.shellEdgeMat]) {
      if ((clip?.length ?? 0) !== (em.clippingPlanes?.length ?? 0)) em.needsUpdate = true;
      em.clippingPlanes = clip;
    }
    const fastIn = new Set(cur?.fasteners ?? []);
    const fastAt = this.assembly().fast;
    for (const [id, fo] of this.fast) {
      const joinsVisible = fo.f.joins.some((p) => this.parts.get(p)?.mesh.visible && (!scope || scope.has(p)));
      let visible = this.fasteners && look !== 'exterior' && joinsVisible && !this.hidden.has(id);
      // Instructions: the hardware is part of the build in any look (the guide shows every screw)
      if (g) visible = (joinsVisible || !!this.isolated?.has(id)) && !this.hidden.has(id) && (!g.hover || g.hover.has(id))
        && (!this.isolated || this.isolated.has(id)); // inspecting: only what is inspected
      if (cur && (fastAt.get(id) ?? -1) > this.step) visible = false;
      if (this.isolated && !fo.f.joins.some((p) => this.isolated!.has(p)) && !this.isolated.has(id)) visible = false;
      fo.obj.visible = visible;
      const addsF = !!g && !g.title && !!cur && fastIn.has(id) && !this.isolated;
      fo.mat.color.setHex(addsF ? GUIDE_COLOR.add : MATERIAL.fastener.color);
      fo.mat.emissive.setHex(0);
      setLook(fo.mat, 1, clip);
      if (g) rim(fo.mat, GUIDE_COLOR.focus, g.hover?.has(id) || (this.selected === id && !this.isolated) ? 0.8 : 0);
      else rim(fo.mat, this.selected === id ? HIGHLIGHT.selected : HIGHLIGHT.step, this.selected === id ? 0.9 : cur && fastIn.has(id) ? 0.7 : 0);
      fo.obj.castShadow = visible;
    }
    this.updateMarkers();
    this.applyOffsets(0);
  }



  // ------------------------------------------------------------------ explode

  /** A focus's explode (hierarchical): part id -> offset at full explode, mm in the droid's frame. */
  private plan: Map<string, THREE.Vector3> | null = null;

  /**
   * The explode of what is in focus, as it is assembled: links that move (a joint's child) pull out of
   * the link they ride - a slide sideways out of its rails (perpendicular to the slide), a coaxial turn
   * along its axis, anything else away from its parent - carrying everything downstream with them;
   * then each link's parts spread from that link's centre along their own direction (the plates of a
   * box to their faces, wheels to their corners). The fixed frame the focus hangs from stays. Parts
   * of the focus that live elsewhere (a ring's servo on the column) back away from the focus's centre.
   * Without a focus, each part's manifest `explode` (a direction and a distance) applies.
   */
  private buildPlan() {
    this.plan = null;
    const sc = this.scope;
    if (!this.top) return;
    // nothing in focus: the whole droid, level by level (assemblies apart, then their moving
    // groups, then the pieces), the inner levels at half the distance so the structure reads
    const whole = !sc;
    const K = whole ? 0.5 : 1;
    const centre = (po: PartObj) => {
      const g = po.mesh.geometry;
      if (!g.boundingBox) g.computeBoundingBox();
      const c = g.boundingBox!.isEmpty() ? new THREE.Vector3() : g.boundingBox!.getCenter(new THREE.Vector3());
      return c.add(po.sBase ?? po.base).applyMatrix4(this.restLink.get(po.part.id) ?? new THREE.Matrix4());
    };
    const ids = sc ? [...sc.parts] : [...this.parts.keys()];
    const mine = ids.map((id) => this.parts.get(id)).filter((po): po is PartObj => !!po && !this.variantHidden.has(po.node) && !po.part.replaced_by);
    if (!mine.length) return;
    const all = new THREE.Box3();
    const cen = new Map<PartObj, THREE.Vector3>();
    for (const po of mine) {
      const c = centre(po);
      cen.set(po, c);
      all.expandByPoint(c);
    }
    const scopeC = all.getCenter(new THREE.Vector3());
    const plan = new Map<string, THREE.Vector3>();
    const nodes = new Set(sc ? sc.joints.map((x) => x.node) : []);
    if (!nodes.size) for (const po of mine) nodes.add(po.node);
    const half = (pts: THREE.Vector3[], c: THREE.Vector3, d: THREE.Vector3) => pts.reduce((m, p) => Math.max(m, Math.abs(p.clone().sub(c).dot(d))), 0);
    for (const n of nodes) {
      const byLink = new Map<string, PartObj[]>();
      for (const po of mine) if (po.node === n) (byLink.get(po.part.link) ?? byLink.set(po.part.link, []).get(po.part.link)!).push(po);
      const centroid = (ps: PartObj[]) => ps.reduce((v, po) => v.add(cen.get(po)!), new THREE.Vector3()).divideScalar(Math.max(1, ps.length));
      const linkOff = new Map<string, THREE.Vector3>();
      const offOf = (link: string): THREE.Vector3 => {
        const have = linkOff.get(link);
        if (have) return have;
        const j = n.asm.joints.find((x) => x.child_link === link);
        const kids = byLink.get(link) ?? [];
        if (!j || !kids.length) {
          const z = j ? offOf(j.parent_link).clone() : new THREE.Vector3();
          linkOff.set(link, z);
          return z;
        }
        const parent = byLink.get(j.parent_link) ?? [];
        const pc = parent.length ? centroid(parent) : scopeC;
        const lc = centroid(kids);
        const axis = new THREE.Vector3(...j.axis).normalize().transformDirection(this.restLink.get(kids[0].part.id) ?? new THREE.Matrix4());
        const v = lc.clone().sub(pc);
        let dir: THREE.Vector3;
        if (j.type === 'prismatic') {
          // out of its rails: across the slide, toward the open side (else the front)
          dir = v.clone().addScaledVector(axis, -v.dot(axis));
          if (dir.length() < 8) dir.set(0, 0, 1).addScaledVector(axis, -axis.z);
        } else {
          // a turn pulls along its axis when coaxial with its frame (a ring, the pan) - and always for the
          // whole droid, where a sideways pull reads as parts flying off
          const radial = v.clone().addScaledVector(axis, -v.dot(axis));
          dir = radial.length() < 10 || whole ? axis.clone().multiplyScalar(v.dot(axis) < 0 ? -1 : 1) : v.clone();
        }
        dir.normalize();
        const kidPts = kids.map((po) => cen.get(po)!);
        const parPts = parent.map((po) => cen.get(po)!);
        const dist = K * Math.min(140, half(parPts, pc, dir) * 0.6 + half(kidPts, lc, dir) + 30);
        const off = offOf(j.parent_link).clone().addScaledVector(dir, dist);
        linkOff.set(link, off);
        return off;
      };
      for (const [link, ps] of byLink) {
        const moving = n.asm.joints.some((x) => x.child_link === link);
        const lc = centroid(ps);
        for (const po of ps) {
          const off = offOf(link).clone();
          if (moving && ps.length > 1) {
            const v = cen.get(po)!.clone().sub(lc);
            const d = v.length();
            if (d > 2) off.addScaledVector(v.normalize(), K * Math.min(60, 0.7 * d + 12));
          }
          plan.set(po.part.id, off);
        }
      }
    }
    // the whole droid: each assembly pulls away from the one it is mounted on, carrying its children
    if (whole) {
      const byNode = new Map<AsmNode, PartObj[]>();
      for (const po of mine) (byNode.get(po.node) ?? byNode.set(po.node, []).get(po.node)!).push(po);
      const mean = (pts: THREE.Vector3[]) => pts.reduce((v, p) => v.add(p), new THREE.Vector3()).divideScalar(Math.max(1, pts.length));
      // The droid is a stack around its column: each assembly (base skirt, column, rings, head) lifts
      // above the one under it, in the order they sit (by their lowest part), with a gap - the column
      // stays, the shells and rings rise off it, the head rides on top. Arms ride their rings.
      const order = [...byNode.keys()].map((n) => ({ n, lo: Math.min(...byNode.get(n)!.map((po) => cen.get(po)!.y)) }))
        .sort((x, y) => x.lo - y.lo);
      const nodeOff = new Map<AsmNode, THREE.Vector3>();
      let rise = 0;
      for (const { n } of order) {
        const own = byNode.get(n)!.map((po) => cen.get(po)!);
        nodeOff.set(n, new THREE.Vector3(0, rise, 0));
        rise += Math.min(110, half(own, mean(own), new THREE.Vector3(0, 1, 0)) * 0.25 + 45);
      }
      const offN = (n: AsmNode) => nodeOff.get(n) ?? new THREE.Vector3();
      for (const po of mine) plan.set(po.part.id, (plan.get(po.part.id) ?? new THREE.Vector3()).add(offN(po.node)));
    }
    // the focus's parts that live in other assemblies (a drive servo on the column) back away from it
    for (const po of mine) {
      if (plan.has(po.part.id)) continue;
      const v = cen.get(po)!.clone().sub(scopeC);
      const d = v.length();
      plan.set(po.part.id, d > 2 ? v.normalize().multiplyScalar(Math.min(60, 0.4 * d + 20)) : new THREE.Vector3());
    }
    // in each part's link frame (mesh.position's), once
    for (const [id, o] of plan) {
      const rest = this.restLink.get(id);
      if (rest) o.applyMatrix3(new THREE.Matrix3().setFromMatrix4(rest).invert());
    }
    this.plan = plan;
  }

  /** Where a part arrives from in a step (link frame): its explode direction, 50-120 mm out. */
  private arrival(po: PartObj): THREE.Vector3 {
    const o = this.plan?.get(po.part.id)?.clone() ?? new THREE.Vector3(...(po.part.explode ?? [0, 1, 0]));
    if (o.lengthSq() < 1e-6) o.set(0, 1, 0);
    const len = Math.min(120, Math.max(50, o.length() * 1.5));
    return o.normalize().multiplyScalar(len);
  }

  /** A part's explode offset at `k`, in its link's frame. */
  private offsetOf(po: PartObj, k: number): THREE.Vector3 {
    if (!(k > 0)) return new THREE.Vector3();
    if (this.plan) {
      const o = this.plan.get(po.part.id);
      return o ? o.clone().multiplyScalar(k) : new THREE.Vector3();
    }
    return new THREE.Vector3(...(po.part.explode ?? [0, 0, 0])).multiplyScalar((po.part.explode_mm ?? 0) * k);
  }

  /** Explode offsets, plus the insertion animation of the current step (`insert` 1 -> 0). Fasteners ride
   *  the part they hold and back out along their own axis, out of their holes. */
  private applyOffsets(insert: number) {
    const cur = this.step >= 0 ? this.steps[this.step] : null;
    const inStep = new Set(cur?.parts ?? []);
    const fastIn = new Set(cur?.fasteners ?? []);
    // Instructions: the step plays (sequence.ts) - each new part comes in along its way from where it starts out
    const g = !!this.guide && !this.guide.title && !!cur;
    for (const po of this.parts.values()) {
      const u = g && inStep.has(po.part.id) ? this.seqU(po.part.id) : 0;
      // a step's new parts come in from where the explode would take them (at least 50 mm out)
      const step = inStep.has(po.part.id) && (u > 0 || (!g && insert > 0))
        ? (g ? (this.guideFrom.get(po.part.id)?.clone() ?? this.arrival(po)).multiplyScalar(GUIDE_PULL.part * u) : this.arrival(po).multiplyScalar(insert)) : null;
      po.mesh.position.copy(po.sBase ?? po.base).add(this.offsetOf(po, this.explode));
      if (step) po.mesh.position.add(step);
    }
    const scope = this.scope?.parts;
    for (const fo of this.fast.values()) {
      const owner = this.parts.get(fo.f.joins[0]);
      const mine = !scope || fo.f.joins.some((p) => scope.has(p));
      if (g) {
        // the guide: only ever along its own axis from its seat (never a part's offset), spinning on
        if (!fastIn.has(fo.f.id)) {
          fo.obj.matrix.copy(fo.base);
          continue;
        }
        // waiting clear of a part it goes through that is still coming in: as far again out along its own axis
        const side = fastenerTravel(fo.f.spec).side;
        const outDir = new THREE.Vector3(0, 0, -side).transformDirection(fo.base);
        let clear = 0;
        for (const j of fo.f.joins) {
          const po = this.parts.get(j);
          const u = inStep.has(j) ? this.seqU(j) : 0;
          if (!po || u <= 0) continue;
          const o = (this.guideFrom.get(j)?.clone() ?? new THREE.Vector3()).multiplyScalar(GUIDE_PULL.part * u);
          clear = Math.max(clear, o.dot(outDir) + 4);
        }
        fo.obj.matrix.copy(fastenerPose(fo.base, fo.f.spec, this.seqU(fo.f.id), undefined, clear));
        continue;
      }
      const back = (mine ? 22 * this.explode : 0) + (fastIn.has(fo.f.id) ? insert * 40 : 0); // driven in along its axis
      const m = fo.base.clone().multiply(new THREE.Matrix4().makeTranslation(0, 0, -back));
      if (owner && mine) {
        const d = this.offsetOf(owner, this.explode);
        m.premultiply(new THREE.Matrix4().makeTranslation(d.x, d.y, d.z));
      }
      fo.obj.matrix.copy(m);
    }
    this.root.updateMatrixWorld(true);
    this.updateGuidePaths();
    this.updateMarkers();
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
    let dist = Math.max(size.y / 2 / tanV, halfW / (tanV * 0.95)) * 1.12 + halfW;
    if (this.guide) {
      // the guide's model area (beside the text column), the box's sphere in it with a margin
      const a = this.guideArea();
      const r = size.length() / 2;
      const tanY = tanV * a.sy;
      const tanX = tanV * (a.w / a.h) * a.sx;
      dist = (r / Math.min(tanY, tanX)) * 0.92;
    }
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

/**
 * The overlay channel of a part's material: a light emissive rim (stronger at grazing angles, a
 * faint lift face-on) in one colour, set by `rim()`. It is how a hovered joint's moving parts, the
 * selection, a step's or a check's parts are marked - over the look, never instead of it: the
 * colour, opacity and side stay the look's. One shader program for every part (the cache key).
 */
function withRim(m: THREE.MeshStandardMaterial) {
  const u = { value: new THREE.Vector4(0, 0, 0, 0) };
  m.userData.rim = u;
  m.onBeforeCompile = (sh) => {
    sh.uniforms.uRim = u;
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform vec4 uRim;')
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
        if (uRim.a > 0.0) {
          float rimF = 1.0 - clamp(abs(dot(normalize(normal), normalize(vViewPosition))), 0.0, 1.0);
          totalEmissiveRadiance += uRim.rgb * uRim.a * (0.04 + 0.96 * pow(rimF, 3.0));
        }`);
  };
  m.customProgramCacheKey = () => 'r3x-rim';
  return m;
}

/** Mark a part (colour, strength 0..1) or clear the mark (strength 0). */
function rim(m: THREE.MeshStandardMaterial, color: number, strength: number) {
  const u = (m.userData.rim as { value: THREE.Vector4 } | undefined)?.value;
  if (!u) return;
  const c = new THREE.Color(color);
  u.set(c.r, c.g, c.b, strength);
}

type RenderItem = { groupOrder: number; renderOrder: number; z: number; id: number };
function ghostSort(a: RenderItem, b: RenderItem) {
  if (a.groupOrder !== b.groupOrder) return a.groupOrder - b.groupOrder;
  if (a.renderOrder !== b.renderOrder) return a.renderOrder - b.renderOrder;
  if (a.z !== b.z) return a.renderOrder === 2 ? a.z - b.z : b.z - a.z;
  return a.id - b.id;
}

/** Opacity and clipping; recompiles the material only when its program would change. */
function setLook(m: THREE.MeshStandardMaterial, opacity: number, clip: THREE.Plane[] | null) {
  const transparent = opacity < 1;
  // a faint ghost draws its outer faces only: two layers per shell piled up into a white fog
  m.side = opacity < 0.3 ? THREE.FrontSide : THREE.DoubleSide;
  const program = transparent !== m.transparent || (clip?.length ?? 0) !== (m.clippingPlanes?.length ?? 0);
  m.transparent = transparent;
  m.opacity = opacity;
  m.depthWrite = true; // a ghost too: with the nearest-first sort (ghostSort) only its front surface draws
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

/** A part id without its index or side (morton_post_low_3 -> morton_post_low), as mech/workbench/steps.py groups like parts. */
export function partStem(id: string): string {
  let s = id.toLowerCase();
  for (;;) {
    const t = s.replace(/_(\d+|l|r|m|left|right|full)$/, '');
    if (t === s || !t) return s;
    s = t;
  }
}
