import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { dragMode, nextMoveMode, pressAction, type PressInput } from '../src/workbench/drag';
import { cubeDirection, cubeMatrix, FACES, poleSafe, type CubeFace } from '../src/workbench/viewcube';

const press = (p: Partial<PressInput>): PressInput => ({ button: 0, mod: false, moveMode: false, onHandle: false, target: 'movable', exploded: false, ...p });

describe('navigation or move: what a press does', () => {
  it('a plain left drag navigates, even on a part; right and middle always navigate', () => {
    expect(pressAction(press({}))).toBe('navigate');
    expect(pressAction(press({ target: 'grounded' }))).toBe('navigate');
    expect(pressAction(press({ target: 'none', mod: true }))).toBe('navigate');
    expect(pressAction(press({ button: 2, mod: true }))).toBe('navigate');
    expect(pressAction(press({ button: 1, moveMode: true, onHandle: true }))).toBe('navigate');
  });

  it('a handle always drags its joint (not while exploded)', () => {
    expect(pressAction(press({ onHandle: true }))).toBe('handle');
    expect(pressAction(press({ onHandle: true, target: 'none' }))).toBe('handle');
    expect(pressAction(press({ onHandle: true, exploded: true }))).toBe('navigate');
  });

  it('⌘/Ctrl, Move mode or a touch long-press move a part; grounded or exploded is blocked', () => {
    expect(pressAction(press({ mod: true }))).toBe('move');
    expect(pressAction(press({ moveMode: true }))).toBe('move');
    expect(pressAction(press({ longPress: true }))).toBe('move');
    expect(pressAction(press({ mod: true, target: 'grounded' }))).toBe('blocked');
    expect(pressAction(press({ moveMode: true, exploded: true }))).toBe('blocked');
  });

  it('a part drag: the direction lock, or with Shift all its joints together (no other variants)', () => {
    expect(dragMode({ shift: false })).toEqual({ chain: 'default', free: false });
    expect(dragMode({ shift: true })).toEqual({ chain: 'default', free: true });
  });

  it('Move mode: M toggles, Esc leaves, other keys keep it', () => {
    let on = false;
    on = nextMoveMode(on, 'm');
    expect(on).toBe(true);
    expect(nextMoveMode(on, 'x')).toBe(true);
    expect(nextMoveMode(on, 'Escape')).toBe(false);
    expect(nextMoveMode(on, 'M')).toBe(false);
    expect(nextMoveMode(false, 'Escape')).toBe(false);
  });
});

describe('view cube', () => {
  const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z).normalize();
  const close = (a: THREE.Vector3, b: THREE.Vector3) => expect(a.distanceTo(b)).toBeLessThan(1e-9);

  it('faces look square at the droid: Front from +Z, Right from +X, Top from above', () => {
    close(cubeDirection('front', 0, 0), v(0, 0, 1));
    close(cubeDirection('back', 0, 0), v(0, 0, -1));
    close(cubeDirection('right', 0, 0), v(1, 0, 0));
    close(cubeDirection('left', 0, 0), v(-1, 0, 0));
    close(cubeDirection('top', 0, 0), v(0, 1, 0));
    close(cubeDirection('bottom', 0, 0), v(0, -1, 0));
  });

  it('an edge is the 45 degree view and a corner the isometric, the same from either face', () => {
    close(cubeDirection('front', 1, 0), v(1, 0, 1)); // front-right edge from the front
    close(cubeDirection('right', -1, 0), v(1, 0, 1)); // ... and from the right
    close(cubeDirection('front', 0, 1), v(0, 1, 1)); // front-top
    close(cubeDirection('top', 0, -1), v(0, 1, 1)); // ... from the top (its bottom edge is the front)
    close(cubeDirection('front', 1, 1), v(1, 1, 1)); // the front-right-top corner
    close(cubeDirection('right', -1, 1), v(1, 1, 1));
    close(cubeDirection('top', 1, -1), v(1, 1, 1));
    // every edge and corner cell agrees with its neighbours (each view reached from 2 or 3 faces)
    const seen = new Map<string, number>();
    for (const f of Object.keys(FACES) as CubeFace[]) for (const i of [-1, 0, 1] as const) for (const j of [-1, 0, 1] as const) {
      const k = cubeDirection(f, i, j).toArray().map((x) => x.toFixed(6)).join();
      seen.set(k, (seen.get(k) ?? 0) + 1);
    }
    expect(seen.size).toBe(26); // 6 faces + 12 edges + 8 corners
    expect([...seen.values()].sort()).toEqual([...Array(6).fill(1), ...Array(12).fill(2), ...Array(8).fill(3)].sort());
  });

  it('top and bottom lean off the pole so the view has an up (front at the bottom of Top)', () => {
    const top = poleSafe(cubeDirection('top', 0, 0));
    expect(top.y).toBeGreaterThan(0.9999);
    expect(top.z).toBeGreaterThan(0);
    const cam = new THREE.PerspectiveCamera();
    cam.position.copy(top);
    cam.lookAt(0, 0, 0);
    // screen up, in the world: toward -Z (the back), so the front sits at the bottom, as the Top face is drawn
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.quaternion);
    expect(up.z).toBeLessThan(-0.99);
    close(poleSafe(v(1, 1, 0)), v(1, 1, 0));
  });

  it("turns with the camera: the face toward the camera faces the viewer", () => {
    // a face's CSS normal (y down) is its world normal with y flipped
    const cssNormal = (f: CubeFace) => new THREE.Vector3(FACES[f].n[0], -FACES[f].n[1], FACES[f].n[2]);
    for (const f of Object.keys(FACES) as CubeFace[]) {
      for (const [i, j] of [[0, 0], [1, 1], [-1, 0]] as const) {
        const cam = new THREE.PerspectiveCamera();
        cam.position.copy(poleSafe(cubeDirection(f, i, j)).multiplyScalar(3));
        cam.lookAt(0, 0, 0);
        cam.updateMatrixWorld();
        const n = cssNormal(f).applyMatrix4(new THREE.Matrix4().extractRotation(cubeMatrix(cam.quaternion)));
        // CSS +z is toward the viewer: the clicked face leans toward us (square on at the centre cell)
        expect(n.z).toBeGreaterThan(i === 0 && j === 0 ? 0.999 : 0.5);
      }
    }
  });
});
