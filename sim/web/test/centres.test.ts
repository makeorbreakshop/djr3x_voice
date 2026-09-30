import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { arcPoints, atHome, formatValue, onCircle, perpendicular, zeroDirection, type V3 } from '../src/centres';

const close = (a: V3, b: V3) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 9));

describe('centre gizmo math', () => {
  it('zero points forward for the body axis, and along the part for a tilted axis', () => {
    close(zeroDirection([0, 1, 0], []), [0, 0, 1]);
    // A hint along the axis is unusable: fall back to forward.
    close(zeroDirection([0, 1, 0], [[0, 2, 0]]), [0, 0, 1]);
    // A shoulder about X with its arm hanging down and a little forward: zero is the arm.
    const z = zeroDirection([1, 0, 0], [[0.3, -1, 0.2]]);
    close(z, perpendicular([1, 0, 0], [0, -1, 0.2])!);
    expect(z[0]).toBeCloseTo(0, 12);
  });

  it('the needle turns the way the rig turns the joint', () => {
    // Rig.Joint.set: quaternion.setFromAxisAngle(axis, deg). The gizmo must agree.
    const axis: V3 = [0.83867, 0, -0.54464];
    const zero = zeroDirection(axis, [[0, -1, 0.3]]);
    for (const deg of [-40, -5, 0, 17, 30]) {
      const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(...axis).normalize(), THREE.MathUtils.degToRad(deg));
      const want = new THREE.Vector3(...zero).applyQuaternion(q);
      close(onCircle(axis, zero, deg), [want.x, want.y, want.z]);
    }
    // head_pan: +pan turns the face toward +X (the droid's left).
    expect(onCircle([0, 1, 0], [0, 0, 1], 90)[0]).toBeCloseTo(1, 12);
  });

  it('arcs span the range at the radius', () => {
    const pts = arcPoints([0, 1, 0], [0, 0, 1], -66, 66, 0.1);
    close(pts[0], onCircle([0, 1, 0], [0, 0, 1], -66, 0.1));
    close(pts[pts.length - 1], onCircle([0, 1, 0], [0, 0, 1], 66, 0.1));
    expect(pts.every((p) => Math.abs(Math.hypot(...p) - 0.1) < 1e-12)).toBe(true);
    expect(pts.length).toBe(34);
  });

  it('labels and the at-home check', () => {
    expect(formatValue(0.02, 'deg')).toBe('0.0°');
    expect(formatValue(-3.44, 'deg')).toBe('-3.4°');
    expect(formatValue(22.6, 'deg')).toBe('+23°');
    expect(formatValue(4, 'mm')).toBe('+4.0 mm');
    const home = { head_pan: 10 };
    expect(atHome({ head_pan: 9.6, visor: -0.3 }, home, ['head_pan', 'visor'], 0.5)).toBe(true);
    expect(atHome({ head_pan: 9.6, visor: -0.6 }, home, ['head_pan', 'visor'], 0.5)).toBe(false);
    expect(atHome({ head_pan: 10 }, home, ['head_pan', 'visor'], 0.5)).toBe(false);
  });
});
