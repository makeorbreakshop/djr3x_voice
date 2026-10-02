/**
 * Build's camera input, one mapping for Build, Library, a focus and Instructions (CAD conventions, Mac and
 * Windows, mouse, trackpad and touch). OrbitControls stays the camera's holder outside Build; in Build its
 * own input is off (Workbench.setActive) and this module moves the camera and the target.
 *
 *   mouse      left drag orbit (about the point under the cursor) · right / middle / Shift+left drag pan ·
 *              wheel zoom toward the cursor · double-click a part: frame it (empty: everything)
 *   trackpad   two-finger scroll pan · pinch zoom toward the cursor · click-drag orbit · Shift+scroll orbit
 *   touch      one finger orbit · two fingers pan and pinch · long-press a part, then drag: move it
 *   parts      ⌘ (Mac) / Ctrl (Windows) + drag moves a part · + Shift: all its joints · M: Move mode
 *   keys       F frame · H / Home home view · arrows nudge the selected joint · Esc back · ? this card
 *
 * A wheel event is a mouse wheel or a trackpad by its shape (classifyWheel), or by the user's setting.
 */

import * as THREE from 'three';
import { ControlsCard } from './controlscard';
import type { BuildHost, Workbench } from './workbench';

/** Wheel events closer than this belong to one gesture (one zoom-point raycast: `wheelPoint`). */
export const WHEEL_GAP_MS = 250;
/** The most one pinch event zooms (log factor: about 16%). */
export const PINCH_STEP = 0.15;

// ------------------------------------------------------------------ pure mapping (tested)

export type Platform = 'mac' | 'other';
export type ScrollDevice = 'auto' | 'trackpad' | 'mouse';
export type WheelAction = 'zoom' | 'pinch' | 'pan' | 'orbit';

export const platformOf = (s: string): Platform => (/Mac|iPhone|iPad|iPod/i.test(s) ? 'mac' : 'other');
/** This browser's platform (the tests pass theirs: Node has a navigator too). */
const PLATFORM: Platform = platformOf(typeof navigator === 'undefined' ? '' : navigator.platform || navigator.userAgent);

/** The move-a-part key: ⌘ on a Mac, Ctrl elsewhere (Ctrl-click on a Mac is the context click). */
export function partKey(e: { metaKey: boolean; ctrlKey: boolean }, p: Platform): boolean {
  return p === 'mac' ? e.metaKey : e.ctrlKey;
}

export interface WheelLike { deltaX: number; deltaY: number; deltaMode: number; ctrlKey: boolean; shiftKey: boolean }

/**
 * Mouse wheel or trackpad, from one event's shape. A pinch (and Ctrl+wheel) arrives with ctrlKey. Line or page
 * units, or a large whole-number step on Y alone, is a wheel notch; sideways motion or a small step is a
 * trackpad's two fingers. (Shift+wheel on a mouse reads as sideways: Chrome swaps the axes.)
 *
 * A fractional delta depends on the platform. On a Mac it is an ordinary mouse wheel: macOS turns a notch into
 * accelerated pixels (4.000244 for a slow one, then 13.6, 31.2 ...), never whole numbers and never sideways,
 * while a trackpad's two fingers arrive as whole pixels. Read as a trackpad (as it first was), a slow turn of
 * the wheel panned the view and a quick one zoomed it: the same wheel doing two things. Elsewhere a fractional
 * step is a precision touchpad (or a wheel on a scaled display, which is still a large step).
 */
export function wheelDevice(e: WheelLike, p: Platform = PLATFORM): 'trackpad' | 'mouse' {
  if (e.deltaMode !== 0) return 'mouse';
  if (e.deltaX !== 0 && !e.shiftKey) return 'trackpad';
  const v = e.shiftKey ? e.deltaX || e.deltaY : e.deltaY;
  if (!Number.isInteger(v)) return p === 'mac' || Math.abs(v) >= 50 ? 'mouse' : 'trackpad';
  return Math.abs(v) >= 50 ? 'mouse' : 'trackpad';
}

/**
 * What a wheel event does. A gesture's events (each within `gapMs` of the last) keep the device its first one
 * was read as, so one odd delta in the middle of a two-finger scroll never turns a pan into a zoom.
 */
export class WheelClassifier {
  private last = -Infinity;
  private device: 'trackpad' | 'mouse' = 'mouse';
  constructor(public setting: ScrollDevice = 'auto', private readonly gapMs = 220, private readonly platform: Platform = PLATFORM) {}

  classify(e: WheelLike, now: number): WheelAction {
    if (e.ctrlKey) {
      this.last = now;
      return 'pinch';
    }
    let dev: 'trackpad' | 'mouse';
    if (this.setting !== 'auto') dev = this.setting;
    else if (now - this.last < this.gapMs) dev = this.device;
    else dev = wheelDevice(e, this.platform);
    // a trackpad's signature inside a gesture read as a wheel corrects it: sideways motion anywhere, a
    // fractional step off the Mac (there it is the wheel's own, see wheelDevice)
    if (this.setting === 'auto' && dev === 'mouse' && wheelDevice(e, this.platform) === 'trackpad'
      && (e.deltaX !== 0 || !Number.isInteger(e.deltaY))) dev = 'trackpad';
    this.device = dev;
    this.last = now;
    if (dev === 'mouse') return 'zoom';
    return e.shiftKey ? 'orbit' : 'pan';
  }
}

/** Zoom per wheel pixel (the scale of each action). A notch (~100 px) is ~18 %; a pinch is finer and quicker. */
export function zoomFactor(action: 'zoom' | 'pinch', deltaY: number, deltaMode = 0): number {
  const px = deltaMode === 1 ? deltaY * 33 : deltaMode === 2 ? deltaY * 400 : deltaY;
  return Math.exp(-px * (action === 'pinch' ? 0.01 : 0.002));
}

export type DragAction = 'orbit' | 'pan' | 'move' | 'none';

/** What a pointer drag on empty space or a part does (a handle, and a ⌘-drag on a part, belong to direct.ts). */
export function dragAction(e: { button: number; shiftKey: boolean; metaKey: boolean; ctrlKey: boolean; pointerType?: string }, p: Platform, moveMode = false): DragAction {
  if (e.button === 1 || e.button === 2) return 'pan';
  if (e.button !== 0) return 'none';
  if (partKey(e, p) || moveMode) return 'move';
  return e.shiftKey ? 'pan' : 'orbit';
}

/**
 * The orbit's centre: the point under the cursor at the drag's start. On empty space, the camera's target while it
 * is on the model (inside its bounds, padded 10%), else the model's centre (CAD: Onshape, Fusion), since a pan or a
 * zoom toward the cursor slides the target sideways, out into the empty space around the model.
 */
export function pivotFor(hit: THREE.Vector3 | null | undefined, target: THREE.Vector3, model?: THREE.Box3): THREE.Vector3 {
  if (hit) return hit.clone();
  if (!model || model.isEmpty()) return target.clone();
  const pad = model.getSize(new THREE.Vector3()).multiplyScalar(0.1);
  const near = model.clone().expandByVector(pad).containsPoint(target);
  return near ? target.clone() : model.getCenter(new THREE.Vector3());
}

/**
 * Turn the camera rig about a pivot: yaw about the world vertical, pitch about the camera's right axis. The
 * camera and its target turn together, so the pivot stays where it is on screen; the view never flips over
 * the pole (pitch stops within ~1 degree of straight up or down).
 */
export function orbitAbout(pos: THREE.Vector3, target: THREE.Vector3, pivot: THREE.Vector3, yaw: number, pitch: number): { pos: THREE.Vector3; target: THREE.Vector3 } {
  const up = new THREE.Vector3(0, 1, 0);
  const fwd = target.clone().sub(pos).normalize();
  const right = fwd.clone().cross(up);
  if (right.lengthSq() < 1e-12) right.set(1, 0, 0);
  right.normalize();
  // pitch > 0 raises the camera (looks more from above); stop ~1 degree short of either pole
  const polar = Math.acos(THREE.MathUtils.clamp(fwd.dot(up), -1, 1)); // 0 looking straight up, PI straight down
  const lim = 0.02;
  const p = THREE.MathUtils.clamp(pitch, lim - polar, Math.PI - lim - polar);
  const q = new THREE.Quaternion().setFromAxisAngle(up, yaw).multiply(new THREE.Quaternion().setFromAxisAngle(right, -p));
  const turn = (v: THREE.Vector3) => v.clone().sub(pivot).applyQuaternion(q).add(pivot);
  return { pos: turn(pos), target: turn(target) };
}

/**
 * Dolly toward a point by `factor` (> 1 in, < 1 out): the camera moves along its line to `point`, so the point
 * under the cursor stays under it. The target slides sideways with it (the view's direction is unchanged) but
 * not forward - the camera closes on it - until it would come nearer than `minTarget`: then it is carried
 * forward, so a zoom never runs through it. The camera stays between `min` and `max` from the point.
 */
export function dollyToward(pos: THREE.Vector3, target: THREE.Vector3, point: THREE.Vector3, factor: number, min: number, max: number, minTarget = 0.08): { pos: THREE.Vector3; target: THREE.Vector3 } {
  const d = pos.distanceTo(point);
  const want = THREE.MathUtils.clamp(d / factor, Math.min(min, d), Math.max(max, d));
  const step = point.clone().sub(pos).setLength(d - want); // toward the point (positive in)
  const fwd = target.clone().sub(pos).normalize();
  const p2 = pos.clone().add(step);
  const t2 = target.clone().add(step).addScaledVector(fwd, -step.dot(fwd));
  const ahead = t2.clone().sub(p2).dot(fwd);
  if (ahead < minTarget) t2.addScaledVector(fwd, minTarget - ahead);
  return { pos: p2, target: t2 };
}

/**
 * One pinch event's zoom (log factor), at most PINCH_STEP either way: a pinch's first event after a pause
 * can carry a whole burst (deltaY -54 seen: x1.7 in a single frame, a jump). Added to the eased zoom
 * (`pendingZoom`, `zoomTake`), never applied at once.
 */
export function pinchStep(deltaY: number): number {
  return Math.max(-PINCH_STEP, Math.min(PINCH_STEP, Math.log(zoomFactor('pinch', deltaY))));
}

/** The part of the eased zoom still to apply that one frame of `dt` seconds takes. */
export function zoomTake(pending: number, dt: number): number {
  return pending * (1 - Math.exp(-dt * 22));
}

/**
 * The wheel gesture's zoom point: reuse it (no new raycast) while events keep coming less than
 * WHEEL_GAP_MS apart and the cursor stays within 4 px; otherwise the caller picks anew at (x, y).
 * Updates `w` (the gesture's time and, on a new pick, its cursor).
 */
export function reuseWheelPick(w: { at: number; x: number; y: number }, now: number, x: number, y: number): boolean {
  const same = now - w.at < WHEEL_GAP_MS && Math.hypot(x - w.x, y - w.y) < 4;
  w.at = now;
  if (!same) {
    w.x = x;
    w.y = y;
  }
  return same;
}

// ------------------------------------------------------------------ the controller

const SCROLL_KEY = 'r3x.build.scroll';
export const PART_KEY_NAME = PLATFORM === 'mac' ? '⌘' : 'Ctrl';

export class Navigator {
  readonly platform = PLATFORM;
  readonly wheel = new WheelClassifier((() => {
    try {
      const v = localStorage.getItem(SCROLL_KEY);
      return v === 'trackpad' || v === 'mouse' ? v : 'auto';
    } catch {
      return 'auto';
    }
  })());
  /** Pointers on the canvas this module is steering with (drag or touch). */
  private ptrs = new Map<number, { x: number; y: number; action: DragAction }>();
  private pivot = new THREE.Vector3();
  private pinch: { d: number; cx: number; cy: number } | null = null;
  /** A mouse wheel's zoom still to apply (log factor) and toward where: eased over a few frames. */
  private pendingZoom = 0;
  private zoomAt = new THREE.Vector3();
  /**
   * The zoom point of the wheel gesture under way: one raycast per gesture, not per notch (a
   * trackpad sends 60+ wheel events a second, and a raycast through the build can take 15 ms).
   * Reused while the notches keep coming (< WHEEL_GAP_MS apart) and the cursor stays put; the
   * point is on a surface in world space, so it stays right while the camera closes in.
   */
  private wheelPick = { at: -Infinity, x: 0, y: 0, p: new THREE.Vector3() };
  private lastTick = 0;
  /** The controls card (? or the ? by the view cube). */
  readonly card: ControlsCard;

  constructor(private readonly wb: Workbench, private readonly host: BuildHost) {
    this.card = new ControlsCard(this);
    addEventListener('keydown', (e) => this.onKey(e));
    const el = host.renderer.domElement;
    el.addEventListener('pointerdown', (e) => this.onDown(e));
    el.addEventListener('pointermove', (e) => this.onMove(e));
    el.addEventListener('pointerup', (e) => this.onUp(e));
    el.addEventListener('pointercancel', (e) => this.onUp(e));
    el.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
    el.addEventListener('dblclick', (e) => this.onDouble(e));
    el.addEventListener('contextmenu', (e) => { if (this.on()) e.preventDefault(); });
  }

  setScrollDevice(d: ScrollDevice) {
    this.wheel.setting = d;
    try {
      localStorage.setItem(SCROLL_KEY, d);
    } catch { /* private window */ }
  }

  private on() {
    return this.wb.active && !!this.wb.top && !this.wb.video;
  }

  private ray(x: number, y: number) {
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const rc = new THREE.Raycaster();
    rc.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), this.host.camera);
    return rc.ray;
  }

  /** The point under the cursor: a part's surface, else where the ray passes the target's depth. */
  private pointAt(x: number, y: number): { p: THREE.Vector3; hit: boolean } {
    const h = this.wb.hitAt(x, y);
    if (h) return { p: h.point.clone(), hit: true };
    const ray = this.ray(x, y);
    const cam = this.host.camera;
    const fwd = cam.getWorldDirection(new THREE.Vector3());
    const depth = this.host.controls.target.clone().sub(cam.position).dot(fwd);
    return { p: ray.at(Math.max(0.05, depth / Math.max(1e-3, ray.direction.dot(fwd))), new THREE.Vector3()), hit: false };
  }

  private apply(m: { pos: THREE.Vector3; target: THREE.Vector3 }) {
    this.host.camera.position.copy(m.pos);
    this.host.controls.target.copy(m.target);
    this.host.camera.lookAt(m.target);
    this.wb.stopViewFly();
    this.host.interact();
  }

  // ---------------------------------------------------------------- actions

  orbit(dx: number, dy: number) {
    const cam = this.host.camera;
    this.apply(orbitAbout(cam.position, this.host.controls.target, this.pivot, -dx * 0.0085, dy * 0.0085));
  }

  /** Move the view with the cursor: the point at the pivot's depth stays under it. */
  pan(dx: number, dy: number) {
    const cam = this.host.camera;
    const r = this.host.renderer.domElement.getBoundingClientRect();
    const depthPt = this.pivot;
    const a = depthPt.clone().project(cam);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(cam.quaternion);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.quaternion);
    const ref = depthPt.clone().add(right).project(cam);
    const pxPerUnit = Math.abs(ref.x - a.x) * (r.width / 2) || 1;
    const move = right.multiplyScalar(-dx / pxPerUnit).addScaledVector(up, dy / pxPerUnit);
    this.apply({ pos: cam.position.clone().add(move), target: this.host.controls.target.clone().add(move) });
  }

  zoom(factor: number, toward: THREE.Vector3) {
    const cam = this.host.camera;
    // within OrbitControls' own distance limits in Build (setActive: 0.05 to 4 m), which it still enforces
    const max = Math.min(3.8, Math.max(0.5, this.wb.bounds().getSize(new THREE.Vector3()).length() * 4));
    this.apply(dollyToward(cam.position, this.host.controls.target, toward, factor, 0.03, max));
  }

  /** Per drawn frame (Workbench.tick): the eased rest of a mouse wheel's zoom. */
  tick(now: number) {
    const dt = Math.min(0.05, Math.max(0.001, (now - (this.lastTick || now)) / 1000));
    this.lastTick = now;
    if (Math.abs(this.pendingZoom) < 1e-4) {
      this.pendingZoom = 0;
      return;
    }
    const take = zoomTake(this.pendingZoom, dt);
    this.pendingZoom -= take;
    this.zoom(Math.exp(take), this.zoomAt);
  }

  // ---------------------------------------------------------------- input

  private onDown(e: PointerEvent) {
    if (!this.on() || this.wb.direct.claims(e.pointerId)) return;
    const action = e.pointerType === 'touch' ? 'orbit' : dragAction(e, this.platform);
    // a ⌘/Ctrl or Move-mode press direct.ts did not take (empty space, grounded): the camera pans, as Shift
    if (action === 'none') return;
    this.ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, action: action === 'move' ? 'pan' : action });
    this.pendingZoom = 0;
    this.wb.stopViewFly();
    if (this.ptrs.size === 1) {
      // orbit about what is under the cursor (else the target, or the model's centre); a pan keeps that depth under it
      const at = this.pointAt(e.clientX, e.clientY);
      this.pivot.copy(action === 'orbit' ? pivotFor(at.hit ? at.p : null, this.host.controls.target, this.wb.bounds()) : at.p);
    } else if (this.ptrs.size === 2) {
      const [a, b] = [...this.ptrs.values()];
      this.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
      this.pivot.copy(this.pointAt(this.pinch.cx, this.pinch.cy).p);
    }
  }

  private onMove(e: PointerEvent) {
    const p = this.ptrs.get(e.pointerId);
    if (!p) return;
    if (this.wb.direct.claims(e.pointerId)) {
      // a long-press made it the part's: the camera lets go
      this.ptrs.delete(e.pointerId);
      return;
    }
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.x = e.clientX;
    p.y = e.clientY;
    if (this.ptrs.size === 2 && this.pinch) {
      const [a, b] = [...this.ptrs.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      this.pan(cx - this.pinch.cx, cy - this.pinch.cy);
      if (this.pinch.d > 1 && d > 1) this.zoom(d / this.pinch.d, this.pointAt(cx, cy).p);
      this.pinch = { d, cx, cy };
      return;
    }
    if (this.ptrs.size !== 1) return;
    if (p.action === 'orbit') this.orbit(dx, dy);
    else if (p.action === 'pan') this.pan(dx, dy);
  }

  private onUp(e: PointerEvent) {
    this.ptrs.delete(e.pointerId);
    if (this.ptrs.size < 2) this.pinch = null;
    // the finger left behind continues as a one-finger orbit from where it is
  }

  private wheelPoint(e: WheelEvent): THREE.Vector3 {
    const w = this.wheelPick;
    if (reuseWheelPick(w, performance.now(), e.clientX, e.clientY)) return w.p;
    return w.p.copy(this.pointAt(e.clientX, e.clientY).p);
  }

  private onWheel(e: WheelEvent) {
    if (!this.on()) return;
    e.preventDefault();
    const action = this.wheel.classify(e, performance.now());
    if (action === 'pan') {
      this.pivot.copy(this.host.controls.target);
      this.pan(-e.deltaX, -e.deltaY);
    } else if (action === 'orbit') {
      this.pivot.copy(pivotFor(null, this.host.controls.target, this.wb.bounds()));
      // Shift: the browser may have swapped the axes
      this.orbit(-(e.deltaX || 0) * 0.6, -(e.deltaY || 0) * 0.6);
    } else if (action === 'pinch') {
      // eased over a few frames like the wheel, and no one event more than PINCH_STEP: a pinch's first
      // event after a pause can carry a whole burst (deltaY -54 seen: x1.7 in a single frame, a jump)
      this.zoomAt.copy(this.wheelPoint(e));
      this.pendingZoom += pinchStep(e.deltaY);
      this.host.interact();
    } else {
      // a wheel notch: eased over a few frames toward the point under the cursor
      this.zoomAt.copy(this.wheelPoint(e));
      this.pendingZoom += Math.log(zoomFactor('zoom', e.deltaY, e.deltaMode));
      this.host.interact();
    }
  }

  /** ? the card · Esc closes it · H (or Home, outside Instructions, whose Home is its own) the home view. */
  private onKey(e: KeyboardEvent) {
    if (!this.wb.active || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    let used = true;
    if (e.key === '?') this.card.toggle();
    else if (e.key === 'Escape' && this.card.open) this.card.hide();
    else if (e.key === 'h' || e.key === 'H' || (e.key === 'Home' && !this.wb.guide)) this.wb.homeView();
    else used = false;
    if (used) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }

  private onDouble(e: MouseEvent) {
    if (!this.on() || e.button !== 0) return;
    const h = this.wb.hitAt(e.clientX, e.clientY);
    const id = h ? (h.object.userData.partId ?? h.object.userData.fastenerId) as string | undefined : undefined;
    if (id) this.wb.framePart(id);
    else this.wb.frameAll();
  }
}
