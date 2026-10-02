import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { mergeVertices, toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';
import { EDGE_ANGLE, prepareGeometry, processGeometry, toGeometry } from '../src/workbench/geomwork';

// The geometry the workbench drew before the worker (welded, creased at 30 deg, its feature edges).
const bytes = (a: ArrayLike<number>) => Array.from(new Uint8Array(new Float32Array(a as ArrayLike<number>).buffer));

function before(g: THREE.BufferGeometry) {
  const geo = toCreasedNormals(mergeVertices(g, 1e-4), (30 * Math.PI) / 180);
  return { geo, edges: new THREE.EdgesGeometry(geo, EDGE_ANGLE).attributes.position.array };
}

describe('geomwork', () => {
  const box = () => {
    const g = new THREE.BufferGeometry(); // positions only, as meshGeometry gives them
    g.setAttribute('position', new THREE.BoxGeometry(1, 2, 3, 2, 2, 2).toNonIndexed().attributes.position.clone());
    return g;
  };
  it('makes the same geometry and edges as the main-thread path', () => {
    const ref = before(box());
    const b = box();
    const out = processGeometry({ position: b.attributes.position.array as Float32Array, index: null, uv: null });
    expect(bytes(out.attributes.position.array)).toEqual(bytes(ref.geo.attributes.position.array));
    expect(bytes(out.attributes.normal.array)).toEqual(bytes(ref.geo.attributes.normal.array));
    expect(bytes(out.edges!)).toEqual(bytes(ref.edges));
  });
  it('matches on facets between the crease and the edge angle (a box would hide a changed angle)', () => {
    // 10 sides: 36 deg between facets, above the 30 deg crease (kept hard) and below the 40 deg edge
    // angle (no line); 8 sides: 45 deg, above both. The reference states both angles itself, so a
    // change to either in geomwork.ts shows here.
    for (const sides of [10, 8]) {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.CylinderGeometry(0.5, 0.5, 1, sides, 1, true).toNonIndexed().attributes.position.clone());
      const geo = toCreasedNormals(mergeVertices(g.clone(), 1e-4), (30 * Math.PI) / 180);
      const edges = new THREE.EdgesGeometry(geo, 40).attributes.position.array;
      const out = processGeometry({ position: g.attributes.position.array as Float32Array, index: null, uv: null });
      expect(bytes(out.attributes.normal.array)).toEqual(bytes(geo.attributes.normal.array));
      expect(bytes(out.edges!)).toEqual(bytes(edges));
    }
  });
  it('keeps per-corner UVs and falls back to the main thread without a Worker (Node)', async () => {
    const b = new THREE.BoxGeometry(1, 1, 1);
    const n = b.index!.count;
    const uv = new Float32Array(n * 2).map((_, i) => (i % 7) / 7);
    const g = toGeometry(await prepareGeometry({ position: b.attributes.position.array as Float32Array, index: b.index!.array as Uint16Array, uv }));
    expect(g.attributes.uv).toBeDefined();
    expect(g.attributes.normal.count).toBe(g.attributes.position.count);
    expect((g.userData.edges as Float32Array).length).toBeGreaterThan(0);
  });
});
