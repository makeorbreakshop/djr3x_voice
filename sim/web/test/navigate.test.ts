import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { dollyToward, dragAction, orbitAbout, partKey, pivotFor, platformOf, WheelClassifier, wheelDevice, zoomFactor, type WheelLike } from '../src/workbench/navigate';
import { controlRows } from '../src/workbench/controlscard';

const w = (p: Partial<WheelLike>): WheelLike => ({ deltaX: 0, deltaY: 0, deltaMode: 0, ctrlKey: false, shiftKey: false, ...p });
const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

describe('wheel: mouse or trackpad', () => {
  it('reads a notch as a mouse wheel and two fingers as a trackpad', () => {
    expect(wheelDevice(w({ deltaY: 100 }))).toBe('mouse'); // Chrome / Windows notch
    expect(wheelDevice(w({ deltaY: -120 }))).toBe('mouse');
    expect(wheelDevice(w({ deltaY: 3, deltaMode: 1 }))).toBe('mouse'); // Firefox lines
    expect(wheelDevice(w({ deltaY: 4.5 }), 'other')).toBe('trackpad'); // fractional (off the Mac)
    expect(wheelDevice(w({ deltaY: 12 }))).toBe('trackpad'); // small step
    expect(wheelDevice(w({ deltaX: 3, deltaY: 40 }))).toBe('trackpad'); // both axes
    expect(wheelDevice(w({ deltaX: 100, shiftKey: true }))).toBe('mouse'); // Shift+wheel: Chrome swaps the axes
  });

  it('a Mac mouse wheel (fractional, accelerated, never sideways) zooms at every speed', () => {
    // macOS sends a notch as pixels: 4.000244 slow, larger when spun; a trackpad's two fingers are whole pixels
    expect(wheelDevice(w({ deltaY: 4.000244140625 }), 'mac')).toBe('mouse');
    expect(wheelDevice(w({ deltaY: -31.2 }), 'mac')).toBe('mouse');
    expect(wheelDevice(w({ deltaY: 3 }), 'mac')).toBe('trackpad');
    expect(wheelDevice(w({ deltaY: 4.5, deltaX: 1 }), 'mac')).toBe('trackpad');
    expect(wheelDevice(w({ deltaY: 4.5 }), 'other')).toBe('trackpad'); // a precision touchpad
    const c = new WheelClassifier('auto', 220, 'mac');
    expect(c.classify(w({ deltaY: 4.000244140625 }), 0)).toBe('zoom'); // slow
    expect(c.classify(w({ deltaY: 13.6 }), 30)).toBe('zoom');
    expect(c.classify(w({ deltaY: -4.000244140625 }), 1000)).toBe('zoom'); // a new gesture, still the wheel
    // two fingers on the same Mac still pan
    expect(c.classify(w({ deltaY: 2, deltaX: 1 }), 3000)).toBe('pan');
    expect(c.classify(w({ deltaY: 5 }), 3016)).toBe('pan');
  });

  it('pinch is a ctrlKey wheel: zoom, whatever the device', () => {
    const c = new WheelClassifier();
    expect(c.classify(w({ deltaY: 2.3, ctrlKey: true }), 0)).toBe('pinch');
    expect(c.classify(w({ deltaY: 100, ctrlKey: true }), 1000)).toBe('pinch'); // Ctrl+wheel on Windows
  });

  it('two-finger scroll pans (Shift: orbits), the wheel zooms; a gesture keeps its device', () => {
    const c = new WheelClassifier('auto', 220, 'other');
    expect(c.classify(w({ deltaY: 7, deltaX: 1 }), 0)).toBe('pan');
    // later events of the same gesture, even one shaped like a notch, stay a pan
    expect(c.classify(w({ deltaY: 60 }), 16)).toBe('pan');
    expect(c.classify(w({ deltaY: 8, shiftKey: true }), 32)).toBe('orbit');
    // a pause, then a wheel notch: a zoom
    expect(c.classify(w({ deltaY: 100 }), 2000)).toBe('zoom');
    // a trackpad's sideways motion right after a notch is still read as the trackpad
    expect(c.classify(w({ deltaY: 2.5, deltaX: 4 }), 2050)).toBe('pan');
  });

  it('the setting overrides the guess', () => {
    const m = new WheelClassifier('mouse');
    expect(m.classify(w({ deltaY: 4.5, deltaX: 2 }), 0)).toBe('zoom');
    const t = new WheelClassifier('trackpad');
    expect(t.classify(w({ deltaY: 100 }), 0)).toBe('pan');
    expect(t.classify(w({ deltaY: 2, ctrlKey: true }), 500)).toBe('pinch');
  });

  it('zoom: in for a negative delta, out for a positive one; a pinch finer per pixel', () => {
    expect(zoomFactor('zoom', -100)).toBeGreaterThan(1);
    expect(zoomFactor('zoom', 100)).toBeLessThan(1);
    expect(zoomFactor('zoom', 100) * zoomFactor('zoom', -100)).toBeCloseTo(1, 9);
    expect(zoomFactor('zoom', 3, 1)).toBeCloseTo(zoomFactor('zoom', 99), 9); // lines as pixels
    expect(zoomFactor('pinch', -10)).toBeGreaterThan(1);
  });
});

describe('drags and keys, per platform', () => {
  const ev = (p: Partial<{ button: number; shiftKey: boolean; metaKey: boolean; ctrlKey: boolean }>) => ({ button: 0, shiftKey: false, metaKey: false, ctrlKey: false, ...p });
  it('left orbits; right, middle and Shift+left pan', () => {
    for (const p of ['mac', 'other'] as const) {
      expect(dragAction(ev({}), p)).toBe('orbit');
      expect(dragAction(ev({ button: 2 }), p)).toBe('pan');
      expect(dragAction(ev({ button: 1 }), p)).toBe('pan');
      expect(dragAction(ev({ shiftKey: true }), p)).toBe('pan');
    }
  });

  it('the part key is ⌘ on a Mac and Ctrl elsewhere (Ctrl-click on a Mac is the context click)', () => {
    expect(platformOf('MacIntel')).toBe('mac');
    expect(platformOf('Win32')).toBe('other');
    expect(partKey({ metaKey: true, ctrlKey: false }, 'mac')).toBe(true);
    expect(partKey({ metaKey: false, ctrlKey: true }, 'mac')).toBe(false);
    expect(partKey({ metaKey: false, ctrlKey: true }, 'other')).toBe(true);
    expect(partKey({ metaKey: true, ctrlKey: false }, 'other')).toBe(false);
    expect(dragAction(ev({ metaKey: true }), 'mac')).toBe('move');
    expect(dragAction(ev({ ctrlKey: true }), 'other')).toBe('move');
    expect(dragAction(ev({ ctrlKey: true }), 'mac')).toBe('orbit');
    // the card names this platform's key
    expect(controlRows('⌘').find((r) => r[0] === 'Move part')![1]).toBe('⌘ drag');
    expect(controlRows('Ctrl').find((r) => r[0] === 'All its joints')![2]).toBe('Ctrl ⇧ drag');
  });
});

describe('camera moves', () => {
  it('the pivot is the point under the cursor, else the target', () => {
    expect(pivotFor(V(1, 2, 3), V(0, 0, 0))).toEqual(V(1, 2, 3));
    expect(pivotFor(null, V(0, 1, 0))).toEqual(V(0, 1, 0));
  });

  it('on empty space, a target slid off the model gives way to the model centre', () => {
    const model = new THREE.Box3(V(-0.2, 0, -0.2), V(0.2, 1, 0.2));
    expect(pivotFor(null, V(0.1, 0.5, 0), model)).toEqual(V(0.1, 0.5, 0)); // on the model: kept
    expect(pivotFor(null, V(0.22, 0.5, 0), model)).toEqual(V(0.22, 0.5, 0)); // within the 10% pad
    expect(pivotFor(null, V(3, 0.5, 0), model)).toEqual(V(0, 0.5, 0)); // out in space: the centre
    expect(pivotFor(V(3, 0, 0), V(0, 0, 0), model)).toEqual(V(3, 0, 0)); // a hit always wins
    expect(pivotFor(null, V(3, 0, 0), new THREE.Box3())).toEqual(V(3, 0, 0)); // nothing shown
  });

  it('orbit turns the rig about the pivot: the pivot stays put on screen, the view never flips', () => {
    const pos = V(0, 0.5, 2), target = V(0, 0.5, 0), pivot = V(0.3, 0.6, 0.1);
    const cam = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
    const screen = (p: THREE.Vector3, t: THREE.Vector3) => {
      cam.position.copy(p);
      cam.lookAt(t);
      cam.updateMatrixWorld();
      return pivot.clone().project(cam);
    };
    const before = screen(pos, target);
    const m = orbitAbout(pos, target, pivot, 0.7, 0.3);
    const after = screen(m.pos, m.target);
    expect(after.distanceTo(before)).toBeLessThan(1e-9);
    expect(m.pos.distanceTo(pivot)).toBeCloseTo(pos.distanceTo(pivot), 9);
    // pitch up raises the camera (the view from higher up), as dragging down does in any orbit viewer
    const raised = orbitAbout(pos, target, target, 0, 0.3);
    expect(raised.pos.y).toBeGreaterThan(pos.y);
    // pitch past the pole stops short of it
    const over = orbitAbout(pos, target, target, 0, 3);
    const fwd = over.target.clone().sub(over.pos).normalize();
    expect(Math.abs(fwd.y)).toBeLessThan(0.9999);
  });

  it('zoom dollies toward the point: the cursor point stays put, the target is closed on, never passed', () => {
    const pos = V(0, 0, 2), target = V(0, 0, 0), pt = V(0.2, 0, 0);
    const z = dollyToward(pos, target, pt, 2, 0.03, 10);
    expect(z.pos.distanceTo(pt)).toBeCloseTo(pos.distanceTo(pt) / 2, 9);
    // on the same line: the point under the cursor stays under it; the view keeps its direction
    expect(z.pos.clone().sub(pt).normalize().distanceTo(pos.clone().sub(pt).normalize())).toBeLessThan(1e-9);
    expect(z.target.clone().sub(z.pos).normalize().distanceTo(V(0, 0, -1))).toBeLessThan(1e-9);
    // nearer the target, not carrying it along
    expect(z.pos.distanceTo(z.target)).toBeLessThan(1.1);
    // zoomed hard in: the target is pushed ahead, never behind the camera
    const close = dollyToward(pos, target, pt, 1e6, 0.03, 10);
    expect(close.pos.distanceTo(pt)).toBeCloseTo(0.03, 9);
    expect(close.target.clone().sub(close.pos).dot(V(0, 0, -1))).toBeGreaterThanOrEqual(0.08 - 1e-9);
    const far = dollyToward(pos, target, pt, 1e-6, 0.03, 10);
    expect(far.pos.distanceTo(pt)).toBeCloseTo(10, 9);
  });
});
