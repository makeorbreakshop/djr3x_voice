// The viewer's mesh packs (publish-viewer.mjs): one file per published assembly folder holding every
// overview display GLB (parts and fasteners) of the assembly tree in that folder, and each one's
// transferred UVs (`<mesh>.uv.bin`), instead of ~1000 separate requests.
//
//   <assembly>/meshes.pack   gzip( GLB | uv.bin | GLB | ... )   each GLB re-encoded with
//                            EXT_meshopt_compression, QUANTIZE method: the codec over the build's
//                            already-quantized int16 positions and uint16/32 indices, no filters,
//                            no reordering or re-quantization; the viewer decodes the same arrays
//   manifest.json  + "pack": { "file": "meshes.pack", "format": "r3x.meshpack.1", "encoding": "gzip",
//                              "entries": { "<path from the folder>": [offset, length], ... } }
//
// gzip inside the file, not by the host: Cloudflare (and most static hosts) do not compress
// application/octet-stream or model/gltf-binary, so the publisher does it once, at level 9, and the
// viewer inflates it with DecompressionStream (it sniffs the gzip magic, so a host that does add
// Content-Encoding still works). Full-detail meshes (`mesh_full`) stay separate files: they are
// fetched one by one, only for the parts in focus. A referenced assembly published on its own
// (the droid's head: ../hunter_head) is not copied in: the viewer reads it from that folder's pack.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression } from '@gltf-transform/extensions';
import { MeshoptDecoder, MeshoptEncoder } from 'meshoptimizer';

let io = null;
async function codec() {
  if (io) return io;
  await MeshoptEncoder.ready;
  await MeshoptDecoder.ready;
  io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });
  return io;
}

/**
 * A display GLB with its buffers meshopt-encoded, losslessly (no filters). Indices go through the
 * codec's INDICES mode, not TRIANGLES: TRIANGLES may rotate a triangle's corners (1,0,2 -> 0,2,1),
 * the same triangle but not the same bytes, and the welded, creased result then differs in order.
 * glTF-Transform picks TRIANGLES for triangle primitives, so the primitives are written as LINES
 * and set back to triangles (glTF's default mode) in the written JSON.
 */
export async function meshoptGlb(bytes) {
  const io = await codec();
  const doc = await io.readBinary(new Uint8Array(bytes));
  const prims = doc.getRoot().listMeshes().flatMap((m) => m.listPrimitives());
  if (prims.some((p) => p.getMode() !== 4)) throw new Error('mesh pack: only triangle meshes');
  for (const p of prims) p.setMode(1);
  doc.createExtension(EXTMeshoptCompression).setRequired(true).setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
  const glb = Buffer.from(await io.writeBinary(doc));
  // the GLB's JSON chunk: every primitive back to the default mode (TRIANGLES)
  const jsonLen = glb.readUInt32LE(12);
  const json = JSON.parse(glb.subarray(20, 20 + jsonLen).toString('utf8').trimEnd());
  for (const m of json.meshes ?? []) for (const p of m.primitives ?? []) delete p.mode;
  // and the nodes' transforms exactly as built: glTF-Transform drops a near-identity one (a 3.6e-15
  // translation), which moves the dequantized positions by an ulp
  const src = JSON.parse(Buffer.from(bytes).subarray(20, 20 + Buffer.from(bytes).readUInt32LE(12)).toString('utf8').trimEnd());
  if ((src.nodes ?? []).length !== (json.nodes ?? []).length) throw new Error('mesh pack: node count changed');
  (src.nodes ?? []).forEach((n, i) => {
    for (const k of ['translation', 'rotation', 'scale', 'matrix']) {
      if (k in n) json.nodes[i][k] = n[k];
      else delete json.nodes[i][k];
    }
  });
  let j = Buffer.from(JSON.stringify(json), 'utf8');
  j = Buffer.concat([j, Buffer.alloc((4 - (j.length % 4)) % 4, 0x20)]);
  const rest = glb.subarray(20 + jsonLen);
  const head = Buffer.alloc(20);
  head.writeUInt32LE(0x46546c67, 0);
  head.writeUInt32LE(2, 4);
  head.writeUInt32LE(20 + j.length + rest.length, 8);
  head.writeUInt32LE(j.length, 12);
  head.writeUInt32LE(0x4e4f534a, 16);
  const out = Buffer.concat([head, j, rest]);
  await sameData(bytes, out);
  return out;
}

/** Throw unless `b` decodes to the same accessor bytes as `a` (the pack must never change a mesh). */
async function sameData(a, b) {
  const io = await codec();
  const [da, db] = [await io.readBinary(new Uint8Array(a)), await io.readBinary(new Uint8Array(b))];
  const acc = (d) => d.getRoot().listAccessors().map((x) => Buffer.from(x.getArray().buffer, x.getArray().byteOffset, x.getArray().byteLength));
  const [aa, ab] = [acc(da), acc(db)];
  if (aa.length !== ab.length || aa.some((x, i) => !x.equals(ab[i]))) throw new Error('mesh pack: an accessor changed');
}

/**
 * Every overview mesh (and its uv.bin) the tree under `dir`/manifest.json draws, as paths from `dir`,
 * in pack order: the shells first (the viewer's landing view waits for them; loadorder.ts), then the
 * other parts, then the fasteners. The viewer streams the pack, so what is near the front is ready first.
 */
export function packFiles(dir) {
  const rank = new Map(); // path -> 0 shell, 1 part, 2 fastener (the lowest wins)
  const put = (f, r) => rank.set(f, Math.min(r, rank.get(f) ?? 9));
  const walk = (node, base) => {
    for (const p of node.parts ?? []) if (p.mesh) put(path.relative(dir, path.resolve(base, p.mesh)), p.class === 'shell' ? 0 : 1);
    for (const f of node.fasteners ?? []) if (f.mesh) put(path.relative(dir, path.resolve(base, f.mesh)), 2);
    for (const c of node.children ?? []) {
      if (c.ref) {
        const m = path.resolve(base, c.ref);
        if (path.relative(dir, m).startsWith('..') || !fs.existsSync(m)) continue; // published on its own
        walk(JSON.parse(fs.readFileSync(m, 'utf8')).root, path.dirname(m));
      } else walk(c, base);
    }
  };
  walk(JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8')).root, dir);
  const files = [];
  for (const f of [...rank.keys()].sort((a, b) => rank.get(a) - rank.get(b) || (a < b ? -1 : 1))) {
    if (!fs.existsSync(path.join(dir, f))) continue;
    files.push(f);
    const uv = f.replace(/\.glb$/, '.uv.bin');
    if (fs.existsSync(path.join(dir, uv))) files.push(uv);
  }
  return files;
}

/** A content hash, short (cache-busting names and `mesh_sig`). */
export const hash12 = (...bufs) => {
  const h = crypto.createHash('sha256');
  for (const b of bufs) h.update(b);
  return h.digest('hex').slice(0, 12);
};

/**
 * Pack the published folder `dir` in place: write meshes.<hash>.pack (content-hashed: cache it for
 * good), add `pack` to its manifest.json, and remove the files it holds. Returns sizes for the log.
 */
export async function packFolder(dir) {
  const files = packFiles(dir);
  if (!files.length) return null;
  const parts = [];
  const entries = {};
  let off = 0;
  let raw = 0;
  for (const f of files) {
    const src = fs.readFileSync(path.join(dir, f));
    raw += src.length;
    const b = f.endsWith('.glb') ? await meshoptGlb(src) : src;
    entries[f.split(path.sep).join('/')] = [off, b.length];
    parts.push(b);
    off += b.length;
  }
  const inflated = Buffer.concat(parts);
  const body = zlib.gzipSync(inflated, { level: 9 });
  const file = `meshes.${hash12(body)}.pack`;
  fs.writeFileSync(path.join(dir, file), body);
  const mpath = path.join(dir, 'manifest.json');
  const m = JSON.parse(fs.readFileSync(mpath, 'utf8'));
  m.pack = { file, format: 'r3x.meshpack.1', encoding: 'gzip', size: inflated.length, entries };
  fs.writeFileSync(mpath, JSON.stringify(m));
  for (const f of files) fs.rmSync(path.join(dir, f));
  return { files: files.length, raw, meshopt: off, gzip: body.length };
}
