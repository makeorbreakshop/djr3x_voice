/**
 * Moving parts in Build, the way Fusion and Onshape do it - navigation first:
 *
 * - The camera is navigate.ts's (a plain drag on a part navigates; a click selects). A press this module takes
 *   (a handle, a part to move) is claimed (`claims`) and the camera leaves it alone.
 * - Handles: a selected part (or, with ⌘/Ctrl held or in Move mode, a hovered one) shows a thin ring per
 *   revolute joint of its chain and an arrow per prismatic one, at the joint, sized to the screen. A handle
 *   always drags its one joint.
 * - ⌘ (Mac) / Ctrl (Windows) + drag on a part's body moves it - grab cursor and a tint on what moves while
 *   held, a lock on a grounded part. Move mode (M, or Move in the view bar; Esc leaves) does the same with no
 *   key. Touch: a long-press on a part, then drag, moves it.
 * - One joint in its chain turns like a door or slides along its axis. Several (Hunter's head: tilt and
 *   roll): the first ~8 px lock onto the joint whose motion matches the cursor. Shift: all of them by IK.
 * - An actuator's output: a pinion or direct-drive horn turns its joint through its ratio (either visor horn
 *   turns the visor). A push-rod horn: "Drive: servo" turns that servo and the rods decide the joints;
 *   "Drive: joint" (the pill by a selected horn, or D) moves the joint it mostly drives.
 * - Smooth: the solve runs once per frame on the newest pointer position, warm-started; the model follows
 *   through a critically damped spring (~65 ms), landing on the solve at release. Limits ease in (tanh),
 *   coupled limits hold, a push-rod pose the rods cannot reach keeps the last good one.
 * - No moving parts while exploded or in Instructions. Ctrl/Cmd+Z puts back the pose before a move; the arrow
 *   keys nudge the selected part's joint (Left/Right the nearest, Up/Down the next one up; Shift: x5).
 */

import * as THREE from 'three';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { actuators, servoAngle, solveServo, type Actuator } from '../mechrig/servos';
import { linkMatrices, type Pose } from './kinematics';
import {
  couplingAt, couplingRangeA, jointChain, moves, partDrive, pickByDirection, pickChain, planeBasis, rayAngle, rayLineParam,
  dragMode, nextMoveMode, pressAction, rotaryValue, softLimit, solveIK, springStep, type ChainJoint, type ChainMode, type Coupling,
} from './drag';
import type { MGear, MJoint, MLinkage } from './manifest';
import { platformOf, partKey } from './navigate';
import { jointLabel } from './systems';
import type { AsmNode, BuildHost, Workbench } from './workbench';

type DriveMode = 'servo' | 'joint';

/** What a press on a part would move. */
type Target =
  | { kind: 'chain'; chain: ChainJoint<AsmNode>[]; node: AsmNode; link: string | null; mode: ChainMode }
  | { kind: 'servo'; a: Actuator; lk: MLinkage }
  | { kind: 'gear'; gear: MGear; gearNode: AsmNode; node: AsmNode; joint: MJoint };

interface Rotary {
  kind: 'rotary';
  /** The frame the axis is stated in, at the drag's solved pose (it may move with the value: a horn on the head). */
  frame: () => THREE.Matrix4;
  centre: THREE.Vector3;
  axis: THREE.Vector3;
  /** The zero direction: towards the grabbed point at the start (so the grab sits at angle 0). */
  u: THREE.Vector3;
  /** Degrees the grabbed point turns per unit of the value. */
  rate: number;
  v0: number;
  raw: number;
  radius: number;
  local: THREE.Vector3;
  soft: boolean;
  get(): number;
  set(v: number): void;
  limits(): [number, number];
  label(): string;
}

interface Linear {
  kind: 'linear';
  frame: () => THREE.Matrix4;
  origin: THREE.Vector3;
  dir: THREE.Vector3;
  v0: number;
  raw: number;
  get(): number;
  set(v: number): void;
  limits(): [number, number];
  label(): string;
}

interface IK {
  kind: 'ik';
  chain: ChainJoint<AsmNode>[];
  node: AsmNode;
  link: string | null;
  local: THREE.Vector3;
  plane: THREE.Plane;
  weights: number[];
  target: THREE.Vector3;
  label(): string;
}

/** A free drag on a part of several joints, waiting for the cursor's first pixels to say which. */
interface Lock {
  kind: 'lock';
  chain: ChainJoint<AsmNode>[];
  node: AsmNode;
  link: string | null;
  point: THREE.Vector3;
  label(): string;
}

type Grab = Rotary | Linear | IK | Lock;

interface Handle {
  node: AsmNode;
  joint: MJoint;
  /** A dark underlay and the orange line over it (screen-width lines: they read on any paint). */
  obj: THREE.Group;
  top: LineSegments2;
  /** Its outline as segment pairs, unit size in the handle's own frame. */
  unit: THREE.Vector3[];
  /** The same in world space (for hit testing), refreshed each frame. */
  pts: THREE.Vector3[];
}

const ACCENT = 0xe8762a;
/** The tanh cushion at each end of a joint's range (deg or mm), at most a tenth of the range. */
const SOFT = 2.5;
/** The shown pose follows the solve with this spring: 95 % of a step in ~65 ms. */
const OMEGA = 72;
const LOCK_PX = 8;
const HANDLE_PX = 46;
const sgn = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}`;
const unitOf = (j: MJoint) => (j.unit === 'mm' ? ' mm' : '°');
const SERVO_NAME: Record<string, string> = {
  servo_l: 'Gimbal L servo', servo_r: 'Gimbal R servo', visor_servo: 'Visor servo',
  visor_servo_l: 'Visor L servo', visor_servo_r: 'Visor R servo', tilt_servo: 'Tilt servo',
  pan_servo: 'Pan servo', lift_servo: 'Lift servo', lower_servo: 'Lower ring servo', top_servo: 'Top ring servo',
  col_pan_servo: 'Pan servo', col_lift_servo: 'Lift servo', col_lower_servo: 'Lower ring servo', col_top_servo: 'Top ring servo',
};
const servoName = (s: string) => SERVO_NAME[s] ?? s;
const LOCK_CURSOR = `url("data:image/svg+xml;utf8,${encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24'><path d='M2 1v15l4-4 3 6 2-1-3-6h5z' fill='#fff' stroke='#000'/>"
  + "<path d='M15.5 15v-2a2.5 2.5 0 0 1 5 0v2' fill='none' stroke='#2a2c31' stroke-width='1.4'/><rect x='13.5' y='15' width='9' height='7' rx='1.2' fill='#8a8f99' stroke='#2a2c31'/></svg>",
)}") 2 1, default`;
const DRIVE_KEY = 'r3x.build.drive';
const LONG_PRESS_MS = 450;
const PLATFORM = platformOf(navigator.platform || navigator.userAgent);
const MOD_NAME = PLATFORM === 'mac' ? '⌘' : 'Ctrl';

export class DirectDrag {
  /** A drag is moving the model (the puppet and the camera stand down). */
  dragging = false;
  /** Push-rod horns: turn the servo, or move the joint it mostly drives. */
  driveMode: DriveMode = (() => {
    try {
      return localStorage.getItem(DRIVE_KEY) === 'joint' ? 'joint' : 'servo';
    } catch {
      return 'servo';
    }
  })();
  private down: {
    x: number; y: number; pointer: number; touch: boolean; hit: THREE.Intersection | null; target: Target | null;
    handle: Handle | null; why: 'grounded' | 'exploded' | null; mods: { free: boolean; ground: boolean };
  } | null = null;
  private grab: Grab | null = null;
  /** The latest pointer position, and whether the solve has seen it. */
  private ptr = { x: 0, y: 0, dirty: false };
  /** The drag's solved pose (sparse, over the nodes' own) and the shown pose's spring state. */
  private solved = new Map<AsmNode, Pose>();
  private springs = new Map<string, { node: AsmNode; id: string; x: number; v: number }>();
  private lastTick = 0;
  private justDragged = false;
  private tag: HTMLDivElement | null = null;
  private pill: HTMLDivElement | null = null;
  private hintTimer = 0;
  private lastHint = 0;
  private readonly giz = new THREE.Group();
  private readonly ring: THREE.LineLoop;
  private readonly arc: THREE.Line;
  private readonly axisLine: THREE.Line;
  private readonly track: THREE.Line;
  private readonly dot: THREE.Points;
  private readonly lead: THREE.Line;
  private readonly handleGroup = new THREE.Group();
  private readonly handleMat: LineMaterial;
  private readonly handleHot: LineMaterial;
  private readonly handleUnder: LineMaterial;
  private handles: Handle[] = [];
  private handleKey = '';
  private hotHandle: Handle | null = null;
  /** The part whose handles show while nothing is selected (the pointer is on it, or on its handles). */
  private hoverMesh: THREE.Object3D | null = null;
  private hoverAt = 0;
  private hoverTimer = 0;
  private longFrom: { x: number; y: number; pointer: number } | null = null;
  /** Move mode (M, or the view bar's Move): a plain left drag moves parts instead of orbiting. */
  moveMode = false;
  /** ⌘ (Mac) or Ctrl held: a drag on a part's body moves it. */
  private modHeld = false;
  /** Shift held: in a focus, the joints further up the chain too (their handles show, a drag may move them). */
  private shiftHeld = false;
  private lastPtr = { x: -1, y: -1 };
  private longTimer = 0;
  /** The joint tint (Workbench.setHover) is ours: the parts a ⌘-drag here would move. */
  private tinting = false;
  private moveBtn: HTMLButtonElement | null = null;
  private moveBadge: HTMLDivElement | null = null;
  private lastNudge = 0;

  constructor(private readonly wb: Workbench, private readonly host: BuildHost) {
    const mat = (opacity: number) => new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity, depthTest: false, depthWrite: false });
    this.ring = new THREE.LineLoop(circleGeometry(72), mat(0.3));
    this.arc = new THREE.Line(new THREE.BufferGeometry(), mat(0.95));
    this.axisLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, -0.6), new THREE.Vector3(0, 0, 0.6)]), mat(0.5));
    this.track = new THREE.Line(new THREE.BufferGeometry(), mat(0.55));
    this.dot = new THREE.Points(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3()]),
      new THREE.PointsMaterial({ color: ACCENT, size: 7, sizeAttenuation: false, depthTest: false, depthWrite: false, transparent: true }));
    this.lead = new THREE.Line(new THREE.BufferGeometry(), mat(0.6));
    for (const o of [this.ring, this.arc, this.axisLine, this.track, this.dot, this.lead]) this.overlay(o, this.giz);
    this.giz.name = 'build_drag_gizmo';
    this.giz.matrixAutoUpdate = false;
    this.giz.visible = false;
    host.scene.add(this.giz);
    const fat = (color: number, linewidth: number, opacity: number) => new LineMaterial({ color, linewidth, transparent: true, opacity, depthTest: false, depthWrite: false });
    this.handleMat = fat(ACCENT, 1.6, 0.9);
    this.handleHot = fat(ACCENT, 2.6, 1);
    this.handleUnder = fat(0x101216, 4.5, 0.45);
    this.handleGroup.name = 'build_joint_handles';
    host.scene.add(this.handleGroup);

    const el = host.renderer.domElement;
    // capture: ahead of OrbitControls' own pointerdown on the canvas, so a press on a part never starts an orbit
    el.addEventListener('pointerdown', (e) => this.onDown(e), { capture: true });
    el.addEventListener('pointermove', (e) => this.onMove(e), { capture: true });
    el.addEventListener('pointerup', (e) => this.onUp(e), { capture: true });
    el.addEventListener('pointercancel', (e) => this.onUp(e), { capture: true });
    el.addEventListener('pointerleave', () => {
      if (this.down) return;
      this.cursor('');
      this.say(null);
      this.tint(null);
      this.lastPtr = { x: -1, y: -1 };
      if (this.hotHandle) {
        this.hotHandle = null;
        this.host.interact();
      }
    });
    addEventListener('keydown', (e) => this.onKey(e));
    // ⌘/Ctrl down or up changes what a drag on the part under the pointer would do: the cursor and tint follow
    const modKey = (e: KeyboardEvent) => {
      const held = partKey(e, PLATFORM);
      if (held !== this.modHeld || e.shiftKey !== this.shiftHeld) {
        this.modHeld = held;
        this.shiftHeld = e.shiftKey;
        if (this.lastPtr.x >= 0 && !this.down) this.hover(this.lastPtr.x, this.lastPtr.y);
        this.host.interact(); // the handles come and go with it
      }
    };
    addEventListener('keydown', modKey);
    addEventListener('keyup', modKey);
    addEventListener('blur', () => {
      this.modHeld = false;
      this.shiftHeld = false;
      this.tint(null);
    });
    this.mountMoveButton();
  }

  /** The view bar's Move toggle, beside the looks (Build only, CSS). */
  private mountMoveButton() {
    const looks = document.querySelector('#view-bar .bb-looks');
    if (!looks || this.moveBtn) return;
    const seg = document.createElement('div');
    seg.className = 'vb-seg bb-move';
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('aria-pressed', 'false');
    b.title = `Move parts: a drag on a part moves it instead of turning the view (M; or hold ${MOD_NAME} and drag)`;
    b.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5v13M1.5 8h13M8 1.5 6 3.5M8 1.5l2 2M8 14.5l-2-2M8 14.5l2-2M1.5 8l2-2M1.5 8l2 2M14.5 8l-2-2M14.5 8l-2 2"/></svg><span>Move</span>';
    b.onclick = () => {
      this.setMoveMode(!this.moveMode);
      b.blur();
    };
    seg.append(b);
    looks.after(seg);
    this.moveBtn = b;
  }

  setMoveMode(on: boolean) {
    if (on === this.moveMode) return;
    this.moveMode = on;
    this.moveBtn?.setAttribute('aria-pressed', String(on));
    if (on && !this.moveBadge) {
      const d = document.createElement('div');
      d.className = 'bb-move-badge';
      d.setAttribute('role', 'status');
      d.innerHTML = '<b>Move</b> drag a part to move it · <kbd>M</kbd> or <kbd>Esc</kbd> to orbit again';
      document.body.append(d);
      this.moveBadge = d;
    }
    if (this.moveBadge) this.moveBadge.hidden = !on;
    document.body.classList.toggle('bb-moving', on);
    if (this.lastPtr.x >= 0) this.hover(this.lastPtr.x, this.lastPtr.y);
    this.host.interact();
  }

  /** Tint what a part drag would move (null: none), only while a ⌘-drag or Move mode would move it. */
  private tint(t: Target | null) {
    const pick = !t ? null : t.kind === 'chain' ? t.chain[0] : t.kind === 'gear' ? { node: t.node, joint: t.joint } : { node: t.a.node, joint: t.a.joints[0] };
    if (!pick) {
      if (this.tinting) this.wb.setHover(null, null);
      this.tinting = false;
      return;
    }
    this.wb.setHover(pick.node, pick.joint.id);
    this.tinting = true;
  }

  private overlay(o: THREE.Object3D, parent: THREE.Object3D) {
    o.renderOrder = 60;
    o.frustumCulled = false;
    o.raycast = () => {};
    parent.add(o);
  }

  /** Whether this press is ours (a handle, or a part to move): the camera (navigate.ts) leaves it alone. */
  claims(pointerId: number): boolean {
    return !!this.down && this.down.pointer === pointerId && !this.down.why;
  }

  /** Whether the pointer handler of a click should stand down (the press was a drag). */
  takeClick(): boolean {
    const was = this.justDragged;
    this.justDragged = false;
    return was;
  }

  private usable() {
    const wb = this.wb;
    return wb.active && !!wb.top && !wb.guide && !wb.video;
  }

  setDriveMode(m: DriveMode) {
    this.driveMode = m;
    try {
      localStorage.setItem(DRIVE_KEY, m);
    } catch { /* private window */ }
    this.syncPill();
  }

  // ------------------------------------------------------------------ what a part moves

  /** The press target of a mesh (null: grounded). */
  /** The press target of a mesh (null: grounded - or, in a focus, moved by none of its joints). In a system or
   *  assembly focus only the focus's own joints move, unless `wide` (Shift held): then the part's chain as usual. */
  targetOf(mesh: THREE.Object3D, mode: ChainMode, wide = false): Target | null {
    const info = this.wb.grabOf(mesh);
    if (!info) return null;
    const mine = focusJoints(this.wb.scope, wide);
    const ours = (node: AsmNode, j: MJoint) => !mine || mine.has(`${node.key}:${j.id}`);
    const drive = partDrive(info.node.asm, info.id);
    if (drive?.kind === 'linkage') {
      const a = actuators([info.node]).find((x) => x.servo === drive.servo);
      const lk = info.node.asm.linkages?.find((l) => l.id === drive.linkage);
      if (a && lk && a.joints.some((j) => ours(a.node, j))) return { kind: 'servo', a, lk };
    }
    if (drive?.kind === 'gear') {
      const g = drive.gear;
      const node = g.joint_assembly ? this.wb.nodeOf(g.joint_assembly) : info.node;
      const joint = node?.asm.joints.find((j) => j.id === g.joint);
      if (node && joint && moves(joint) && ours(node, joint)) return { kind: 'gear', gear: g, gearNode: info.node, node, joint };
    }
    const all = jointChain(info.node, info.link);
    const chain = mine ? all.filter((c) => ours(c.node, c.joint)) : pickChain(all, mode);
    return chain.length ? { kind: 'chain', chain, node: info.node, link: info.link, mode } : null;
  }

  // ------------------------------------------------------------------ limits

  private couplings(): Coupling[] {
    return ((this.wb.manifest?.root as { couplings?: Coupling[] } | undefined)?.couplings) ?? [];
  }

  /** The joint called `name` (id or profile name) nearest `from`: itself, its ancestors, its subtree, anywhere. */
  private findJoint(from: AsmNode, name: string): { node: AsmNode; joint: MJoint } | null {
    const has = (n: AsmNode) => (this.wb.nodeShown(n) ? n.asm.joints.find((j) => j.id === name || j.profile_joint === name) : undefined);
    for (let n: AsmNode | null = from; n; n = n.parent) {
      const j = has(n);
      if (j) return { node: n, joint: j };
    }
    let hit: { node: AsmNode; joint: MJoint } | null = null;
    const look = (root: AsmNode | undefined) => this.wb.forEachNode((n) => {
      const j = !hit && has(n);
      if (j) hit = { node: n, joint: j };
    }, root);
    look(from);
    if (!hit) look(undefined);
    return hit;
  }

  /** A joint's range now: its limits, narrowed by the couplings it is in (either side). */
  limitsOf(node: AsmNode, j: MJoint, poseOf: (n: AsmNode) => Pose = (n) => n.pose): [number, number] {
    let lo = j.limits.min, hi = j.limits.max;
    const names = [j.id, j.profile_joint].filter(Boolean);
    for (const c of this.couplings()) {
      if (names.includes(c.joint)) {
        const dep = this.findJoint(node, c.depends_on);
        const r = dep && couplingAt(c, poseOf(dep.node)[dep.joint.id] ?? 0);
        if (r) {
          lo = Math.max(lo, r[0]);
          hi = Math.min(hi, r[1]);
        }
      } else if (names.includes(c.depends_on)) {
        const b = this.findJoint(node, c.joint);
        if (b) [lo, hi] = couplingRangeA(c, poseOf(b.node)[b.joint.id] ?? 0, poseOf(node)[j.id] ?? 0, lo, hi);
      }
    }
    return lo <= hi ? [lo, hi] : [j.limits.min, j.limits.max];
  }

  private soft(lo: number, hi: number) {
    return Math.min(SOFT, 0.1 * (hi - lo));
  }

  // ------------------------------------------------------------------ the solved pose

  /** A node's pose as the drag has solved it (the shown pose lags it through the spring). */
  private poseOf = (n: AsmNode): Pose => {
    const s = this.solved.get(n);
    return s ? { ...n.pose, ...s } : n.pose;
  };

  private val(node: AsmNode, id: string) {
    return this.solved.get(node)?.[id] ?? node.pose[id] ?? 0;
  }

  private put(node: AsmNode, id: string, v: number) {
    let s = this.solved.get(node);
    if (!s) this.solved.set(node, (s = {}));
    s[id] = v;
    const k = `${node.key}:${id}`;
    if (!this.springs.has(k)) this.springs.set(k, { node, id, x: node.pose[id] ?? 0, v: 0 });
  }

  /** A link's world matrix with some poses overridden (trial or solved poses), without touching the model. */
  private linkWorld(n: AsmNode, link: string | null, poseOf: (n: AsmNode) => Pose): THREE.Matrix4 {
    const g = this.groupWorld(n, poseOf);
    const m = link ? linkMatrices(n.asm.links, n.asm.joints, poseOf(n)).get(link) : undefined;
    return m ? g.multiply(m) : g;
  }

  private groupWorld(n: AsmNode, poseOf: (n: AsmNode) => Pose): THREE.Matrix4 {
    if (!n.parent) return n.group.matrixWorld.clone();
    const pl = n.asm.mount?.parent_link;
    const base = pl && n.parent.links.has(pl) ? this.linkWorld(n.parent, pl, poseOf) : this.groupWorld(n.parent, poseOf);
    return base.multiply(n.group.matrix);
  }

  // ------------------------------------------------------------------ servos

  /** Turn an actuator to `deg` (within its range) in the solved pose, stopping short where the linkage or a
   *  joint limit says so, and never jumping to the rods' other branch (the last good pose holds). */
  private setServo(a: Actuator, deg: number, into: (id: string, v: number) => void = (id, v) => this.put(a.node, id, v)): void {
    const base = this.poseOf(a.node);
    const cur = servoAngle(a, base) ?? 0;
    const d = Math.min(a.range[1], Math.max(a.range[0], deg));
    const ok = (x: number) => {
      const sol = solveServo(a, x, base);
      if (!sol) return null;
      for (const [id, v] of Object.entries(sol)) {
        const j = a.node.asm.joints.find((q) => q.id === id)!;
        const [lo, hi] = this.limitsOf(a.node, j, (n) => (n === a.node ? { ...base, ...sol } : this.poseOf(n)));
        if (v < lo - 1e-3 || v > hi + 1e-3) return null;
        // continuity: a few degrees of servo never throws a joint far (a branch flip near the rods' edge)
        if (Math.abs(v - (base[id] ?? 0)) > 2 + 3 * Math.abs(x - cur)) return null;
      }
      return sol;
    };
    let sol = ok(d);
    if (!sol) {
      let good = cur;
      let bad = d;
      for (let i = 0; i < 14; i++) {
        const mid = (good + bad) / 2;
        if (ok(mid)) good = mid;
        else bad = mid;
      }
      sol = Math.abs(good - cur) > 1e-6 ? ok(good) : null;
    }
    if (sol) for (const [id, v] of Object.entries(sol)) into(id, v);
  }

  /** The joint a push-rod servo mostly drives here (relative to its range). */
  private dominantJoint(a: Actuator): MJoint {
    const base = this.poseOf(a.node);
    const cur = servoAngle(a, base) ?? 0;
    const sol = solveServo(a, cur + 2, base) ?? solveServo(a, cur - 2, base);
    let best = a.joints[0];
    let score = -1;
    for (const j of a.joints) {
      const dv = Math.abs((sol?.[j.id] ?? 0) - (base[j.id] ?? 0)) / Math.max(1e-6, j.limits.max - j.limits.min);
      if (dv > score) {
        score = dv;
        best = j;
      }
    }
    return best;
  }

  /** "Gimbal L servo +7° · Gimbal R servo −3°": every servo of a push-rod group at the solved pose. */
  private servoLine(a: Actuator) {
    const group = actuators([a.node]).filter((x) => x.group?.some((l) => l.id === a.linkage?.id) || x.servo === a.servo);
    return group.map((x) => `${servoName(x.servo)} ${sgn(servoAngle(x, this.poseOf(a.node)) ?? 0)}°`).join(' · ');
  }

  // ------------------------------------------------------------------ a drag

  private ray(x: number, y: number): THREE.Ray {
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const rc = new THREE.Raycaster();
    rc.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), this.host.camera);
    return rc.ray;
  }

  private jointText(node: AsmNode, j: MJoint) {
    return `${jointLabel(j.name)} ${sgn(this.val(node, j.id))}${unitOf(j)}`;
  }

  /** One joint, grabbed at a world point: a door for a revolute joint, a slide for a prismatic one. */
  private single(node: AsmNode, j: MJoint, point: THREE.Vector3, label?: () => string): Grab | null {
    if (!node.links.get(j.parent_link)) return null;
    const frame = () => this.linkWorld(node, j.parent_link, this.poseOf);
    const get = () => this.val(node, j.id);
    const set = (v: number) => this.put(node, j.id, v);
    const limits = () => this.limitsOf(node, j, this.poseOf);
    const lab = label ?? (() => this.jointText(node, j));
    if (j.type === 'prismatic') {
      const origin = point.clone().applyMatrix4(frame().invert());
      return { kind: 'linear', frame, origin, dir: new THREE.Vector3(...j.axis).normalize(), v0: get(), raw: get(), get, set, limits, label: lab };
    }
    return this.rotary({ frame, centre: new THREE.Vector3(...j.pivot), axis: new THREE.Vector3(...j.axis), rate: 1, v0: get(), soft: true, get, set, limits, label: lab }, point);
  }

  private rotary(o: Omit<Rotary, 'kind' | 'u' | 'raw' | 'radius' | 'local'>, point: THREE.Vector3): Rotary {
    const local = point.clone().applyMatrix4(o.frame().invert());
    const n = o.axis.clone().normalize();
    const off = local.clone().sub(o.centre);
    off.addScaledVector(n, -off.dot(n));
    const radius = Math.max(off.length(), 1);
    const u = off.lengthSq() > 1e-9 ? off.normalize() : planeBasis(n).u;
    // the drag plane square to the axis through the grabbed point (not the pivot, which may sit far along the
    // axis - a ring's is at the floor): the same turn, and the cursor stays on the part
    const centre = o.centre.clone().addScaledVector(n, local.clone().sub(o.centre).dot(n));
    return { kind: 'rotary', ...o, centre, axis: n, u, raw: o.v0, radius, local };
  }

  private begin(t: Target, point: THREE.Vector3, mods: { free: boolean; ground: boolean }): Grab | null {
    if (t.kind === 'servo') {
      const { a, lk } = t;
      if (this.driveMode === 'joint') {
        const j = this.dominantJoint(a);
        return this.single(a.node, j, point, () => `${this.jointText(a.node, j)} → ${this.servoLine(a)}`);
      }
      if (!a.node.links.get(lk.horn.link)) return null;
      const v0 = servoAngle(a, this.poseOf(a.node)) ?? 0;
      return this.rotary({
        frame: () => this.linkWorld(a.node, lk.horn.link, this.poseOf), centre: new THREE.Vector3(...lk.horn.centre),
        axis: new THREE.Vector3(...lk.horn.axis), rate: 1, v0, soft: false,
        get: () => servoAngle(a, this.poseOf(a.node)) ?? v0,
        set: (v) => this.setServo(a, v),
        limits: () => a.range,
        label: () => `${servoName(a.servo)} ${sgn(servoAngle(a, this.poseOf(a.node)) ?? 0)}° → ${a.joints.map((j) => this.jointText(a.node, j)).join(' · ')}`,
      }, point);
    }
    if (t.kind === 'gear') {
      const { gear, gearNode, node, joint } = t;
      if (!gearNode.links.get(gear.link)) return null;
      // every servo on this joint (the visor's mirrored pair turns together)
      const servos = (node.asm.gears ?? []).filter((g) => g.joint === joint.id && g.servo && !g.joint_assembly);
      const list = servos.length ? servos : gear.servo ? [gear] : [];
      return this.rotary({
        frame: () => this.linkWorld(gearNode, gear.link, this.poseOf), centre: new THREE.Vector3(...gear.pivot),
        axis: new THREE.Vector3(...gear.axis), rate: gear.deg_per_unit, v0: this.val(node, joint.id), soft: true,
        get: () => this.val(node, joint.id),
        set: (v) => this.put(node, joint.id, v),
        limits: () => this.limitsOf(node, joint, this.poseOf),
        label: () => {
          const v = this.val(node, joint.id);
          const per = (g: MGear) => g.servo_deg_per_unit ?? joint.drive?.servo_deg_per_unit;
          const sv = list.filter((g) => per(g)).map((g) => `${servoName(g.servo!)} ${sgn(v * per(g)!)}°`).join(' · ');
          return `${sv ? `${sv} → ` : ''}${this.jointText(node, joint)}`;
        },
      }, point);
    }
    const { chain } = t;
    if (chain.length === 1) return this.single(chain[0].node, chain[0].joint, point);
    if (mods.free) {
      // free: the grabbed point follows the cursor on every joint, in the plane facing the camera
      const lw = this.linkWorld(t.node, t.link, this.poseOf);
      const local = point.clone().applyMatrix4(lw.invert());
      const normal = this.host.camera.getWorldDirection(new THREE.Vector3());
      const k = mods.ground ? 0.6 : 0.5;
      return {
        kind: 'ik', chain, node: t.node, link: t.link, local, plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, point),
        weights: chain.map((_, i) => k ** i), target: point.clone(),
        label: () => `Free · ${chain.map(({ node, joint }) => this.jointText(node, joint)).join(' · ')}`,
      };
    }
    return { kind: 'lock', chain, node: t.node, link: t.link, point, label: () => chain.map(({ joint }) => jointLabel(joint.name)).join(' or ') };
  }

  /** Lock a free drag onto the joint its first pixels point along. */
  private lockOn(g: Lock, dx: number, dy: number): Grab | null {
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const cam = this.host.camera;
    const scr = (p: THREE.Vector3) => {
      const s = p.clone().project(cam);
      return new THREE.Vector2(((s.x + 1) / 2) * r.width, ((1 - s.y) / 2) * r.height);
    };
    const local = g.point.clone().applyMatrix4(this.linkWorld(g.node, g.link, this.poseOf).invert());
    const at = scr(g.point);
    const motion = g.chain.map(({ node, joint }) => {
      const h = joint.type === 'prismatic' ? 0.5 : 0.5;
      const p = local.clone().applyMatrix4(this.linkWorld(g.node, g.link, (n) => (n === node ? { ...this.poseOf(n), [joint.id]: this.val(node, joint.id) + h } : this.poseOf(n))));
      return scr(p).sub(at).divideScalar(h);
    });
    const i = pickByDirection(motion, { x: dx, y: dy });
    const pick = g.chain[Math.max(0, i)];
    const others = g.chain.length > 1;
    return this.single(pick.node, pick.joint, g.point, () => `${this.jointText(pick.node, pick.joint)}${others ? '  ·  ⇧ all joints' : ''}`);
  }

  /** Solve the grab for the cursor (once per frame). */
  private solve(g: Grab, x: number, y: number) {
    const ray = this.ray(x, y);
    if (g.kind === 'lock') return;
    if (g.kind === 'ik') {
      const target = ray.intersectPlane(g.plane, new THREE.Vector3());
      if (!target) return;
      g.target.copy(target);
      const ids = g.chain.map(({ node, joint }) => ({ node, id: joint.id }));
      const trial = (q: number[]) => {
        const over = new Map<AsmNode, Pose>();
        ids.forEach(({ node, id }, i) => over.set(node, { ...(over.get(node) ?? this.poseOf(node)), [id]: q[i] }));
        return (n: AsmNode) => over.get(n) ?? this.poseOf(n);
      };
      const fk = (q: number[]) => g.local.clone().applyMatrix4(this.linkWorld(g.node, g.link, trial(q)));
      // warm start: from the last solution, so it never hops to another one between frames
      const q0 = ids.map(({ node, id }) => this.val(node, id));
      const q = solveIK(fk, q0, g.chain.map(({ joint }, i) => ({ min: joint.limits.min, max: joint.limits.max, weight: g.weights[i] })), target, {
        normal: g.plane.normal, iters: 8, maxStep: 4,
        limits: (i, qq) => this.limitsOf(g.chain[i].node, g.chain[i].joint, trial(qq)),
      });
      ids.forEach(({ node, id }, i) => this.put(node, id, q[i]));
      return;
    }
    if (g.kind === 'linear') {
      const lr = ray.clone().applyMatrix4(g.frame().invert());
      const s = rayLineParam(lr, g.origin, g.dir);
      if (s !== null && Math.abs(lr.direction.dot(g.dir)) < 0.97) g.raw = g.v0 + s;
      const [lo, hi] = g.limits();
      g.set(softLimit(g.raw, lo, hi, this.soft(lo, hi)));
      return;
    }
    // rotary: the frame may move with the value (a horn on the head it tilts): a few passes on the solved pose
    for (let pass = 0; pass < 3; pass++) {
      const F = g.frame();
      const lr = ray.clone().applyMatrix4(F.clone().invert());
      // a plane seen nearly edge on turns a few pixels into a large angle: drag along the screen path instead
      const c = rayAngle(lr, g.centre, g.axis, g.u, 0.3);
      const before = g.get();
      if (c === null) {
        if (pass > 0) break;
        const ang = (g.rate * (before - g.v0) * Math.PI) / 180;
        const at = g.local.clone().sub(g.centre).applyAxisAngle(g.axis, ang).add(g.centre);
        const tangent = g.axis.clone().cross(at.clone().sub(g.centre)).multiplyScalar((g.rate * Math.PI) / 180);
        const r = this.host.renderer.domElement.getBoundingClientRect();
        const scr = (v: THREE.Vector3) => {
          const s = v.clone().applyMatrix4(F).project(this.host.camera);
          return new THREE.Vector2(r.left + ((s.x + 1) / 2) * r.width, r.top + ((1 - s.y) / 2) * r.height);
        };
        const a0 = scr(at);
        const b = scr(at.clone().addScaledVector(tangent, 0.01)).sub(a0).divideScalar(0.01);
        // the cursor's offset from where the grabbed point is now, along its path
        const l2 = b.lengthSq();
        if (l2 > 1e-6) g.raw += ((x - a0.x) * b.x + (y - a0.y) * b.y) / l2;
      } else {
        g.raw = rotaryValue(g.raw, g.v0, 0, c, g.rate); // the grab sits at angle 0 (u points at it)
      }
      const [lo, hi] = g.limits();
      g.set(g.soft ? softLimit(g.raw, lo, hi, this.soft(lo, hi)) : g.raw);
      if (c === null || Math.abs(g.get() - before) < 0.02) break;
    }
  }

  /** Per drawn frame (Workbench.tick): solve for the latest pointer, spring the shown pose toward it, draw. */
  tick(now: number) {
    const dt = Math.min(0.05, Math.max(0, (now - (this.lastTick || now)) / 1000));
    this.lastTick = now;
    if (this.moveMode && !this.usable()) this.setMoveMode(false); // Instructions, a video: no parts to move
    if (!this.dragging || !this.grab) {
      this.updateHandles();
      this.syncPill();
      return;
    }
    const g = this.grab;
    if (this.ptr.dirty) {
      this.ptr.dirty = false;
      this.solve(g, this.ptr.x, this.ptr.y);
    }
    this.stepSprings(dt);
    this.wb.applyPose(true); // the sliders only, while dragging (Workbench.onPose)
    this.drawGizmo(g);
    this.say(g.label(), this.ptr.x, this.ptr.y);
    // keep the pacer at full rate (and its "input" fresh, so no still frame lands mid-drag) while the hand is on it
    this.host.interact();
  }

  private stepSprings(dt: number) {
    for (const s of this.springs.values()) {
      const target = this.solved.get(s.node)?.[s.id];
      if (target === undefined) continue;
      [s.x, s.v] = springStep(s.x, s.v, target, OMEGA, dt);
      if (Math.abs(s.x - target) < 1e-4 && Math.abs(s.v) < 1e-3) [s.x, s.v] = [target, 0];
      s.node.pose[s.id] = s.x;
    }
  }

  /** Release: the shown pose is the solved one at once (no lag on letting go). */
  private commit() {
    for (const [node, p] of this.solved) Object.assign(node.pose, p);
    this.solved.clear();
    this.springs.clear();
  }

  // ------------------------------------------------------------------ gizmo, handles and label

  /** World size of one screen pixel at a point (measured by projection: right for any camera and zoom). */
  private pxAt(p: THREE.Vector3) {
    const cam = this.host.camera;
    const h = this.host.renderer.domElement.getBoundingClientRect().height || 1;
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.getWorldQuaternion(new THREE.Quaternion()));
    const d = p.distanceTo(cam.position) * 0.01 || 1e-3;
    const a = p.clone().project(cam);
    const b = p.clone().addScaledVector(up, d).project(cam);
    const px = (Math.abs(b.y - a.y) * h) / 2;
    return px > 1e-9 ? d / px : 1e-3;
  }

  private drawGizmo(g: Grab) {
    const show = (...on: THREE.Object3D[]) => {
      for (const o of [this.ring, this.arc, this.axisLine, this.track, this.dot, this.lead]) o.visible = on.includes(o);
    };
    const m = this.giz.matrix;
    if (g.kind === 'rotary') {
      // drawn on the shown pose (the spring's), sized to the screen: 28-140 px whatever the part's size
      const F = this.liveFrame(g);
      const w = new THREE.Vector3().crossVectors(g.axis, g.u);
      const c = g.centre.clone().applyMatrix4(F);
      const mmPerWorld = 1 / new THREE.Vector3().setFromMatrixScale(F).x;
      const px = this.pxAt(c) * mmPerWorld;
      const r = Math.min(140 * px, Math.max(28 * px, g.radius));
      m.copy(F)
        .multiply(new THREE.Matrix4().makeTranslation(g.centre.x, g.centre.y, g.centre.z))
        .multiply(new THREE.Matrix4().makeBasis(g.u, w, g.axis))
        .multiply(new THREE.Matrix4().makeScale(r, r, r));
      const sweep = (g.rate * (this.shownValue(g) - g.v0) * Math.PI) / 180;
      const n = Math.max(2, Math.min(96, Math.ceil(Math.abs(sweep) / 0.05)));
      this.arc.geometry.setFromPoints(Array.from({ length: n + 1 }, (_, i) => {
        const t = (sweep * i) / n;
        return new THREE.Vector3(Math.cos(t), Math.sin(t), 0);
      }));
      this.setDot(Math.cos(sweep), Math.sin(sweep), 0);
      show(this.ring, this.arc, this.axisLine, this.dot);
    } else if (g.kind === 'linear') {
      const [lo, hi] = g.limits();
      m.copy(g.frame());
      const at = (v: number) => g.origin.clone().addScaledVector(g.dir, v - g.v0);
      this.track.geometry.setFromPoints([at(lo), at(hi)]);
      this.setDot(...at(this.shownValue(g)).toArray());
      show(this.track, this.dot);
    } else if (g.kind === 'ik') {
      m.identity();
      const p = g.local.clone().applyMatrix4(this.linkWorld(g.node, g.link, (n) => n.pose));
      this.lead.geometry.setFromPoints([p, g.target]);
      this.setDot(p.x, p.y, p.z);
      show(this.dot, this.lead);
    } else {
      m.identity();
      this.setDot(g.point.x, g.point.y, g.point.z);
      show(this.dot);
    }
    this.giz.matrixWorldNeedsUpdate = true;
    this.giz.visible = true;
  }

  /** A rotary grab's frame at the shown pose. */
  private liveFrame(g: Rotary) {
    return this.withShown(() => g.frame());
  }

  /** Evaluate with the solved pose swapped for the shown one. */
  private withShown<T>(fn: () => T): T {
    const saved = this.solved;
    this.solved = new Map();
    try {
      return fn();
    } finally {
      this.solved = saved;
    }
  }

  private shownValue(g: Rotary | Linear) {
    return this.withShown(() => g.get());
  }

  private setDot(x: number, y: number, z: number) {
    const a = this.dot.geometry.attributes.position as THREE.BufferAttribute;
    a.setXYZ(0, x, y, z);
    a.needsUpdate = true;
    this.dot.geometry.computeBoundingSphere();
  }

  /** The part whose joint handles show: the selected one, else the one under the pointer. */
  private handleSource(): THREE.Object3D | null {
    const sel = this.wb.selected;
    if (sel) return this.wb.parts.get(sel)?.mesh ?? this.wb.fast.get(sel)?.obj ?? null;
    // a hovered part's handles only when a drag would move parts anyway (⌘/Ctrl, Move mode): otherwise they
    // would sit on the part and catch drags meant to orbit
    return this.modHeld || this.moveMode ? this.hoverMesh : null;
  }

  /** Rings and arrows for each joint the part's free drag can move, at the joint, sized to the screen. */
  private updateHandles() {
    const src = this.usable() && this.wb.explode < 1e-3 && !this.dragging ? this.handleSource() : null;
    const t = src && src.visible ? this.targetOf(src, 'default', this.shiftHeld) : null;
    const chain = t?.kind === 'chain' ? t.chain : t?.kind === 'gear' ? [{ node: t.node, joint: t.joint }] : [];
    const key = chain.map((c) => `${c.node.key}:${c.joint.id}`).join('|');
    if (key !== this.handleKey) {
      this.handleKey = key;
      for (const h of this.handles) h.obj.traverse((o) => (o as LineSegments2).geometry?.dispose());
      this.handleGroup.clear();
      this.hotHandle = null;
      this.handles = chain.map(({ node, joint }) => {
        const unit = joint.type === 'prismatic' ? arrowPairs() : ringPairs();
        const geo = new LineSegmentsGeometry().setPositions(unit.flatMap((v) => v.toArray()));
        const obj = new THREE.Group();
        obj.matrixAutoUpdate = false;
        const under = new LineSegments2(geo, this.handleUnder);
        const top = new LineSegments2(geo, this.handleMat);
        this.overlay(under, obj);
        this.overlay(top, obj);
        top.renderOrder = 61;
        this.handleGroup.add(obj);
        return { node, joint, obj, top, unit, pts: [] };
      });
    }
    const cr = this.host.renderer.domElement.getBoundingClientRect();
    for (const m of [this.handleMat, this.handleHot, this.handleUnder]) m.resolution.set(cr.width, cr.height);
    if (!this.handles.length || !src) return;
    src.updateWorldMatrix(true, false);
    const box = new THREE.Box3().setFromObject(src);
    const partC = box.getCenter(new THREE.Vector3());
    this.handles.forEach((h, i) => {
      const F = this.linkWorld(h.node, h.joint.parent_link, (n) => n.pose);
      const n = new THREE.Vector3(...h.joint.axis).normalize();
      const local = partC.clone().applyMatrix4(F.clone().invert());
      const piv = new THREE.Vector3(...h.joint.pivot);
      // on the axis, level with the part (a ring's pivot is at the floor)
      const c = piv.clone().addScaledVector(n, local.clone().sub(piv).dot(n));
      if (h.joint.type === 'prismatic') c.copy(local);
      const world = c.clone().applyMatrix4(F);
      const mm = 1 / new THREE.Vector3().setFromMatrixScale(F).x;
      const r = (HANDLE_PX + 12 * i) * this.pxAt(world) * mm;
      const { u, w } = planeBasis(n);
      h.obj.matrix.copy(F)
        .multiply(new THREE.Matrix4().makeTranslation(c.x, c.y, c.z))
        .multiply(new THREE.Matrix4().makeBasis(u, w, n))
        .multiply(new THREE.Matrix4().makeScale(r, r, r));
      h.obj.matrixWorldNeedsUpdate = true;
      h.pts = h.unit.map((v) => v.clone().applyMatrix4(h.obj.matrix));
      h.top.material = h === this.hotHandle ? this.handleHot : this.handleMat;
    });
  }

  /** The handle under a screen point (within 9 px of its outline), and the nearest outline point. */
  private handleAt(x: number, y: number): { h: Handle; point: THREE.Vector3 } | null {
    if (!this.handles.length) return null;
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const cam = this.host.camera;
    const scr = (p: THREE.Vector3) => {
      const s = p.clone().project(cam);
      return new THREE.Vector2(r.left + ((s.x + 1) / 2) * r.width, r.top + ((1 - s.y) / 2) * r.height);
    };
    let best: { h: Handle; point: THREE.Vector3; d: number } | null = null;
    const m = new THREE.Vector2(x, y);
    for (const h of this.handles) {
      for (let k = 0; k + 1 < h.pts.length; k += 2) {
        const a = h.pts[k];
        const b = h.pts[k + 1];
        const sa = scr(a), sb = scr(b);
        const ab = sb.clone().sub(sa);
        const t = ab.lengthSq() > 1e-9 ? Math.min(1, Math.max(0, m.clone().sub(sa).dot(ab) / ab.lengthSq())) : 0;
        const d = sa.clone().addScaledVector(ab, t).distanceTo(m);
        if (d < 9 && (!best || d < best.d)) best = { h, point: a.clone().lerp(b, t), d };
      }
    }
    return best;
  }

  /** The drive toggle by a selected push-rod horn: Drive servo | joint. */
  private syncPill() {
    const sel = this.wb.selected;
    const mesh = sel && this.usable() ? this.wb.parts.get(sel)?.mesh ?? this.wb.fast.get(sel)?.obj : null;
    const t = mesh ? this.targetOf(mesh, 'default') : null;
    if (t?.kind !== 'servo' || this.dragging) {
      if (this.pill) this.pill.hidden = true;
      return;
    }
    if (!this.pill) {
      const p = document.createElement('div');
      p.className = 'bb-drive';
      p.setAttribute('role', 'group');
      p.setAttribute('aria-label', 'Dragging a horn drives');
      p.innerHTML = '<span>Drive</span><button type="button" data-drive="servo" title="The horn turns its servo; the push rods decide tilt and roll">servo</button>'
        + '<button type="button" data-drive="joint" title="The horn moves the joint its servo mostly drives; both servos follow (D)">joint</button>';
      p.addEventListener('click', (e) => {
        const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-drive]');
        if (b) this.setDriveMode(b.dataset.drive as DriveMode);
      });
      document.body.append(p);
      this.pill = p;
    }
    this.pill.querySelectorAll<HTMLButtonElement>('[data-drive]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.drive === this.driveMode)));
    const box = new THREE.Box3().setFromObject(mesh!);
    const c = box.getCenter(new THREE.Vector3()).project(this.host.camera);
    const r = this.host.renderer.domElement.getBoundingClientRect();
    this.pill.style.left = `${Math.round(r.left + ((c.x + 1) / 2) * r.width + 22)}px`;
    this.pill.style.top = `${Math.round(r.top + ((1 - c.y) / 2) * r.height - 34)}px`;
    this.pill.hidden = false;
  }

  private say(text: string | null, x = 0, y = 0, hint = false) {
    if (!text) {
      if (this.tag) this.tag.hidden = true;
      return;
    }
    if (!this.tag) {
      this.tag = document.createElement('div');
      this.tag.className = 'bb-drag-tag';
      this.tag.setAttribute('role', 'status');
      this.tag.setAttribute('aria-live', 'polite');
      document.body.append(this.tag);
    }
    if (this.tag.textContent !== text) this.tag.textContent = text;
    this.tag.classList.toggle('hint', hint);
    this.tag.style.left = `${Math.round(x + 16)}px`;
    this.tag.style.top = `${Math.round(y + 18)}px`;
    this.tag.hidden = false;
  }

  private hint(text: string, x: number, y: number) {
    const now = performance.now();
    if (now - this.lastHint < 4000) return;
    this.lastHint = now;
    this.say(text, x, y, true);
    clearTimeout(this.hintTimer);
    this.hintTimer = window.setTimeout(() => { if (!this.dragging) this.say(null); }, 1400);
  }

  private cursor(c: string) {
    const el = this.host.renderer.domElement;
    if (el.style.cursor !== c) el.style.cursor = c;
  }

  /** The cursor over what the pointer is on, and the hover handles: a handle grabs; a part orbits (default
   *  cursor) unless ⌘/Ctrl or Move mode would move it (grab, and a tint on what moves; a lock if grounded). */
  private hover(x: number, y: number) {
    this.lastPtr = { x, y };
    if (!this.usable()) {
      this.cursor('');
      this.tint(null);
      return;
    }
    const hh = this.handleAt(x, y);
    if (hh !== null || this.hotHandle !== null) {
      this.hotHandle = hh?.h ?? null;
      this.host.interact();
    }
    if (hh) {
      this.cursor('grab');
      this.tint(null);
      this.say(`${jointLabel(hh.h.joint.name)} ${sgn(hh.h.node.pose[hh.h.joint.id] ?? 0)}${unitOf(hh.h.joint)}`, x, y, true);
      return;
    }
    if (!this.dragging) this.say(null);
    const hit = this.wb.hitAt(x, y);
    const t = hit ? this.targetOf(hit.object, 'default', this.shiftHeld) : null;
    if (hit && t) {
      if (hit.object !== this.hoverMesh) {
        this.hoverMesh = hit.object;
        this.host.interact();
      }
    } else if (this.hoverMesh && !this.nearHandles(x, y)) {
      this.hoverMesh = null;
      this.host.interact();
    }
    const act = pressAction({ button: 0, mod: this.modHeld, moveMode: this.moveMode, onHandle: false,
      target: !hit ? 'none' : t ? 'movable' : 'grounded', exploded: this.wb.explode > 1e-3 });
    this.tint(act === 'move' ? t : null);
    this.cursor(act === 'move' ? 'grab' : act === 'blocked' ? LOCK_CURSOR : '');
  }

  /** Within reach of the shown handles (so moving onto them does not make them go away). */
  private nearHandles(x: number, y: number) {
    if (!this.handles.length) return false;
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const box = new THREE.Box2();
    for (const h of this.handles) for (const p of h.pts) {
      const s = p.clone().project(this.host.camera);
      box.expandByPoint(new THREE.Vector2(r.left + ((s.x + 1) / 2) * r.width, r.top + ((1 - s.y) / 2) * r.height));
    }
    return box.expandByScalar(30).containsPoint(new THREE.Vector2(x, y));
  }

  // ------------------------------------------------------------------ pointer

  private onDown(e: PointerEvent) {
    if (this.down || this.dragging) {
      // a second finger: let go (the pinch is the camera's)
      this.end(e);
      return;
    }
    clearTimeout(this.longTimer);
    if (!this.usable() || e.button !== 0) return; // right drag orbits, middle pans: the camera's
    const dm = dragMode({ shift: e.shiftKey });
    const touch = e.pointerType === 'touch';
    const base = { x: e.clientX, y: e.clientY, pointer: e.pointerId, touch, mods: { free: dm.free, ground: dm.chain === 'extend' } };
    const exploded = this.wb.explode > 1e-3;
    const hh = !exploded ? this.handleAt(e.clientX, e.clientY) : null;
    const hit = hh ? null : this.wb.hitAt(e.clientX, e.clientY);
    const target = hit ? this.targetOf(hit.object, dm.chain, e.shiftKey) : null;
    const input = { button: 0, mod: partKey(e, PLATFORM), moveMode: this.moveMode, onHandle: !!hh,
      target: (!hit ? 'none' : target ? 'movable' : 'grounded') as 'none' | 'movable' | 'grounded', exploded };
    const act = pressAction(input);
    if (act === 'navigate') {
      // a touch held still on a part that moves: after a moment, it is the part's (long-press)
      if (touch && hit && target && !exploded) {
        const { clientX, clientY, pointerId } = e;
        this.longTimer = window.setTimeout(() => {
          if (this.down || pressAction({ ...input, longPress: true }) !== 'move') return;
          this.down = { ...base, x: clientX, y: clientY, pointer: pointerId, hit, target, handle: null, why: null };
          this.say('Move', clientX, clientY - 40, true);
        }, LONG_PRESS_MS);
        this.longFrom = { x: e.clientX, y: e.clientY, pointer: e.pointerId };
      }
      return;
    }
    if (act === 'handle') {
      this.down = { ...base, hit: null, target: null, handle: hh!.h, why: null };
      this.hotHandle = hh!.h;
      (this.down as { handlePoint?: THREE.Vector3 }).handlePoint = hh!.point;
    } else {
      // move, or blocked (grounded, exploded): the camera keeps a blocked drag, with a hint
      this.down = { ...base, hit, target, handle: null, why: act === 'blocked' ? (target ? 'exploded' : 'grounded') : null };
    }
    if (!this.down.why) {
      // a handle or a part to move: the press is ours, not the camera's
    }
  }

  private onMove(e: PointerEvent) {
    const lf = this.longFrom;
    if (lf && e.pointerId === lf.pointer && Math.hypot(e.clientX - lf.x, e.clientY - lf.y) > 8 && !this.down) {
      clearTimeout(this.longTimer); // the finger moved first: it is orbiting
      this.longFrom = null;
    }
    const d = this.down;
    if (!d || e.pointerId !== d.pointer) {
      if (e.buttons === 0 && e.pointerType !== 'touch') {
        // the cursor follows what is under it, at most ~12 times a second (a raycast through the build)
        const now = performance.now();
        clearTimeout(this.hoverTimer);
        if (now - this.hoverAt > 80) {
          this.hoverAt = now;
          this.hover(e.clientX, e.clientY);
        } else {
          const { clientX: x, clientY: y } = e;
          this.hoverTimer = window.setTimeout(() => { this.hoverAt = performance.now(); this.hover(x, y); }, 90);
        }
      }
      return;
    }
    // the newest of the events the browser coalesced into this one: the solve runs once per frame on it
    const list = e.getCoalescedEvents?.() ?? [];
    const last = list.length ? list[list.length - 1] : e;
    const x = last.clientX, y = last.clientY;
    const dist = Math.hypot(x - d.x, y - d.y);
    if (!this.dragging) {
      if (dist <= (d.touch ? 8 : 4)) return;
      if (d.why || (!d.target && !d.handle)) {
        if (d.why === 'grounded') this.hint('Grounded', x, y);
        else if (d.why === 'exploded') this.hint('Collapse the explode to move parts', x, y);
        this.down = null;
        return;
      }
      const hp = (d as { handlePoint?: THREE.Vector3 }).handlePoint;
      const g = d.handle && hp ? this.single(d.handle.node, d.handle.joint, hp) : this.begin(d.target!, d.hit!.point.clone(), d.mods);
      if (!g) {
        this.release();
        return;
      }
      this.wb.stopMotion();
      this.wb.pushUndo();
      this.grab = g;
      this.dragging = true;
      this.lastTick = 0;
      this.updateHandles(); // hidden while dragging
      try { this.host.renderer.domElement.setPointerCapture(e.pointerId); } catch { /* the pointer is gone */ }
      this.cursor('grabbing');
    }
    if (this.grab?.kind === 'lock') {
      if (dist < LOCK_PX) return;
      const locked = this.lockOn(this.grab, x - d.x, y - d.y);
      if (!locked) return;
      this.grab = locked;
    }
    this.ptr = { x, y, dirty: true };
    this.host.interact();
    e.preventDefault();
  }

  private onUp(e: PointerEvent) {
    clearTimeout(this.longTimer);
    this.longFrom = null;
    if (!this.down || e.pointerId !== this.down.pointer) return;
    this.end(e);
  }

  private end(e: PointerEvent) {
    const was = this.dragging && !!this.grab;
    if (this.dragging && this.grab) {
      // the last position counts, then the pose lands where it was solved
      if (this.ptr.dirty) this.solve(this.grab, this.ptr.x, this.ptr.y);
      this.ptr.dirty = false;
      this.commit();
      this.justDragged = true;
      try { this.host.renderer.domElement.releasePointerCapture(e.pointerId); } catch { /* released */ }
    }
    this.release();
    if (was) this.wb.applyPose(true); // the whole panel, now the drag is over
    this.cursor('');
    if (!(e.pointerType === 'touch')) this.hover(e.clientX, e.clientY);
  }

  private release() {
    this.down = null;
    this.grab = null;
    this.dragging = false;
    this.solved.clear();
    this.springs.clear();
    this.giz.visible = false;
    this.say(null);
    this.host.interact();
  }

  // ------------------------------------------------------------------ keys

  private onKey(e: KeyboardEvent) {
    if (!this.usable() || e.defaultPrevented) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'z') {
      if (this.wb.undoPose()) e.preventDefault();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if ((e.key === 'Escape' && this.moveMode) || ((e.key === 'm' || e.key === 'M') && !e.repeat)) {
      const t2 = e.target as HTMLElement | null;
      if (e.key !== 'Escape' && t2 && t2 !== document.body && t2 !== this.host.renderer.domElement && !t2.closest('#view-bar')) return;
      this.setMoveMode(nextMoveMode(this.moveMode, e.key));
      e.preventDefault();
      e.stopImmediatePropagation(); // Esc leaves Move mode only (not the focus too)
      return;
    }
    const onModel = !t || t === document.body || t === this.host.renderer.domElement || t === document.documentElement;
    if ((e.key === 'd' || e.key === 'D') && onModel && (this.pill && !this.pill.hidden || this.dragging)) {
      this.setDriveMode(this.driveMode === 'servo' ? 'joint' : 'servo');
      e.preventDefault();
      return;
    }
    const dir = { ArrowRight: 1, ArrowLeft: -1, ArrowUp: 1, ArrowDown: -1 }[e.key];
    if (!dir || !this.wb.selected || !onModel) return;
    const sel = this.wb.selected;
    const mesh = this.wb.parts.get(sel)?.mesh ?? this.wb.fast.get(sel)?.obj;
    const target = mesh && this.targetOf(mesh, 'default');
    if (!target) return;
    e.preventDefault();
    const now = performance.now();
    if (now - this.lastNudge > 800) this.wb.pushUndo();
    this.lastNudge = now;
    this.wb.stopMotion();
    const k = e.shiftKey ? 5 : 1;
    let text = '';
    if (target.kind === 'servo') {
      const a = target.a;
      this.setServo(a, (servoAngle(a) ?? 0) + 2 * k * dir, (id, v) => (a.node.pose[id] = v));
      text = `${servoName(a.servo)} ${sgn(servoAngle(a) ?? 0)}°`;
    } else {
      const { node, joint } = target.kind === 'gear' ? target : target.chain[e.key === 'ArrowUp' || e.key === 'ArrowDown' ? Math.min(1, target.chain.length - 1) : 0];
      const [lo, hi] = this.limitsOf(node, joint);
      node.pose[joint.id] = Math.min(hi, Math.max(lo, (node.pose[joint.id] ?? 0) + k * dir));
      text = `${jointLabel(joint.name)} ${sgn(node.pose[joint.id])}${unitOf(joint)}`;
    }
    this.wb.applyPose(true);
    // the value by the part, for a moment
    const box = new THREE.Box3().setFromObject(mesh);
    const c = box.getCenter(new THREE.Vector3()).project(this.host.camera);
    const r = this.host.renderer.domElement.getBoundingClientRect();
    this.say(text, r.left + ((c.x + 1) / 2) * r.width, r.top + ((1 - c.y) / 2) * r.height);
    clearTimeout(this.hintTimer);
    this.hintTimer = window.setTimeout(() => { if (!this.dragging) this.say(null); }, 1200);
  }
}

/** A unit circle in the XY plane. */
function circleGeometry(n: number) {
  return new THREE.BufferGeometry().setFromPoints(Array.from({ length: n }, (_, i) => new THREE.Vector3(Math.cos((i / n) * 2 * Math.PI), Math.sin((i / n) * 2 * Math.PI), 0)));
}

/** A unit circle in the XY plane, as segment pairs. */
function circlePairs(n: number) {
  const at = (i: number) => new THREE.Vector3(Math.cos((i / n) * 2 * Math.PI), Math.sin((i / n) * 2 * Math.PI), 0);
  return Array.from({ length: n }, (_, i) => [at(i), at(i + 1)]).flat();
}

/** A revolute handle: the unit ring about Z, an arrowhead on it pointing the positive way (right hand about +Z),
 *  and a short stub of the axis through its centre, so it reads as "turns about this axis, this way". */
function ringPairs() {
  const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const a = 0.5; // where the arrowhead sits on the ring (rad)
  const tip = V(Math.cos(a), Math.sin(a), 0);
  const back = V(Math.sin(a), -Math.cos(a), 0).multiplyScalar(0.2); // against the positive tangent
  const out = V(Math.cos(a), Math.sin(a), 0).multiplyScalar(0.1);
  return [
    ...circlePairs(64),
    tip, tip.clone().add(back).add(out), tip, tip.clone().add(back).sub(out),
    V(0, 0, -0.35), V(0, 0, 0.35),
  ];
}

/** A prismatic handle: an arrow along Z (unit half-length), its head at the positive end, a tick at the other. */
function arrowPairs() {
  const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const h = 0.24;
  return [
    V(0, 0, -1), V(0, 0, 1),
    V(0, 0, 1), V(h, 0, 1 - h), V(0, 0, 1), V(-h, 0, 1 - h),
    V(0, 0, 1), V(0, h, 1 - h), V(0, 0, 1), V(0, -h, 1 - h),
    V(-h * 0.6, 0, -1), V(h * 0.6, 0, -1),
  ];
}

/** The joints a focus lets a drag move ("node key:joint id"), or null (no focus, a library design, or `wide`). */
export function focusJoints(sc: { kind: string; joints: { node: { key: string }; joint: { id: string } }[] } | null, wide: boolean): Set<string> | null {
  if (!sc || sc.kind === 'library' || wide) return null;
  return new Set(sc.joints.map((j) => `${j.node.key}:${j.joint.id}`));
}
