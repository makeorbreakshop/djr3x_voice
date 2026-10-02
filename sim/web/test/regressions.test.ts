/**
 * Regression tests for the viewer and Build-rendering fixes of 2026-10-02, one block per behaviour
 * (numbered as in the fix list). The decisions are pure functions next to the code that uses them;
 * the browser-only halves are in scripts/viewer-check.mjs.
 */
import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { acceleratedRaycast, type MeshBVH } from 'three-mesh-bvh';
import { SETTLE_FRAMES, aoStep, buildFrameKind, cameraHash, frameScale, type BuildFrame } from '../src/buildframe';
import { FramePacer } from '../src/pacer';
import { PINCH_STEP, WHEEL_GAP_MS, pinchStep, reuseWheelPick, zoomTake } from '../src/workbench/navigate';
import { bvhFor, isShellsOnly, lookShows, noteArrival } from '../src/workbench/workbench';
import { LINK_SETTLE_MS, linkSettings, mayWriteLink, parseView } from '../src/viewer/link';
import { classifyStep } from '../src/viewer/diag';
import { firstParts } from '../src/workbench/loadorder';
import { mayExist, notePublished } from '../src/workbench/published';
import type { MAssembly, MPart } from '../src/workbench/manifest';

describe('1. idle Build draws nothing after a drag (damping residue)', () => {
  it('a camera creeping by 1e-6..1e-8 a frame hashes the same', () => {
    const m = new THREE.Matrix4().makeRotationY(0.7).setPosition(0.9, 1.3, 2.2);
    const e = [...m.elements];
    const crept = e.map((v, i) => v + (i % 3 === 0 ? 3e-7 : -2e-8));
    expect(cameraHash(crept, 1.1)).toBe(cameraHash(e, 1.1));
  });
  it('a real move (1 mm) still changes it', () => {
    const e = [...new THREE.Matrix4().setPosition(0.9, 1.3, 2.2).elements];
    const moved = e.slice();
    moved[12] += 1e-3;
    expect(cameraHash(moved, 1.1)).not.toBe(cameraHash(e, 1.1));
  });
  it('an unchanged scene after its still frame skips until the refresh', () => {
    const base = { settled: true, sig: 5, drawnSig: 5, refreshMs: 3000 };
    expect(buildFrameKind({ ...base, stillAt: 1000, now: 1500 })).toBe('skip');
    expect(buildFrameKind({ ...base, stillAt: 1000, now: 4100 })).toBe('still');
    expect(buildFrameKind({ ...base, settled: false, stillAt: 1000, now: 1500 })).toBe('moving');
    expect(buildFrameKind({ ...base, sig: 6, stillAt: -Infinity, now: 1500 })).toBe('still');
  });
});

describe('2. one pixel ratio for moving, changing and still Build frames', () => {
  it('a machine that holds frame rate keeps native in every kind', () => {
    const build = { scale: 2, max: 2 }; // native, not lowered
    const quality = { scale: 0.9, max: 1.5 }; // the other modes' dynamic scale
    const s = (['moving', 'changing', 'still'] as const).map((k) => frameScale(k, k === 'moving', true, build, quality));
    expect(new Set(s).size).toBe(1);
    expect(s[0]).toBe(2);
  });
  it('the other modes keep the quality scale', () => {
    expect(frameScale(null, true, false, { scale: 2, max: 2 }, { scale: 0.9, max: 1.5 })).toBe(0.9);
  });
});

describe('3. progressive shading: cheap AO while moving, the still AO accumulated over a few frames', () => {
  /** The frames a sequence of kinds draws: [pass, reset] per drawn frame, '-' for one not drawn. */
  const run = (kinds: BuildFrame[]) => {
    let refined = 0;
    return kinds.map((k) => {
      const s = aoStep(true, k, refined);
      refined = s.refined;
      return s.draw ? `${s.pass}${s.reset ? '!' : ''}` : '-';
    });
  };
  it('moving and changing frames draw the cheap AO; the first still frame restarts the accumulation', () => {
    expect(run(['moving', 'moving', 'changing', 'still'])).toEqual(['move', 'move', 'move', 'still!']);
  });
  it(`the settle draws ${SETTLE_FRAMES} accumulation frames, then nothing`, () => {
    const out = run(['moving', 'still', 'skip', 'skip', 'skip', 'skip', 'skip', 'skip']);
    expect(out.filter((x) => x.startsWith('still')).length).toBe(SETTLE_FRAMES);
    expect(out.slice(-3)).toEqual(['-', '-', '-']);
  });
  it('a refresh after the settle adds a frame without restarting; input restarts it', () => {
    expect(run(['still', 'skip', 'skip', 'skip', 'skip', 'still', 'moving', 'still'])).toEqual(['still!', 'still', 'still', 'still', '-', 'still', 'move', 'still!']);
  });
  it("other modes and non-Build frames keep the composer's own pass", () => {
    expect(aoStep(false, null, 0)).toEqual({ draw: true, pass: null, reset: false, refined: 0 });
    expect(aoStep(false, 'moving', 0).pass).toBeNull();
  });
});

describe('4. background arrivals are not input', () => {
  it('a host with `changed` gets a touch, not an interaction', () => {
    const pacer = new FramePacer({ active: 30, quiet: 15 });
    const calls: string[] = [];
    noteArrival({ interact: () => calls.push('interact'), changed: () => calls.push('changed') });
    expect(calls).toEqual(['changed']);
    // the viewer's host (page.ts): changed = pacer.touch, which leaves the pacer settled (no 'moving' frames)
    pacer.interact(0);
    noteArrival({ interact: () => pacer.interact(500), changed: () => pacer.touch(500) });
    expect(pacer.settled(150, 600)).toBe(true);
    expect(pacer.touches).toBe(1);
  });
  it('a host without it (the sim) still gets interact, with its own this', () => {
    const host = { n: 0, interact() { this.n++; } };
    noteArrival(host);
    expect(host.n).toBe(1);
  });
});

describe('5. BVH picking matches the brute-force raycast', () => {
  it('same hit, and the geometry index (draw order) untouched', () => {
    const geo = new THREE.TorusKnotGeometry(0.3, 0.1, 128, 24);
    const index0 = Array.from(geo.index!.array);
    const plain = new THREE.Mesh(geo, new THREE.MeshBasicMaterial());
    const fast = new THREE.Mesh(geo.clone(), plain.material);
    (fast.geometry as THREE.BufferGeometry & { boundsTree?: MeshBVH }).boundsTree = bvhFor(fast.geometry);
    fast.raycast = acceleratedRaycast;
    expect(Array.from(fast.geometry.index!.array)).toEqual(index0);
    const rc = new THREE.Raycaster();
    for (const [x, y] of [[0, 0], [0.2, 0.1], [-0.25, -0.05], [0.31, 0.2], [2, 2]]) {
      rc.set(new THREE.Vector3(x, y, 2), new THREE.Vector3(0, 0, -1));
      const a = rc.intersectObject(plain)[0];
      const b = rc.intersectObject(fast)[0];
      expect(!!b).toBe(!!a);
      if (a && b) {
        expect(b.distance).toBeCloseTo(a.distance, 6);
        expect(b.faceIndex).toBe(a.faceIndex);
      }
    }
  });
});

describe('7. pinch: bounded per event, eased', () => {
  it('no single event zooms more than PINCH_STEP (a -54 burst was x1.7)', () => {
    expect(Math.abs(pinchStep(-54))).toBeLessThanOrEqual(PINCH_STEP + 1e-12);
    expect(Math.abs(pinchStep(80))).toBeLessThanOrEqual(PINCH_STEP + 1e-12);
    expect(Math.sign(pinchStep(-2))).toBe(-Math.sign(pinchStep(2))); // a small step passes, in its direction
    expect(Math.abs(pinchStep(-2))).toBeLessThan(PINCH_STEP);
  });
  it('one frame applies only part of it', () => {
    const take = zoomTake(PINCH_STEP, 1 / 60);
    expect(take).toBeGreaterThan(0);
    expect(take).toBeLessThan(PINCH_STEP * 0.5);
  });
});

describe('8. one zoom-point raycast per wheel gesture', () => {
  it('reused while events keep coming at the same cursor; anew after the gap or a move', () => {
    const w = { at: -Infinity, x: 0, y: 0 };
    expect(reuseWheelPick(w, 0, 100, 100)).toBe(false); // first: pick
    expect(reuseWheelPick(w, 16, 100, 100)).toBe(true);
    expect(reuseWheelPick(w, 32, 102, 101)).toBe(true); // within 4 px
    expect(reuseWheelPick(w, 48, 120, 100)).toBe(false); // the cursor moved
    expect(reuseWheelPick(w, 48 + WHEEL_GAP_MS + 1, 120, 100)).toBe(false); // the gap: a new gesture
    expect(reuseWheelPick(w, 48 + WHEEL_GAP_MS + 20, 120, 100)).toBe(true);
  });
});

describe('9. share link: defaults and the writer gate', () => {
  it("a link's unmentioned settings take the design's defaults, not saved state", () => {
    expect(linkSettings(parseView('#at=lib:hunter&look=mechanism')!)).toEqual({ look: 'mechanism', ctx: 'ghost', explode: 0, fasteners: true, joints: [] });
    expect(linkSettings(parseView('#at=lib:kit')!).look).toBeNull(); // the design's own look
    expect(linkSettings(parseView('#at=build')!).look).toBe('exterior');
  });
  it('unknown ids and looks fall back', () => {
    expect(parseView('#at=what:x&look=sideways&ctx=maybe')).toEqual({ at: { kind: 'list' } });
  });
  it('never writes the address while a pointer is down or within 350 ms of input', () => {
    const settledAfter = (quiet: number) => (ms: number) => quiet >= ms;
    expect(LINK_SETTLE_MS).toBe(350);
    expect(mayWriteLink(1, settledAfter(10_000))).toBe(false);
    expect(mayWriteLink(0, settledAfter(200))).toBe(false);
    expect(mayWriteLink(0, settledAfter(400))).toBe(true);
  });
});

describe('10-12. looks and the focus (refresh)', () => {
  const part = { look: 'mechanism' as const, outside: false, shell: false, inScope: false, scope: 'library' as const };
  it('10. Ghost/Hide decides the rest of the droid inside a library design', () => {
    expect(lookShows({ ...part, context: 'ghost' })).toBe(true);
    expect(lookShows({ ...part, context: 'hide' })).toBe(false);
    expect(lookShows({ ...part, scope: 'system', context: 'ghost' })).toBe(true);
    expect(lookShows({ ...part, scope: 'system', context: 'hide' })).toBe(false);
  });
  it('11. Exterior draws a library design\'s own parts (Lower cage: inside, not seen from outside)', () => {
    expect(lookShows({ look: 'exterior', outside: false, shell: false, inScope: true, scope: 'library', context: 'hide' })).toBe(true);
    // the rest of the build in Exterior is still only what is seen from outside
    expect(lookShows({ look: 'exterior', outside: false, shell: false, inScope: true, scope: null, context: 'ghost' })).toBe(false);
    expect(lookShows({ look: 'exterior', outside: false, shell: false, inScope: true, scope: 'system', context: 'ghost' })).toBe(false);
  });
  it('12. a design of shells only is shells-only; anything with a mechanism part is not', () => {
    expect(isShellsOnly('library', ['shell', 'shell'])).toBe(true);
    expect(isShellsOnly('library', ['shell', 'mech'])).toBe(false);
    expect(isShellsOnly('system', ['shell'])).toBe(false);
    expect(isShellsOnly('library', [])).toBe(false);
  });
});

describe('15. diagnostic: jumps and moves with no input', () => {
  it('flags a move 20+ quiet frames after input as unprompted', () => {
    const steps: number[] = [];
    expect(classifyStep(steps, 0.5, 21)).toBe('unprompted');
    expect(classifyStep(steps, 0.5, 3)).toBe('');
  });
  it('flags a step many times the gesture\'s usual one as a jump, after 8 steps', () => {
    const steps: number[] = [];
    for (let i = 0; i < 8; i++) expect(classifyStep(steps, 0.3, 0)).toBe('');
    expect(classifyStep(steps, 5, 0)).toBe('jump');
    expect(classifyStep(steps, 0.4, 0)).toBe('');
  });
  it('forgets the gesture after 30 quiet frames', () => {
    const steps = [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3];
    classifyStep(steps, 0, 31);
    expect(steps).toEqual([]);
  });
});

describe('16. a link loads its design first, the context after (progressive load)', () => {
  const part = (id: string, cls: string) => ({ id, name: id, class: cls, link: 'l', transform: { t: [0, 0, 0] }, mesh: `parts/${id}.glb` }) as unknown as MPart;
  const asm = (id: string, parts: MPart[], children: MAssembly[] = []) => ({ id, name: id, links: [], joints: [], parts, children }) as unknown as MAssembly;
  const head = asm('hunter_head', [part('gimbal', 'structure')], [asm('visor_asm', [part('visor', 'shell')])]);
  const root = asm('r3x', [part('body_shell', 'shell'), part('column', 'structure')], [head]);
  (root as unknown as { designs: unknown[] }).designs = [
    { id: 'hunter', name: 'Head', assemblies: ['hunter_head'], deep: true },
    { id: 'kit', name: 'Kit', assemblies: ['r3x'], only: ['shell'] },
  ];
  const ids = (f: ReturnType<typeof firstParts>) => [...(f?.parts ?? [])].map((p) => p.id).sort();
  it("a library link: the design's parts (deep: its subtree), and its assemblies' fasteners", () => {
    const f = firstParts(root, 'hunter');
    expect(ids(f)).toEqual(['gimbal', 'visor']);
    expect([...f!.nodes].map((n) => n.id).sort()).toEqual(['hunter_head', 'visor_asm']);
  });
  it("a design's class filter applies (kit: shells of its own assembly only)", () => {
    expect(ids(firstParts(root, 'kit'))).toEqual(['body_shell']);
  });
  it('no link: the shells, anywhere in the tree', () => {
    expect(ids(firstParts(root, ''))).toEqual(['body_shell', 'visor']);
  });
  it('anything else (or an unknown design): everything at once', () => {
    expect(firstParts(root, null)).toBeNull();
    expect(firstParts(root, 'nope')).toBeNull();
  });
});

describe('17. a published snapshot asks only for the optional files it has', () => {
  it('a listed folder: only listed files; the deepest listing wins; unlisted folders ask', () => {
    notePublished('http://h/data/index.json', ['checks.json', 'asm/manifest.json']);
    notePublished('http://h/data/asm/manifest.json', ['uvtransfer.json']);
    expect(mayExist('http://h/data/asm/uvtransfer.json')).toBe(true);
    expect(mayExist('http://h/data/asm/base/uvtransfer.json')).toBe(false);
    expect(mayExist('http://h/data/asm/interference.json?x=1')).toBe(false);
    expect(mayExist('http://h/data/checks.json')).toBe(true);
    expect(mayExist('http://dev/mech/out/asm/uvtransfer.json')).toBe(true);
  });
});
