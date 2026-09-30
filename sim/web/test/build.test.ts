import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { hornMatrix, linkMatrices, rodMatrix, solveRod } from '../src/workbench/kinematics';
import { defaultVariants, flatten, hiddenByVariants, joinUrl, resolveTree, firstStep, type MAssembly, type MJoint, type MLink, type MLinkage, type Manifest } from '../src/workbench/manifest';

const links: MLink[] = [
  { id: 'base', name: 'Base', joint: null },
  { id: 'arm', name: 'Arm', joint: 'hinge' },
];
const hinge: MJoint = {
  id: 'hinge', name: 'Hinge', type: 'revolute', parent_link: 'base', child_link: 'arm',
  pivot: [0, 0, 0], axis: [1, 0, 0], unit: 'deg', limits: { min: -30, max: 30 }, profile_joint: 'head_tilt',
};
// Same linkage as mech/workbench/tests/test_manifest.py: the two solvers must agree.
const rod: MLinkage = {
  id: 'rod', kind: 'push_rod', servo: 'servo',
  horn: { link: 'base', centre: [0, 10, -30], axis: [0, 1, 0], radius: 10, zero_dir: [1, 0, 0], ball_offset: 2 },
  ground: { link: 'arm', point: [0, 12, 20] }, rod_length: Math.sqrt(2600), parts: [],
};

describe('build kinematics', () => {
  it('+deg about +X tips the +Z end down (show/SPEC.md signs)', () => {
    const m = linkMatrices(links, [hinge], { hinge: 90 }).get('arm')!;
    const z = new THREE.Vector3(0, 0, 1).transformDirection(m);
    expect(z.y).toBeCloseTo(-1, 9);
  });

  it('the closed-form rod keeps its length and is 0 at the zero pose', () => {
    for (const v of [-20, -5, 0, 5, 15]) {
      const ms = linkMatrices(links, [hinge], { hinge: v });
      const s = solveRod(rod, ms.get('base')!, ms.get('arm')!)!;
      expect(s).not.toBeNull();
      expect(s.a.distanceTo(s.b)).toBeCloseTo(rod.rod_length, 6);
      if (v === 0) expect(s.servoDeg).toBeCloseTo(0, 9);
    }
  });

  it('reports an unreachable pose instead of clamping', () => {
    const ms = linkMatrices(links, [hinge], { hinge: -90 });
    expect(solveRod(rod, ms.get('base')!, ms.get('arm')!)).toBeNull();
  });

  it('horn and rod matrices move the zero-pose parts onto the solved balls', () => {
    const z = linkMatrices(links, [hinge], {});
    const s0 = solveRod(rod, z.get('base')!, z.get('arm')!)!;
    const ms = linkMatrices(links, [hinge], { hinge: 12 });
    const s = solveRod(rod, ms.get('base')!, ms.get('arm')!)!;
    const horn = hornMatrix(rod, ms.get('base')!, s.servoDeg);
    // the horn's zero-pose ball, turned by the servo angle, is the solved ball
    expect(s0.a.clone().applyMatrix4(horn).distanceTo(s.a)).toBeLessThan(1e-6);
    const r = rodMatrix(s0.a, s0.b, s.a, s.b);
    expect(s0.b.clone().applyMatrix4(r).distanceTo(s.b)).toBeLessThan(1e-6);
  });
});

describe('build manifest', () => {
  const asm = (id: string, extra: Partial<MAssembly> = {}): MAssembly => ({ id, name: id, links: [], parts: [], joints: [], ...extra });

  it('resolves a ChildRef against the parent directory and keeps its mount', async () => {
    const child: Manifest = { schema: 'r3x.mech.manifest', version: 1, generated_at: '', root: asm('head', { parts: [] }) };
    const root = asm('droid', {
      children: [{ ref: '../hunter_head/manifest.json', id: 'head', name: 'Hunter head', mount: { parent_link: 'neck', transform: { t: [0, 738, 0] } } }],
    });
    const seen: string[] = [];
    const out = await resolveTree(root, '/mech/out/kit/', async (u) => (seen.push(u), child));
    expect(seen).toEqual(['/mech/out/hunter_head/manifest.json']);
    const kid = out.children![0] as MAssembly;
    expect(kid.base).toBe('/mech/out/hunter_head/');
    expect(kid.mount?.transform?.t).toEqual([0, 738, 0]);
    expect(kid.name).toBe('Hunter head');
    expect(flatten(out).map((x) => x.path.map((p) => p.id).join('>'))).toEqual(['droid', 'droid>head']);
  });

  it('a missing child is shown as not built rather than failing the tree', async () => {
    const out = await resolveTree(asm('droid', { children: [{ ref: 'x/manifest.json', id: 'x' }] }), '/mech/out/kit/', async () => {
      throw new Error('404');
    });
    expect((out.children![0] as MAssembly).name).toMatch(/not built/);
  });

  it('variants: defaults, and hiding the options not picked', () => {
    const a = asm('head', {
      variants: [
        { id: 'eyes_a', group: 'eyes', name: 'A', parts: ['ea'] },
        { id: 'eyes_b', group: 'eyes', name: 'B', parts: ['eb'], default: true },
      ],
    });
    const pick = defaultVariants(a);
    expect(pick).toEqual({ eyes: 'eyes_b' });
    expect([...hiddenByVariants(a, pick)]).toEqual(['ea']);
  });

  it('first step per part, and URL joining', () => {
    const a = asm('h', { steps: [{ id: 's1', n: 1, title: '', parts: ['p'] }, { id: 's2', n: 2, title: '', parts: ['p', 'q'] }] });
    expect([...firstStep(a)]).toEqual([['p', 0], ['q', 1]]);
    expect(joinUrl('/mech/out/hunter_head/', 'parts/a.glb')).toBe('/mech/out/hunter_head/parts/a.glb');
  });
});
