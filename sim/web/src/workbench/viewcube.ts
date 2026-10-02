/**
 * Build's view cube, as in Fusion and Onshape: a small cube in the viewport's top-right corner that turns
 * with the camera, labelled in the droid's frame (Front is his +Z face; Right is +X, the side on your right
 * when you face him). Click a face for that view, an edge for the 45 degree view between two faces, a
 * corner for the isometric; drag it to orbit; the house frames the focus (as F). CSS 3D, no extra render.
 */

import * as THREE from 'three';
import type { BuildHost, Workbench } from './workbench';

export type CubeFace = 'front' | 'back' | 'right' | 'left' | 'top' | 'bottom';

/** Each face: its outward normal, and its own right and up as drawn (world = the droid's frame). */
export const FACES: Record<CubeFace, { n: [number, number, number]; r: [number, number, number]; u: [number, number, number]; css: string; label: string }> = {
  front: { n: [0, 0, 1], r: [1, 0, 0], u: [0, 1, 0], css: 'translateZ(var(--h))', label: 'Front' },
  back: { n: [0, 0, -1], r: [-1, 0, 0], u: [0, 1, 0], css: 'rotateY(180deg) translateZ(var(--h))', label: 'Back' },
  right: { n: [1, 0, 0], r: [0, 0, -1], u: [0, 1, 0], css: 'rotateY(90deg) translateZ(var(--h))', label: 'Right' },
  left: { n: [-1, 0, 0], r: [0, 0, 1], u: [0, 1, 0], css: 'rotateY(-90deg) translateZ(var(--h))', label: 'Left' },
  top: { n: [0, 1, 0], r: [1, 0, 0], u: [0, 0, -1], css: 'rotateX(90deg) translateZ(var(--h))', label: 'Top' },
  bottom: { n: [0, -1, 0], r: [1, 0, 0], u: [0, 0, 1], css: 'rotateX(-90deg) translateZ(var(--h))', label: 'Bottom' },
};

/**
 * The direction from the target to the camera for a click on a face's 3 x 3 grid: the centre (0, 0) looks
 * square at the face, an edge cell (one of i, j is +-1) along the edge's 45 degree diagonal, a corner cell
 * the isometric. The same edge or corner clicked on either face it borders gives the same view.
 */
export function cubeDirection(face: CubeFace, i: -1 | 0 | 1, j: -1 | 0 | 1): THREE.Vector3 {
  const f = FACES[face];
  return new THREE.Vector3(...f.n).addScaledVector(new THREE.Vector3(...f.r), i).addScaledVector(new THREE.Vector3(...f.u), j).normalize();
}

/** Straight up or down is the orbit's pole: lean a hair toward the front so the view has a defined up
 *  (Top shows the front at the bottom, Bottom at the top - as the faces are drawn). */
export function poleSafe(dir: THREE.Vector3): THREE.Vector3 {
  const d = dir.clone().normalize();
  if (Math.abs(d.y) > 0.9999) d.set(0, Math.sign(d.y), 1e-4).normalize();
  return d;
}

/** The cube's CSS matrix for a camera orientation: the inverse of the camera's turn, in CSS axes (y down). */
export function cubeMatrix(cameraQuaternion: THREE.Quaternion): THREE.Matrix4 {
  const r = new THREE.Matrix4().makeRotationFromQuaternion(cameraQuaternion.clone().invert());
  const s = new THREE.Matrix4().makeScale(1, -1, 1);
  return s.clone().multiply(r).multiply(s);
}

const HOUSE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 7.5 8 2.5l5.5 5M4 6.5v7h3v-4h2v4h3v-7"/></svg>';

export class ViewCube {
  private readonly el: HTMLDivElement;
  private readonly box: HTMLDivElement;
  private lastCss = '';
  private lastPlace = '';
  private drag: { x: number; y: number; moved: boolean; pointer: number } | null = null;

  constructor(private readonly wb: Workbench, private readonly host: BuildHost) {
    const el = document.createElement('div');
    el.className = 'bb-cube';
    el.setAttribute('role', 'group');
    el.setAttribute('aria-label', 'View cube');
    const cells = (face: CubeFace) => {
      const out: string[] = [];
      for (const j of [1, 0, -1]) for (const i of [-1, 0, 1]) {
        const centre = i === 0 && j === 0;
        out.push(centre
          ? `<button type="button" class="z c" data-face="${face}" data-i="0" data-j="0" aria-label="${FACES[face].label} view">${FACES[face].label}</button>`
          : `<div class="z ${i && j ? 'k' : 'e'}" data-face="${face}" data-i="${i}" data-j="${j}" aria-hidden="true"></div>`);
      }
      return out.join('');
    };
    el.innerHTML = `<div class="bb-cube-stage"><div class="bb-cube-box">${(Object.keys(FACES) as CubeFace[])
      .map((f) => `<div class="f" style="transform:${FACES[f].css}">${cells(f)}</div>`).join('')}</div></div>
      <button type="button" class="bb-cube-home" aria-label="Frame the view (F)" title="Frame the view (F)">${HOUSE}</button>`;
    document.body.append(el);
    this.el = el;
    this.box = el.querySelector('.bb-cube-box')!;
    el.querySelector<HTMLButtonElement>('.bb-cube-home')!.onclick = (e) => {
      (e.currentTarget as HTMLButtonElement).blur();
      if (this.wb.guide) this.wb.frameGuide(true);
      else this.wb.frame(false, true);
    };
    // keyboard: the face centres are buttons
    el.addEventListener('click', (e) => {
      const z = (e.target as HTMLElement).closest<HTMLElement>('button.z');
      if (z && e.detail === 0) this.snap(z);
    });
    const stage = el.querySelector<HTMLElement>('.bb-cube-stage')!;
    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      this.drag = { x: e.clientX, y: e.clientY, moved: false, pointer: e.pointerId };
      stage.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    stage.addEventListener('pointermove', (e) => {
      const d = this.drag;
      if (!d || e.pointerId !== d.pointer) return;
      const dx = e.clientX - d.x, dy = e.clientY - d.y;
      if (!d.moved && Math.hypot(dx, dy) < 3) return;
      d.moved = true;
      d.x = e.clientX;
      d.y = e.clientY;
      this.orbit(dx, dy);
    });
    const up = (e: PointerEvent) => {
      const d = this.drag;
      if (!d || e.pointerId !== d.pointer) return;
      this.drag = null;
      if (d.moved) return;
      // a click: the cell under the pointer (the capture retargets the event to the stage)
      const hit = document.elementsFromPoint(e.clientX, e.clientY).find((n) => (n as HTMLElement).dataset?.face) as HTMLElement | undefined;
      if (hit) this.snap(hit);
    };
    stage.addEventListener('pointerup', up);
    stage.addEventListener('pointercancel', () => (this.drag = null));
  }

  private snap(cell: HTMLElement) {
    const dir = cubeDirection(cell.dataset.face as CubeFace, Number(cell.dataset.i) as -1 | 0 | 1, Number(cell.dataset.j) as -1 | 0 | 1);
    this.wb.viewFrom(dir, true);
  }

  /** Drag on the cube: turn the view about the target (left/right about the vertical, up/down over it). */
  private orbit(dx: number, dy: number) {
    const cam = this.host.camera;
    const t = this.host.controls.target;
    const off = cam.position.clone().sub(t);
    const sph = new THREE.Spherical().setFromVector3(off);
    sph.theta -= dx * 0.012;
    sph.phi = Math.min(Math.PI - 1e-3, Math.max(1e-3, sph.phi - dy * 0.012));
    cam.position.copy(t).add(new THREE.Vector3().setFromSpherical(sph));
    cam.lookAt(t);
    this.wb.stopViewFly();
    this.host.interact();
  }

  /** Per frame while Build is open: follow the camera, sit in the viewport's top-right corner. */
  update(visible: boolean) {
    this.el.hidden = !visible;
    if (!visible) return;
    const q = this.host.camera.getWorldQuaternion(new THREE.Quaternion());
    const css = `matrix3d(${cubeMatrix(q).elements.map((v) => (Math.abs(v) < 1e-10 ? 0 : v).toFixed(6)).join(',')})`;
    if (css !== this.lastCss) {
      this.lastCss = css;
      this.box.style.transform = css;
    }
    // inside the viewport: left of the R3X panel, or of the guide's text column
    const g = this.wb.guide ? this.wb.guideRect : null;
    const panel = document.documentElement.style.getPropertyValue('--panel-space').trim() || '0px';
    const place = g ? `${g.right + 12}px|${g.top + 8}px` : `calc(${panel} + 4px)|14px`;
    if (place !== this.lastPlace) {
      this.lastPlace = place;
      const [right, top] = place.split('|');
      this.el.style.right = right;
      this.el.style.top = top;
    }
  }
}
