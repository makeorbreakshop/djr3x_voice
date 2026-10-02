#!/usr/bin/env node
/**
 * The Original rig's surfaces for the texture transfer (mech/workbench/uvtransfer.py): every primitive of
 * public/model/r3x.glb at its rest pose, in the droid's frame (mm, +Y up), with its UVs, its material and which
 * atlas that material samples. Writes mech/out/.cache/original_surfaces.json (gitignored).
 *
 *   node scripts/export_original_surfaces.mjs
 */
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import draco from 'draco3dgltf';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.resolve(WEB, '../../mech/out/.cache/original_surfaces.json');
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'draco3d.decoder': await draco.createDecoderModule() });
const doc = await io.read(path.join(WEB, 'public/model/r3x.glb'));

const mul = (a, b) => {
  const o = new Array(16).fill(0);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) o[j * 4 + i] += a[k * 4 + i] * b[j * 4 + k];
  return o;
};
const world = (n) => {
  let m = n.getMatrix();
  for (let p = n.getParentNode(); p; p = p.getParentNode()) m = mul(p.getMatrix(), m);
  return m;
};
const xf = (m, v, w = 1) => [0, 1, 2].map((i) => m[i] * v[0] + m[4 + i] * v[1] + m[8 + i] * v[2] + m[12 + i] * w);

const prims = [];
for (const n of doc.getRoot().listNodes()) {
  const mesh = n.getMesh();
  if (!mesh) continue;
  const M = world(n);
  for (const p of mesh.listPrimitives()) {
    const pos = p.getAttribute('POSITION'), uv = p.getAttribute('TEXCOORD_0'), nrm = p.getAttribute('NORMAL'), idx = p.getIndices();
    if (!pos || !uv) continue;
    const mat = p.getMaterial();
    const atlas = mat?.getBaseColorTexture()?.getName() ?? '';
    const P = [], N = [], U = [];
    const v = [0, 0, 0], t = [0, 0];
    for (let i = 0; i < pos.getCount(); i++) {
      P.push(...xf(M, pos.getElement(i, v)).map((x) => +(x * 1000).toFixed(3)));
      U.push(...uv.getElement(i, t).map((x) => +x.toFixed(6)));
      if (nrm) N.push(...xf(M, nrm.getElement(i, v), 0).map((x) => +x.toFixed(4)));
    }
    prims.push({ node: n.getName(), material: mat?.getName() ?? '', atlas, P, N, U, F: idx ? Array.from(idx.getArray()) : [...Array(pos.getCount()).keys()] });
  }
}
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify({ source: 'sim/web/public/model/r3x.glb', units: 'mm', prims }));
console.log(`${prims.length} primitives -> ${OUT}`);
