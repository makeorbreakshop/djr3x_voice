// Copy three's Draco decoder and Basis (KTX2) transcoder into public/ so the GLB loads
// offline: the model's meshes are Draco, its textures KTX2 (see scripts/pack-model.mjs).
import { cpSync, mkdirSync } from 'node:fs';
mkdirSync('public/draco', { recursive: true });
cpSync('node_modules/three/examples/jsm/libs/draco/gltf', 'public/draco', { recursive: true });
mkdirSync('public/basis', { recursive: true });
for (const f of ['basis_transcoder.js', 'basis_transcoder.wasm']) {
  cpSync(`node_modules/three/examples/jsm/libs/basis/${f}`, `public/basis/${f}`);
}
