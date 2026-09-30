/**
 * Centres: where each joint's zero is, and where it is now (Bench/Studio viewport overlay).
 *
 * Per revolute joint, at its pivot and in its parent's frame (so it turns with the parent,
 * not with the joint): a thin arc over the animation range, subtle ticks at the soft
 * (blue) and hard (red) limits, a white tick at 0 (centre/home), an amber needle at the
 * current angle and a small label in degrees. `head_lift` gets a linear scale in mm, and
 * the body a floor ring with a "front" marker under the base and the centre line: the
 * canonical body frame's forward (+Z, the base's front) and vertical axis.
 *
 * Drawn after the post pipeline into its own scene (no depth test, no bloom, no tone
 * mapping), with a dark halo under every stroke, so it reads on every backdrop and never
 * adds glow to the LEDs. The math at the top is pure and tested (test/centres.test.ts).
 */
import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import type { Rig, Joint as RigJoint } from './rig';
import type { Joint as ProfileJoint } from './generated/Joint';

// ------------------------------------------------------------------ pure math

export type V3 = [number, number, number];

const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const scale = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const norm = (a: V3): V3 => scale(a, 1 / (Math.hypot(...a) || 1));

/** `hint` with its component along `axis` removed, normalised; null when nearly parallel. */
export function perpendicular(axis: V3, hint: V3): V3 | null {
  const n = norm(axis);
  const p = add(hint, scale(n, -dot(hint, n)));
  const len = Math.hypot(...p);
  return len < 1e-3 * (Math.hypot(...hint) || 1) ? null : scale(p, 1 / len);
}

/** The 0-degree direction: the first usable hint, else forward (+Z), else up (+Y). */
export function zeroDirection(axis: V3, hints: V3[]): V3 {
  for (const h of [...hints, [0, 0, 1] as V3, [0, 1, 0] as V3]) {
    const p = perpendicular(axis, h);
    if (p) return p;
  }
  return [1, 0, 0];
}

/** `zero` turned by `deg` about `axis` (right-handed, as `Quaternion.setFromAxisAngle`). */
export function onCircle(axis: V3, zero: V3, deg: number, r = 1): V3 {
  const a = (deg * Math.PI) / 180;
  const n = norm(axis);
  return add(scale(zero, Math.cos(a) * r), scale(cross(n, zero), Math.sin(a) * r));
}

/** Points along the arc from `from` to `to` degrees, at most `step` apart. */
export function arcPoints(axis: V3, zero: V3, from: number, to: number, r: number, step = 4): V3[] {
  const n = Math.max(1, Math.ceil(Math.abs(to - from) / step));
  return Array.from({ length: n + 1 }, (_, i) => onCircle(axis, zero, from + ((to - from) * i) / n, r));
}

/** `+12°`, `-3.4°`, `0°`, `+4.0 mm`: one decimal near centre, whole degrees further out. */
export function formatValue(v: number, unit: 'deg' | 'mm'): string {
  const a = Math.abs(v);
  const s = unit === 'mm' ? a.toFixed(1) : a < 9.95 ? a.toFixed(1) : a.toFixed(0);
  const sign = Number(s) === 0 ? '' : v > 0 ? '+' : '-';
  return unit === 'mm' ? `${sign}${s} mm` : `${sign}${s}°`;
}

/** Every joint within `tol` of its home value (missing values count as not home). */
export function atHome(values: Record<string, number>, home: Record<string, number>, joints: string[], tol: number): boolean {
  return joints.every((j) => j in values && Math.abs(values[j] - (home[j] ?? 0)) <= tol);
}

/** `head_pan` -> `pan`, `hero_claw_l` -> `claw_l`: the label under the gizmo. */
export const shortName = (j: string) => j.replace(/^(head|torso|hero|throttle|poker)_/, '');

// ------------------------------------------------------------------ placement

/**
 * Where a gizmo sits, in the joint's parent frame, added to the joint node's position.
 * Joints whose pivot is the body axis at the floor (the rings, the neck) are lifted to where
 * they are visible; the head's pitch joints sit beside the head, clear of the face LEDs.
 */
interface Place {
  offset?: V3;
  r?: number;
  /** Label beside the needle tip (default) or at this offset from the origin. */
  label?: V3;
}
const PLACE: Record<string, Place> = {
  torso_lower: { offset: [0, 0.004, 0], r: 0.27 },
  torso_middle: { offset: [0, 0.45, 0], r: 0.215 },
  torso_top: { offset: [0, 0.55, 0], r: 0.2 },
  head_lift: { offset: [0.07, 0.6, 0] },
  head_pan: { offset: [0, 0.625, 0], r: 0.075 },
  // Outside the headphones (+/-0.18 m), so nothing is drawn over the face.
  head_tilt: { offset: [-0.23, 0, 0], r: 0.045 },
  visor: { offset: [0.23, 0, 0], r: 0.045 },
  // Above the dome, facing forward: reads as a level over the head.
  head_roll: { offset: [0, 0.2, 0], r: 0.045 },
};
/** Small parts whose label shows only on hover (the arcs and needles always show). */
const QUIET = /claw/;
const DEFAULT_R = 0.032;
/** Visual mm -> metres for the head-lift scale (2x, so +/-19 mm reads). */
const MM = 0.002;

const COLOR = {
  arc: 0x8fd0ff,
  soft: 0x8fd0ff,
  hard: 0xff8a6a,
  zero: 0xffffff,
  needle: 0xffc94d,
  hot: 0x5cf0ff,
  halo: 0x05070a,
};

// ------------------------------------------------------------------ rendering

type Stroke = { line: LineSegments2; halo: LineSegments2; base: { color: number; width: number; opacity: number } };

interface Gizmo {
  name: string;
  joint: RigJoint;
  spec: ProfileJoint;
  prismatic: boolean;
  root: THREE.Group;
  origin: THREE.Vector3;
  axis: THREE.Vector3;
  moving: THREE.Group;
  strokes: Stroke[];
  label: HTMLElement;
  labelAt: THREE.Vector3;
}

export class Centres {
  readonly scene = new THREE.Scene();
  private gizmos = new Map<string, Gizmo>();
  private materials: LineMaterial[] = [];
  private visible = false;
  private hot = new Set<string>();
  private readonly labels: HTMLElement;
  private readonly tmp = new THREE.Vector3();
  private readonly m = new THREE.Matrix4();

  constructor(rig: Rig, joints: ProfileJoint[], overlay: HTMLElement) {
    this.labels = overlay;
    this.scene.matrixWorldAutoUpdate = true;
    rig.root.updateMatrixWorld(true);
    for (const spec of joints) {
      const joint = rig.joints.get(spec.name);
      if (joint) this.build(spec, joint, rig);
    }
    this.setVisible(false);
  }

  private stroke(pairs: V3[], color: number, width: number, opacity: number, parent: THREE.Object3D): Stroke {
    const geo = new LineSegmentsGeometry().setPositions(pairs.flat());
    const mk = (c: number, w: number, o: number, order: number) => {
      const mat = new LineMaterial({ color: c, linewidth: w, transparent: true, opacity: o, depthTest: false, depthWrite: false, worldUnits: false });
      mat.toneMapped = false;
      this.materials.push(mat);
      const l = new LineSegments2(geo, mat);
      l.renderOrder = order;
      l.frustumCulled = false;
      parent.add(l);
      return l;
    };
    const halo = mk(COLOR.halo, width + 2.5, Math.min(0.6, opacity * 0.8), 1);
    const line = mk(color, width, opacity, 2);
    return { line, halo, base: { color, width, opacity } };
  }

  /** Consecutive points -> segment pairs. */
  private static path(pts: V3[]): V3[] {
    const out: V3[] = [];
    for (let i = 1; i < pts.length; i++) out.push(pts[i - 1], pts[i]);
    return out;
  }

  private build(spec: ProfileJoint, joint: RigJoint, rig: Rig) {
    const node = joint.node;
    const place = PLACE[spec.name] ?? {};
    const axis: V3 = [joint.axis.x, joint.axis.y, joint.axis.z];
    const origin = node.position.clone().add(new THREE.Vector3(...(place.offset ?? [0, 0, 0])));
    const root = new THREE.Group();
    root.matrixAutoUpdate = false;
    this.scene.add(root);
    const moving = new THREE.Group();
    root.add(moving);
    const strokes: Stroke[] = [];
    const prismatic = spec.kind === 'prismatic';
    const label = document.createElement('div');
    label.className = 'centre-label';
    this.labels.appendChild(label);
    let labelAt: THREE.Vector3;

    if (prismatic) {
      // A vertical scale: the animation range, soft/hard ends, 0, and a sliding marker.
      const at = (mm: number): V3 => scale(axis, mm * MM);
      const tick = (mm: number, w: number): V3[] => [add(at(mm), [-w, 0, 0]), add(at(mm), [w, 0, 0])];
      strokes.push(this.stroke([at(spec.animation.min), at(spec.animation.max)], COLOR.arc, 1.5, 0.75, root));
      strokes.push(this.stroke([...tick(spec.soft.min, 0.006), ...tick(spec.soft.max, 0.006)], COLOR.soft, 1.2, 0.5, root));
      strokes.push(this.stroke([...tick(spec.hard.min, 0.006), ...tick(spec.hard.max, 0.006)], COLOR.hard, 1.2, 0.5, root));
      strokes.push(this.stroke(tick(0, 0.012), COLOR.zero, 1.6, 0.95, root));
      strokes.push(this.stroke([[-0.016, 0, 0], [0.004, 0, 0]], COLOR.needle, 2.2, 1, moving));
      labelAt = new THREE.Vector3(0.022, 0, 0);
    } else {
      const r = place.r ?? DEFAULT_R;
      // 0 deg: the body frame's forward for the vertical axes, else along the joint's own
      // part (its meshes' centre). Measured on the rest pose the Rig starts in.
      const hints: V3[] = [];
      if (Math.abs(axis[1]) >= 0.9) {
        const q = node.parent!.getWorldQuaternion(new THREE.Quaternion()).invert();
        const f = new THREE.Vector3(0, 0, 1).applyQuaternion(q);
        hints.push([f.x, f.y, f.z]);
      } else {
        const c = ownCentre(node, rig);
        if (c) hints.push([c.x - node.position.x, c.y - node.position.y, c.z - node.position.z]);
      }
      const zero = zeroDirection(axis, hints);
      const a = spec.animation;
      strokes.push(this.stroke(Centres.path(arcPoints(axis, zero, a.min, a.max, r)), COLOR.arc, 1.4, 0.7, root));
      const radial = (deg: number, r0: number, r1: number): V3[] => [onCircle(axis, zero, deg, r0), onCircle(axis, zero, deg, r1)];
      strokes.push(this.stroke([...radial(spec.soft.min, r * 0.9, r * 1.1), ...radial(spec.soft.max, r * 0.9, r * 1.1)], COLOR.soft, 1.2, 0.45, root));
      strokes.push(this.stroke([...radial(spec.hard.min, r * 0.92, r * 1.14), ...radial(spec.hard.max, r * 0.92, r * 1.14)], COLOR.hard, 1.2, 0.45, root));
      // Body rings: tick and needle on the rim (from the centre they would cross the body).
      const rim = r >= 0.07;
      strokes.push(this.stroke(radial(0, rim ? r * 0.86 : r * 0.78, r * 1.22), COLOR.zero, 1.8, 0.95, root));
      if (spec.name === 'torso_lower') {
        // The floor ring: a dim full circle, and a "front" chevron outside it at 0 deg.
        strokes.push(this.stroke(Centres.path(arcPoints(axis, zero, -180, 180, r * 1.06, 6)), COLOR.arc, 1, 0.3, root));
        // The centre line: forward/back across the floor and the vertical axis up to the head.
        strokes.push(this.stroke([onCircle(axis, zero, 180, r * 1.32), onCircle(axis, zero, 0, r * 1.2), [0, 0, 0], scale(norm(axis), 0.98)], COLOR.zero, 1, 0.35, root));
        const tip = onCircle(axis, zero, 0, r * 1.32);
        strokes.push(this.stroke([onCircle(axis, zero, -4, r * 1.2), tip, tip, onCircle(axis, zero, 4, r * 1.2)], COLOR.zero, 1.8, 0.9, root));
        const front = document.createElement('div');
        front.className = 'centre-label front';
        front.textContent = 'front';
        this.labels.appendChild(front);
        this.fronts.push({ el: front, root, at: new THREE.Vector3(...onCircle(axis, zero, 0, r * 1.45)) });
      }
      // The needle is drawn at 0 inside `moving`, which turns by the joint's value.
      strokes.push(this.stroke(radial(0, rim ? r * 0.9 : 0, r * 1.14), COLOR.needle, 2.4, 1, moving));
      labelAt = new THREE.Vector3(...onCircle(axis, zero, 0, rim ? r * 1.25 : r * 1.45));
    }
    this.gizmos.set(spec.name, {
      name: spec.name, joint, spec, prismatic, root, origin,
      axis: new THREE.Vector3(...axis).normalize(), moving, strokes, label, labelAt,
    });
  }

  private fronts: { el: HTMLElement; root: THREE.Group; at: THREE.Vector3 }[] = [];

  get shown() {
    return this.visible;
  }

  setVisible(on: boolean) {
    this.visible = on;
    this.scene.visible = on;
    this.labels.hidden = !on;
  }

  /** Emphasise these joints' gizmos (hover); null or empty clears. */
  highlight(joints: string[] | null) {
    const next = new Set(joints ?? []);
    if (next.size === this.hot.size && [...next].every((j) => this.hot.has(j))) return;
    this.hot = next;
    for (const g of this.gizmos.values()) {
      const on = this.hot.has(g.name);
      const dim = this.hot.size > 0 && !on;
      for (const s of g.strokes) {
        const m = s.line.material as LineMaterial;
        m.color.setHex(on && s.base.color !== COLOR.needle && s.base.color !== COLOR.zero ? COLOR.hot : s.base.color);
        m.linewidth = s.base.width + (on ? 1 : 0);
        m.opacity = dim ? s.base.opacity * 0.35 : s.base.opacity;
        (s.halo.material as LineMaterial).opacity = dim ? 0.15 : Math.min(0.6, s.base.opacity * 0.8);
      }
      g.label.classList.toggle('hot', on);
      g.label.classList.toggle('dim', dim);
      g.label.textContent = ''; // relabelled on the next update (full name when hot)
    }
  }

  /** Follow the rig (call after `rig.apply`) and draw on top of what is already on screen. */
  render(renderer: THREE.WebGLRenderer, camera: THREE.Camera, values: Record<string, number>) {
    if (!this.visible) return;
    const size = renderer.getSize(new THREE.Vector2());
    for (const m of this.materials) m.resolution.copy(size);
    const w = window.innerWidth;
    const h = window.innerHeight;
    const place = (el: HTMLElement, world: THREE.Vector3) => {
      this.tmp.copy(world).project(camera);
      const off = this.tmp.z > 1 || Math.abs(this.tmp.x) > 1.2 || Math.abs(this.tmp.y) > 1.2;
      el.style.visibility = off ? 'hidden' : '';
      el.style.transform = `translate(${((this.tmp.x + 1) / 2) * w}px, ${((1 - this.tmp.y) / 2) * h}px)`;
    };
    for (const g of this.gizmos.values()) {
      const parent = g.joint.node.parent!;
      g.root.matrix.copy(parent.matrixWorld).multiply(this.m.makeTranslation(g.origin.x, g.origin.y, g.origin.z));
      g.root.matrixWorldNeedsUpdate = true;
      const v = values[g.name] ?? 0;
      if (g.prismatic) g.moving.position.copy(g.axis).multiplyScalar(v * MM);
      else g.moving.quaternion.setFromAxisAngle(g.axis, THREE.MathUtils.degToRad(v));
      const hot = this.hot.has(g.name);
      const text = `${hot ? g.name : shortName(g.name)} ${formatValue(v, g.prismatic ? 'mm' : 'deg')}`;
      if (g.label.textContent !== text) g.label.textContent = text;
      g.label.hidden = QUIET.test(g.name) && !hot;
      g.root.updateMatrixWorld(true);
      place(g.label, this.tmp.copy(g.labelAt).applyMatrix4(g.moving.matrixWorld));
    }
    for (const f of this.fronts) place(f.el, this.tmp.copy(f.at).applyMatrix4(f.root.matrixWorld));
    const auto = renderer.autoClear;
    renderer.autoClear = false;
    renderer.render(this.scene, camera);
    renderer.autoClear = auto;
  }
}

/** Centre of the meshes that belong to `node` itself (not to child joints), in its parent's frame. */
function ownCentre(node: THREE.Object3D, rig: Rig): THREE.Vector3 | null {
  const box = new THREE.Box3();
  const child = new Set([...rig.joints.values()].map((j) => j.node));
  const walk = (o: THREE.Object3D) => {
    for (const c of o.children) {
      if (child.has(c)) continue;
      if ((c as THREE.Mesh).isMesh) box.expandByObject(c);
      walk(c);
    }
  };
  walk(node);
  if (box.isEmpty()) return null;
  const c = box.getCenter(new THREE.Vector3());
  return node.parent ? node.parent.worldToLocal(c) : c;
}
