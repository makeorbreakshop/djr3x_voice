// Instructions' step animation (src/workbench/sequence.ts): the order things go in, and that every fastener
// only ever moves along its own shank axis.
import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { crosswise, fastenerPose, fastenerTravel, planSequence, type SeqFastener } from '../src/workbench/sequence';
import type { MSpec } from '../src/workbench/manifest';

const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
const down = v(0, -1, 0);
const screw: MSpec = { type: 'shcs', thread: 'M3', length_mm: 8 };
const nut: MSpec = { type: 'lock_nut', thread: 'M3' };

describe('step sequence', () => {
  it('goes round a bolt circle crosswise (1-3-2-4)', () => {
    const ring = [[10, 0], [0, 10], [-10, 0], [0, -10]].map(([x, z], i) => ({ id: `f${i}`, at: v(x, 0, z), axis: down }));
    const order = crosswise(ring).map((f) => f.at.toArray().map(Math.round).join());
    // each next screw is opposite the one before, then the other diagonal
    expect(order.length).toBe(4);
    const pts = crosswise(ring).map((f) => f.at);
    expect(pts[0].distanceTo(pts[1])).toBeCloseTo(20);
    expect(pts[2].distanceTo(pts[3])).toBeCloseTo(20);
  });

  it('drops each servo into its pocket, then drives its four screws, before the next servo', () => {
    const f = (id: string, servo: string, x: number, z: number): SeqFastener => ({ id, spec: screw, joins: [servo, 'plate'], at: v(x, 10, z), axis: down });
    const fasteners = [f('a1', 'servo_l', -30, -5), f('a2', 'servo_l', -10, -5), f('a3', 'servo_l', -30, 5), f('a4', 'servo_l', -10, 5),
      f('b1', 'servo_r', 10, -5), f('b2', 'servo_r', 30, -5), f('b3', 'servo_r', 10, 5), f('b4', 'servo_r', 30, 5)];
    const { items } = planSequence([{ id: 'servo_l', group: 'servo', at: v(-20, 0, 0) }, { id: 'servo_r', group: 'servo', at: v(20, 0, 0) }],
      fasteners, (id) => id === 'plate');
    const seq = items.map((it) => it.ids.join('+'));
    expect(seq[0]).toBe('servo_l');
    expect(new Set(seq.slice(1, 5))).toEqual(new Set(['a1', 'a2', 'a3', 'a4']));
    expect(seq[5]).toBe('servo_r');
    expect(new Set(seq.slice(6))).toEqual(new Set(['b1', 'b2', 'b3', 'b4']));
    // in time order, each a little after the one before
    for (let i = 1; i < items.length; i++) expect(items[i].start).toBeGreaterThan(items[i - 1].start);
  });

  it('brings a like pair in together when it has no screws of its own, and a nut after its screw', () => {
    const { items } = planSequence([{ id: 'brg_l', group: 'brg', at: v(-5, 0, 0) }, { id: 'brg_r', group: 'brg', at: v(5, 0, 0) }],
      [{ id: 's', spec: screw, joins: ['plate'], at: v(0, 5, 0), axis: down }, { id: 'n', spec: nut, joins: ['plate'], at: v(0, -5, 0), axis: down }],
      () => true);
    expect(items.map((it) => it.ids.join('+'))).toEqual(['s', 'n', 'brg_l+brg_r']);
  });

  it('moves a fastener only along its own axis: a screw from the head side, a nut from the far side', () => {
    const seat = new THREE.Matrix4().compose(v(5, 2, 1), new THREE.Quaternion().setFromUnitVectors(v(0, 0, 1), down), v(1, 1, 1));
    const start = new THREE.Vector3().setFromMatrixPosition(fastenerPose(seat, screw, 1));
    expect(start.toArray().map((x) => +x.toFixed(6))).toEqual([5, 2 + fastenerTravel(screw).mm, 1]);
    const n = new THREE.Vector3().setFromMatrixPosition(fastenerPose(seat, nut, 1));
    expect(n.y).toBeLessThan(2);
    expect(new THREE.Vector3().setFromMatrixPosition(fastenerPose(seat, screw, 0)).toArray()).toEqual([5, 2, 1]);
  });
});

// Every fastener of every step in the built manifests (mech/out, local only: skipped when absent): its start
// pose is on its shank axis (features.shank, or an insert's bore) and the path to its seat runs along it.
// (node's fs through a dynamic import, as test/electronics.test.ts does: the project has no node typings)
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { existsSync(u: URL): boolean; readFileSync(u: URL, enc: string): string };
const manifests = ['column_internals', 'hunter_head'].map((n) => new URL(`../../../mech/out/${n}/manifest.json`, import.meta.url)).filter((u) => fs.existsSync(u));

describe.skipIf(!manifests.length)('fastener paths in the built manifests', () => {
  for (const file of manifests) {
    it(`${file.pathname.split('/').at(-2)}: on the shank axis, collinear with it`, () => {
      const root = JSON.parse(fs.readFileSync(file, 'utf8')).root;
      const inStep = new Set<string>((root.steps ?? []).flatMap((s: { fasteners?: string[] }) => s.fasteners ?? []));
      let n = 0;
      for (const f of root.fasteners ?? []) {
        if (!f.transform || !inStep.has(f.id)) continue;
        const ax = f.features?.shank ?? f.features?.bore;
        if (!ax) continue;
        const seat = new THREE.Matrix4().compose(v(...(f.transform.t as [number, number, number])),
          new THREE.Quaternion(...((f.transform.q ?? [0, 0, 0, 1]) as [number, number, number, number])), v(1, 1, 1));
        const p = v(...(ax.p as [number, number, number]));
        const d = v(...(ax.d as [number, number, number])).normalize();
        const a = new THREE.Vector3().setFromMatrixPosition(fastenerPose(seat, f.spec, 1));
        const b = new THREE.Vector3().setFromMatrixPosition(seat);
        const off = a.clone().sub(p);
        expect(off.sub(d.clone().multiplyScalar(off.dot(d))).length(), f.id).toBeLessThan(0.05);
        const path = a.clone().sub(b).normalize();
        expect(Math.abs(path.dot(d)), f.id).toBeGreaterThan(0.9999);
        n++;
      }
      expect(n).toBeGreaterThan(20);
    });
  }
});
