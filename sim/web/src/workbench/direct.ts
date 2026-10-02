/**
 * Direct manipulation in Build, the way Fusion and Onshape do it: press on a part and drag, and the
 * mechanism moves the way its joints allow (drag.ts has the maths).
 *
 * - A part on a moving link drags its own joint: a revolute one like a door (the swept angle about its axis),
 *   a prismatic one along its axis. More than one joint between the part and its assembly's ground (Hunter's
 *   head: tilt and roll; the neck: pan and lift) and the grabbed point follows the cursor by damped least
 *   squares on those joints. Alt: only the nearest joint. Shift: on through every joint to the ground.
 * - An actuator's output drags its servo: a horn (or its rod or ball stud) turns the servo and the push rods
 *   solve the joints; a pinion, spline or hub turns through its ratio and moves its joint.
 * - Grounded parts do not drag (a lock cursor; a drag on one orbits as on empty space). While exploded,
 *   parts do not drag either: the explode is a view of the parts apart, not a pose.
 * - Limits hold: soft at the ends while dragging (settling on release), and the manifest's coupled limits.
 * - A press without a drag still selects; Ctrl/Cmd+Z puts back the pose before the last drag; the arrow keys
 *   nudge the selected part's joint (Left/Right the nearest, Up/Down the next one up; Shift: x5).
 */

import * as THREE from 'three';
import { actuators, servoAngle, solveServo, type Actuator } from '../mechrig/servos';
import { linkMatrices, type Pose } from './kinematics';
import {
  couplingAt, couplingRangeA, jointChain, moves, partDrive, pickChain, planeBasis, rayAngle, rayLineParam,
  rotaryValue, softClamp, solveIK, type ChainJoint, type ChainMode, type Coupling,
} from './drag';
import type { MGear, MJoint, MLinkage } from './manifest';
import { jointLabel } from './systems';
import type { AsmNode, BuildHost, Workbench } from './workbench';

/** What a press on a part would move. */
type Target =
  | { kind: 'chain'; chain: ChainJoint<AsmNode>[]; node: AsmNode; link: string | null; mode: ChainMode }
  | { kind: 'servo'; a: Actuator; lk: MLinkage }
  | { kind: 'gear'; gear: MGear; gearNode: AsmNode; node: AsmNode; joint: MJoint };

interface Rotary {
  kind: 'rotary';
  /** The frame the axis is stated in (live: it may move as the value does, a horn on the head). */
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
  /** The grabbed point, frame coordinates, at the start. */
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

type Grab = Rotary | Linear | IK;

const ACCENT = 0xe8762a;
const SOFT = 3; // deg or mm of give past a limit while dragging
const sgn = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}`;
const unitOf = (j: MJoint) => (j.unit === 'mm' ? ' mm' : '°');
const SERVO_NAME: Record<string, string> = {
  servo_l: 'Gimbal L servo', servo_r: 'Gimbal R servo', visor_servo: 'Visor servo', tilt_servo: 'Tilt servo',
  pan_servo: 'Pan servo', lift_servo: 'Lift servo', lower_servo: 'Lower ring servo', top_servo: 'Top ring servo',
  col_pan_servo: 'Pan servo', col_lift_servo: 'Lift servo', col_lower_servo: 'Lower ring servo', col_top_servo: 'Top ring servo',
};
const LOCK_CURSOR = `url("data:image/svg+xml;utf8,${encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24'><path d='M2 1v15l4-4 3 6 2-1-3-6h5z' fill='#fff' stroke='#000'/>"
  + "<path d='M15.5 15v-2a2.5 2.5 0 0 1 5 0v2' fill='none' stroke='#2a2c31' stroke-width='1.4'/><rect x='13.5' y='15' width='9' height='7' rx='1.2' fill='#8a8f99' stroke='#2a2c31'/></svg>",
)}") 2 1, default`;

export class DirectDrag {
  /** A drag is moving the model (the puppet and the camera stand down). */
  dragging = false;
  private down: { x: number; y: number; pointer: number; touch: boolean; hit: THREE.Intersection; target: Target | null; why: 'grounded' | 'exploded' | null; lastX: number; lastY: number } | null = null;
  private grab: Grab | null = null;
  private controlsWere = true;
  private justDragged = false;
  private tag: HTMLDivElement | null = null;
  private hintTimer = 0;
  private lastHint = 0;
  private readonly giz = new THREE.Group();
  private readonly ring: THREE.LineLoop;
  private readonly arc: THREE.Line;
  private readonly axisLine: THREE.Line;
  private readonly track: THREE.Line;
  private readonly dot: THREE.Points;
  private readonly lead: THREE.Line;
  private hoverAt = 0;
  private hoverTimer = 0;
  private lastNudge = 0;
  private settle: number | null = null;

  constructor(private readonly wb: Workbench, private readonly host: BuildHost) {
    const mat = (opacity: number) => new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity, depthTest: false, depthWrite: false });
    const circle = new THREE.BufferGeometry().setFromPoints(Array.from({ length: 72 }, (_, i) => new THREE.Vector3(Math.cos((i / 72) * 2 * Math.PI), Math.sin((i / 72) * 2 * Math.PI), 0)));
    this.ring = new THREE.LineLoop(circle, mat(0.35));
    this.arc = new THREE.Line(new THREE.BufferGeometry(), mat(0.95));
    this.axisLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, -0.7), new THREE.Vector3(0, 0, 0.7)]), mat(0.6));
    this.track = new THREE.Line(new THREE.BufferGeometry(), mat(0.55));
    this.dot = new THREE.Points(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3()]),
      new THREE.PointsMaterial({ color: ACCENT, size: 7, sizeAttenuation: false, depthTest: false, depthWrite: false, transparent: true }));
    this.lead = new THREE.Line(new THREE.BufferGeometry(), mat(0.6));
    for (const o of [this.ring, this.arc, this.axisLine, this.track, this.dot, this.lead]) {
      o.renderOrder = 60;
      o.frustumCulled = false;
      o.raycast = () => {};
      this.giz.add(o);
    }
    this.giz.name = 'build_drag_gizmo';
    this.giz.matrixAutoUpdate = false;
    this.giz.visible = false;
    host.scene.add(this.giz);

    const el = host.renderer.domElement;
    // capture: ahead of OrbitControls' own pointerdown on the canvas, so a press on a part never starts an orbit
    el.addEventListener('pointerdown', (e) => this.onDown(e), { capture: true });
    el.addEventListener('pointermove', (e) => this.onMove(e), { capture: true });
    el.addEventListener('pointerup', (e) => this.onUp(e), { capture: true });
    el.addEventListener('pointercancel', (e) => this.onUp(e), { capture: true });
    el.addEventListener('pointerleave', () => { if (!this.down) this.cursor(''); });
    addEventListener('keydown', (e) => this.onKey(e));
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

  // ------------------------------------------------------------------ what a part moves

  /** The press target of a mesh (null: grounded). */
  targetOf(mesh: THREE.Object3D, mode: ChainMode): Target | null {
    const info = this.wb.grabOf(mesh);
    if (!info) return null;
    const drive = partDrive(info.node.asm, info.id);
    if (drive?.kind === 'linkage') {
      const a = actuators([info.node]).find((x) => x.servo === drive.servo);
      const lk = info.node.asm.linkages?.find((l) => l.id === drive.linkage);
      if (a && lk) return { kind: 'servo', a, lk };
    }
    if (drive?.kind === 'gear') {
      const g = drive.gear;
      const node = g.joint_assembly ? this.wb.nodeOf(g.joint_assembly) : info.node;
      const joint = node?.asm.joints.find((j) => j.id === g.joint);
      if (node && joint && moves(joint)) return { kind: 'gear', gear: g, gearNode: info.node, node, joint };
    }
    const chain = pickChain(jointChain(info.node, info.link), mode);
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

  // ------------------------------------------------------------------ kinematics off the scene graph

  /** A link's world matrix with some poses overridden (the IK's trial poses), without touching the model. */
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

  /** Turn an actuator to `deg` (within its range), stopping short where the linkage or a joint limit says so. */
  private setServo(a: Actuator, deg: number): void {
    const d = Math.min(a.range[1], Math.max(a.range[0], deg));
    const ok = (x: number) => {
      const sol = solveServo(a, x);
      if (!sol) return null;
      for (const [id, v] of Object.entries(sol)) {
        const j = a.node.asm.joints.find((q) => q.id === id)!;
        const [lo, hi] = this.limitsOf(a.node, j, (n) => (n === a.node ? { ...n.pose, ...sol } : n.pose));
        if (v < lo - 1e-3 || v > hi + 1e-3) return null;
      }
      return sol;
    };
    let sol = ok(d);
    if (!sol) {
      let good = servoAngle(a) ?? 0;
      let bad = d;
      if (!ok(good)) return;
      for (let i = 0; i < 14; i++) {
        const mid = (good + bad) / 2;
        if (ok(mid)) good = mid;
        else bad = mid;
      }
      sol = ok(good);
    }
    if (sol) Object.assign(a.node.pose, sol);
  }

  // ------------------------------------------------------------------ a drag

  private ray(x: number, y: number): THREE.Ray {
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const rc = new THREE.Raycaster();
    rc.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), this.host.camera);
    return rc.ray;
  }

  private begin(t: Target, hit: THREE.Intersection): Grab | null {
    const point = hit.point.clone();
    const rotary = (o: Omit<Rotary, 'kind' | 'u' | 'raw' | 'radius' | 'local'>): Rotary => {
      const local = point.clone().applyMatrix4(o.frame().clone().invert());
      const off = local.clone().sub(o.centre);
      const n = o.axis.clone().normalize();
      off.addScaledVector(n, -off.dot(n));
      const radius = Math.max(off.length(), 4);
      const u = off.lengthSq() > 1e-9 ? off.normalize() : planeBasis(n).u;
      // the drag plane square to the axis through the grabbed point (not the pivot, which may sit far along the
      // axis - a ring's is at the floor): the same turn, and the cursor stays on the part
      const centre = o.centre.clone().addScaledVector(n, local.clone().sub(o.centre).dot(n));
      return { kind: 'rotary', ...o, centre, axis: n, u, raw: o.v0, radius, local };
    };
    if (t.kind === 'servo') {
      const { a, lk } = t;
      const link = a.node.links.get(lk.horn.link);
      if (!link) return null;
      const v0 = servoAngle(a) ?? 0;
      const others = () => a.joints.map((j) => `${jointLabel(j.name)} ${sgn(a.node.pose[j.id] ?? 0)}${unitOf(j)}`).join(' · ');
      return rotary({
        frame: () => link.matrixWorld, centre: new THREE.Vector3(...lk.horn.centre), axis: new THREE.Vector3(...lk.horn.axis), rate: 1, v0, soft: false,
        get: () => servoAngle(a) ?? v0,
        set: (v) => this.setServo(a, v),
        limits: () => a.range,
        label: () => `${SERVO_NAME[a.servo] ?? a.servo} ${sgn(servoAngle(a) ?? 0)}° → ${others()}`,
      });
    }
    if (t.kind === 'gear') {
      const { gear, gearNode, node, joint } = t;
      const link = gearNode.links.get(gear.link);
      if (!link) return null;
      const servoPer = gear.servo_deg_per_unit ?? joint.drive?.servo_deg_per_unit;
      return rotary({
        frame: () => link.matrixWorld, centre: new THREE.Vector3(...gear.pivot), axis: new THREE.Vector3(...gear.axis), rate: gear.deg_per_unit,
        v0: node.pose[joint.id] ?? 0, soft: true,
        get: () => node.pose[joint.id] ?? 0,
        set: (v) => { node.pose[joint.id] = v; },
        limits: () => this.limitsOf(node, joint),
        label: () => {
          const v = node.pose[joint.id] ?? 0;
          const servo = gear.servo && servoPer ? `${SERVO_NAME[gear.servo] ?? gear.servo} ${sgn(v * servoPer)}° → ` : '';
          return `${servo}${jointLabel(joint.name)} ${sgn(v)}${unitOf(joint)}`;
        },
      });
    }
    const { chain } = t;
    if (chain.length === 1) {
      const { node, joint: j } = chain[0];
      const link = node.links.get(j.parent_link);
      if (!link) return null;
      const get = () => node.pose[j.id] ?? 0;
      const set = (v: number) => { node.pose[j.id] = v; };
      const limits = () => this.limitsOf(node, j);
      const label = () => `${jointLabel(j.name)} ${sgn(get())}${unitOf(j)}`;
      if (j.type === 'prismatic') {
        const frame = () => link.matrixWorld;
        const origin = point.clone().applyMatrix4(frame().clone().invert());
        return { kind: 'linear', frame, origin, dir: new THREE.Vector3(...j.axis).normalize(), v0: get(), raw: get(), get, set, limits, label };
      }
      return rotary({ frame: () => link.matrixWorld, centre: new THREE.Vector3(...j.pivot), axis: new THREE.Vector3(...j.axis), rate: 1, v0: get(), soft: true, get, set, limits, label });
    }
    // more than one joint: the grabbed point follows the cursor in the plane facing the camera
    const lw = this.linkWorld(t.node, t.link, (n) => n.pose);
    const local = point.clone().applyMatrix4(lw.invert());
    const normal = this.host.camera.getWorldDirection(new THREE.Vector3());
    const k = t.mode === 'extend' ? 0.6 : 0.35;
    return {
      kind: 'ik', chain, node: t.node, link: t.link, local, plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, point),
      weights: chain.map((_, i) => k ** i), target: point.clone(),
      label: () => chain.map(({ node, joint }) => `${jointLabel(joint.name)} ${sgn(node.pose[joint.id] ?? 0)}${unitOf(joint)}`).join(' · '),
    };
  }

  /** Move the grab to the cursor. */
  private update(g: Grab, x: number, y: number, dx: number, dy: number) {
    const wb = this.wb;
    const ray = this.ray(x, y);
    if (g.kind === 'ik') {
      const target = ray.intersectPlane(g.plane, new THREE.Vector3());
      if (!target) return;
      g.target.copy(target);
      const ids = g.chain.map(({ node, joint }) => ({ node, id: joint.id }));
      const poseOf = (q: number[]) => {
        const over = new Map<AsmNode, Pose>();
        ids.forEach(({ node, id }, i) => over.set(node, { ...(over.get(node) ?? node.pose), [id]: q[i] }));
        return (n: AsmNode) => over.get(n) ?? n.pose;
      };
      const fk = (q: number[]) => g.local.clone().applyMatrix4(this.linkWorld(g.node, g.link, poseOf(q)));
      const q0 = ids.map(({ node, id }) => node.pose[id] ?? 0);
      const q = solveIK(fk, q0, g.chain.map(({ joint }, i) => ({ min: joint.limits.min, max: joint.limits.max, weight: g.weights[i] })), target, {
        normal: g.plane.normal, iters: 10,
        limits: (i, qq) => this.limitsOf(g.chain[i].node, g.chain[i].joint, poseOf(qq)),
      });
      ids.forEach(({ node, id }, i) => (node.pose[id] = q[i]));
      wb.applyPose(false);
      return;
    }
    if (g.kind === 'linear') {
      const lr = ray.clone().applyMatrix4(g.frame().clone().invert());
      const s = rayLineParam(lr, g.origin, g.dir);
      if (s !== null && Math.abs(lr.direction.dot(g.dir)) < 0.97) g.raw = g.v0 + s;
      else g.raw += this.screenStep(g.origin.clone().addScaledVector(g.dir, g.get() - g.v0), g.dir, g.frame(), dx, dy);
      const [lo, hi] = g.limits();
      g.set(softClamp(g.raw, lo, hi, Math.min(SOFT, 0.06 * (hi - lo))));
      wb.applyPose(false);
      return;
    }
    // rotary: the frame may move with the value (a horn on the head it tilts), so settle in a few passes
    for (let pass = 0; pass < 3; pass++) {
      const F = g.frame().clone();
      const lr = ray.clone().applyMatrix4(F.clone().invert());
      // a plane seen nearly edge on turns a few pixels into a large angle: drag along the screen path instead
      const c = rayAngle(lr, g.centre, g.axis, g.u, 0.3);
      const before = g.get();
      if (c === null) {
        // the plane edge on: the drag along the grabbed point's path on screen
        const at = g.local.clone().sub(g.centre).applyAxisAngle(g.axis, (g.rate * (before - g.v0) * Math.PI) / 180).add(g.centre);
        const tangent = g.axis.clone().cross(at.clone().sub(g.centre)).multiplyScalar((g.rate * Math.PI) / 180);
        if (pass === 0) g.raw += this.screenStep(at, tangent, F, dx, dy);
      } else {
        g.raw = rotaryValue(g.raw, g.v0, 0, c, g.rate); // the grab sits at angle 0 (u points at it)
      }
      const [lo, hi] = g.limits();
      g.set(g.soft ? softClamp(g.raw, lo, hi, Math.min(SOFT, 0.06 * (hi - lo))) : g.raw);
      wb.applyPose(false);
      if (c === null || Math.abs(g.get() - before) < 0.02) break;
    }
  }

  /** How far a screen drag (dx, dy px) moves a point at `p` (frame coords) whose motion per unit is `dp`. */
  private screenStep(p: THREE.Vector3, dp: THREE.Vector3, F: THREE.Matrix4, dx: number, dy: number): number {
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const scr = (v: THREE.Vector3) => {
      const s = v.clone().applyMatrix4(F).project(this.host.camera);
      return new THREE.Vector2(((s.x + 1) / 2) * r.width, ((1 - s.y) / 2) * r.height);
    };
    const a = scr(p);
    const b = scr(p.clone().addScaledVector(dp, 0.01)).sub(a).divideScalar(0.01);
    const l2 = b.lengthSq();
    return l2 < 1e-6 ? 0 : (dx * b.x + dy * b.y) / l2;
  }

  /** Release: a value left past a limit settles back onto it. */
  private finish(g: Grab) {
    if (g.kind === 'ik') return;
    const [lo, hi] = g.limits();
    const v = g.get();
    const to = Math.min(hi, Math.max(lo, v));
    if (Math.abs(to - v) < 1e-4) return;
    const t0 = performance.now();
    const step = () => {
      const k = Math.min(1, (performance.now() - t0) / 160);
      g.set(v + (to - v) * (1 - (1 - k) ** 3));
      this.wb.applyPose(true);
      this.settle = k < 1 ? requestAnimationFrame(step) : null;
    };
    if (this.settle !== null) cancelAnimationFrame(this.settle);
    this.settle = requestAnimationFrame(step);
  }

  // ------------------------------------------------------------------ gizmo and label

  private drawGizmo(g: Grab) {
    const show = (...on: THREE.Object3D[]) => {
      for (const o of [this.ring, this.arc, this.axisLine, this.track, this.dot, this.lead]) o.visible = on.includes(o);
    };
    const m = this.giz.matrix;
    if (g.kind === 'rotary') {
      const { u } = g;
      const w = new THREE.Vector3().crossVectors(g.axis, u);
      m.copy(g.frame())
        .multiply(new THREE.Matrix4().makeTranslation(g.centre.x, g.centre.y, g.centre.z))
        .multiply(new THREE.Matrix4().makeBasis(u, w, g.axis))
        .multiply(new THREE.Matrix4().makeScale(g.radius, g.radius, g.radius));
      const sweep = (g.rate * (g.get() - g.v0) * Math.PI) / 180;
      const n = Math.max(2, Math.min(96, Math.ceil(Math.abs(sweep) / 0.05)));
      this.arc.geometry.setFromPoints(Array.from({ length: n + 1 }, (_, i) => {
        const t = (sweep * i) / n;
        return new THREE.Vector3(Math.cos(t), Math.sin(t), 0);
      }));
      (this.dot.geometry.attributes.position as THREE.BufferAttribute).setXYZ(0, Math.cos(sweep), Math.sin(sweep), 0);
      this.dot.geometry.attributes.position.needsUpdate = true;
      show(this.ring, this.arc, this.axisLine, this.dot);
    } else if (g.kind === 'linear') {
      const [lo, hi] = g.limits();
      m.copy(g.frame());
      const at = (v: number) => g.origin.clone().addScaledVector(g.dir, v - g.v0);
      this.track.geometry.setFromPoints([at(lo), at(hi)]);
      (this.dot.geometry.attributes.position as THREE.BufferAttribute).setXYZ(0, ...at(g.get()).toArray());
      this.dot.geometry.attributes.position.needsUpdate = true;
      show(this.track, this.dot);
    } else {
      m.identity();
      const p = g.local.clone().applyMatrix4(this.linkWorld(g.node, g.link, (n) => n.pose));
      this.lead.geometry.setFromPoints([p, g.target]);
      (this.dot.geometry.attributes.position as THREE.BufferAttribute).setXYZ(0, p.x, p.y, p.z);
      this.dot.geometry.attributes.position.needsUpdate = true;
      show(this.dot, this.lead);
    }
    this.dot.geometry.computeBoundingSphere();
    this.giz.matrixWorldNeedsUpdate = true;
    this.giz.visible = true;
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
    this.tag.textContent = text;
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

  /** The cursor over what the pointer is on: grab on a part that moves, a lock on a grounded one. */
  private hover(x: number, y: number) {
    if (!this.usable()) {
      this.cursor('');
      return;
    }
    const hit = this.wb.hitAt(x, y);
    if (!hit) return this.cursor('');
    this.cursor(this.targetOf(hit.object, 'default') ? (this.wb.explode > 1e-3 ? '' : 'grab') : LOCK_CURSOR);
  }

  // ------------------------------------------------------------------ pointer

  private onDown(e: PointerEvent) {
    if (this.down || this.dragging) {
      // a second finger: let go (the pinch is the camera's)
      this.end(e);
      return;
    }
    if (!this.usable() || e.button !== 0) return;
    const hit = this.wb.hitAt(e.clientX, e.clientY);
    if (!hit) return;
    const mode: ChainMode = e.altKey ? 'nearest' : e.shiftKey ? 'extend' : 'default';
    const target = this.targetOf(hit.object, mode);
    const why = !target ? 'grounded' : this.wb.explode > 1e-3 ? 'exploded' : null;
    this.down = { x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY, pointer: e.pointerId, touch: e.pointerType === 'touch', hit, target, why };
    if (!why) {
      // a part that moves: the press is ours, not the camera's
      this.controlsWere = this.host.controls.enabled;
      this.host.controls.enabled = false;
    }
  }

  private onMove(e: PointerEvent) {
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
    const dist = Math.hypot(e.clientX - d.x, e.clientY - d.y);
    if (!this.dragging) {
      if (dist <= (d.touch ? 8 : 4)) return;
      if (d.why || !d.target) {
        if (d.why === 'grounded') this.hint('Grounded: it does not move', e.clientX, e.clientY);
        else if (d.why === 'exploded') this.hint('Collapse the explode to drag parts', e.clientX, e.clientY);
        this.down = null;
        return;
      }
      const g = this.begin(d.target, d.hit);
      if (!g) {
        this.release();
        return;
      }
      this.wb.stopMotion();
      this.wb.pushUndo();
      this.grab = g;
      this.dragging = true;
      try { this.host.renderer.domElement.setPointerCapture(e.pointerId); } catch { /* the pointer is gone */ }
      this.cursor('grabbing');
    }
    const g = this.grab!;
    this.update(g, e.clientX, e.clientY, e.clientX - d.lastX, e.clientY - d.lastY);
    d.lastX = e.clientX;
    d.lastY = e.clientY;
    this.wb.applyPose(true);
    this.drawGizmo(g);
    this.say(g.label(), e.clientX, e.clientY);
    e.preventDefault();
  }

  private onUp(e: PointerEvent) {
    if (!this.down || e.pointerId !== this.down.pointer) return;
    this.end(e);
  }

  private end(e: PointerEvent) {
    if (this.dragging && this.grab) {
      this.finish(this.grab);
      this.justDragged = true;
      try { this.host.renderer.domElement.releasePointerCapture(e.pointerId); } catch { /* released */ }
      this.wb.applyPose(true);
    }
    this.release();
    this.cursor(this.usable() ? 'grab' : '');
  }

  private release() {
    if (this.down && !this.down.why) this.host.controls.enabled = this.controlsWere;
    this.down = null;
    this.grab = null;
    this.dragging = false;
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
    const dir = { ArrowRight: 1, ArrowLeft: -1, ArrowUp: 1, ArrowDown: -1 }[e.key];
    if (!dir || !this.wb.selected) return;
    // only with the model's focus (the panel's lists use the arrows themselves)
    if (t && t !== document.body && t !== this.host.renderer.domElement && t !== document.documentElement) return;
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
      this.setServo(target.a, (servoAngle(target.a) ?? 0) + 2 * k * dir);
      text = `${SERVO_NAME[target.a.servo] ?? target.a.servo} ${sgn(servoAngle(target.a) ?? 0)}°`;
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
