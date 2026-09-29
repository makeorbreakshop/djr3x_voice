// Copy three's Draco decoder and Basis (KTX2) transcoder into public/ so the GLB loads offline.
import { cpSync, mkdirSync } from 'node:fs';
mkdirSync('public/draco', { recursive: true });
cpSync('node_modules/three/examples/jsm/libs/draco/gltf', 'public/draco', { recursive: true });
mkdirSync('public/basis', { recursive: true });
cpSync('node_modules/three/examples/jsm/libs/basis', 'public/basis', {
  recursive: true,
  filter: (src) => !src.endsWith('README.md'),
});
