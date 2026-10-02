// Publish the Build viewer: the viewer build (dist-viewer/) plus a snapshot of the workbench
// output it reads, as one static folder that can be hosted under any prefix.
//   npm run publish:viewer -- [--out <dir>] [--exports] [--only r3x_droid,hunter_head]
//
// The snapshot is what the viewer fetches and no more: index.json, checks.json, and each
// assembly's JSON, display GLBs and transferred UVs. The printable exports (STL, 3MF) stay
// behind and their links are dropped from the manifests, unless --exports.
//
// Then each assembly folder's overview meshes are packed into one file (scripts/meshpack.mjs:
// meshes.pack, meshopt + gzip, and an additive `pack` key in that folder's manifest.json; see
// mech/workbench/SCHEMA.md), so a first view is a handful of requests, not ~1000. The viewer reads
// the pack when a manifest has one and the per-part files otherwise (the dev server, mech/out, is
// never packed). --no-pack keeps the per-part files.
//
// Caching: the packs are content-named (meshes.<hash>.pack) and every mesh URL carries its content hash
// (?v=mesh_sig, filled in here where the build left it out), and Vite names the scripts by content, so
// the host may send `Cache-Control: public, max-age=31536000, immutable` for assets/*, *.pack, *.glb and
// *.bin; viewer.html and the JSON (index, manifests) must revalidate (`no-cache`; the viewer also
// fetches them with cache: 'no-store'). Each manifest lists the optional files its folder has
// (`published_files`) and index.json those at the root (`files`): the viewer asks for no others.
//
// The output holds meshes converted from third-party designs (mech/README.md "Licensing"):
// it is gitignored here, and where it is hosted is the publisher's call.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { hash12, packFolder } from './meshpack.mjs';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const MECH_OUT = path.resolve(WEB, '../../mech/out');
const args = process.argv.slice(2);
const flag = (n) => args.includes(n);
const opt = (n) => (args.includes(n) ? args[args.indexOf(n) + 1] : null);
const OUT = path.resolve(opt('--out') ?? path.join(WEB, 'dist-viewer'));
const only = opt('--only')?.split(',').filter(Boolean) ?? null;
const withExports = flag('--exports');
const KEEP = new Set(['.json', '.glb', '.bin', ...(withExports ? ['.stl', '.3mf'] : [])]);

const indexPath = path.join(MECH_OUT, 'index.json');
if (!fs.existsSync(indexPath)) {
  console.error(`${indexPath} missing: build first (cd mech && .venv/bin/python -m workbench build kit)`);
  process.exit(1);
}
const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
const entries = index.assemblies.filter((a) => !only || only.includes(a.id));
if (!entries.length) {
  console.error(`nothing to publish: ${only ? `no assembly among ${only.join(', ')}` : 'the index is empty'}`);
  process.exit(1);
}

if (!flag('--no-build')) {
  execFileSync('npx', ['vite', 'build', '--mode', 'viewer', '--outDir', OUT, '--emptyOutDir'], { cwd: WEB, stdio: 'inherit' });
}

/** Drop the printable-export links from a manifest (every part's `export`, at any depth). */
function stripExports(node) {
  if (Array.isArray(node)) return node.forEach(stripExports);
  if (!node || typeof node !== 'object') return;
  if ('export' in node && 'mesh' in node) delete node.export;
  for (const v of Object.values(node)) stripExports(v);
}

const data = path.join(OUT, 'data');
fs.rmSync(data, { recursive: true, force: true });
fs.mkdirSync(data, { recursive: true });
let files = 0;
let bytes = 0;
function copyTree(src, dst) {
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (e.name.startsWith('.')) continue;
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) {
      copyTree(s, d);
      continue;
    }
    const ext = path.extname(e.name).toLowerCase();
    if (!KEEP.has(ext)) continue;
    fs.mkdirSync(dst, { recursive: true });
    if (ext === '.json' && !withExports) {
      const doc = JSON.parse(fs.readFileSync(s, 'utf8'));
      stripExports(doc);
      fs.writeFileSync(d, JSON.stringify(doc));
    } else {
      fs.copyFileSync(s, d);
    }
    files++;
    bytes += fs.statSync(d).size;
  }
}
for (const a of entries) copyTree(path.join(MECH_OUT, path.dirname(a.manifest)), path.join(data, path.dirname(a.manifest)));
const checks = path.join(MECH_OUT, 'checks.json');
if (fs.existsSync(checks)) fs.copyFileSync(checks, path.join(data, 'checks.json'));
// `files`: the optional files at the data root that exist (the viewer does not ask for the others)
fs.writeFileSync(path.join(data, 'index.json'), JSON.stringify({ ...index, assemblies: entries, published: new Date().toISOString(),
  files: fs.existsSync(checks) ? ['checks.json'] : [] }));

/**
 * Cache-safe URLs: a part without a `mesh_sig` gets one (its GLBs' content hash), which the viewer puts
 * on the mesh and full-detail URLs (?v=), so a host can cache data files for good; the packs are
 * content-named. And the optional files the folder does have (`published_files`): the viewer asks only
 * for those (each folder's uvtransfer.json, interference.json: ~22 guaranteed 404s a load before).
 */
function finishManifest(dir) {
  const mpath = path.join(dir, 'manifest.json');
  if (!fs.existsSync(mpath)) return;
  const m = JSON.parse(fs.readFileSync(mpath, 'utf8'));
  const read = (f) => (f && fs.existsSync(path.join(dir, f)) ? fs.readFileSync(path.join(dir, f)) : Buffer.alloc(0));
  const walk = (node) => {
    for (const p of node.parts ?? []) if (!p.mesh_sig && p.mesh) p.mesh_sig = hash12(read(p.mesh), read(p.mesh_full));
    for (const c of node.children ?? []) if (!c.ref) walk(c);
  };
  walk(m.root);
  const opt = [];
  const scan = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const f = path.join(d, e.name);
      if (e.isDirectory()) scan(f);
      else if (e.name.endsWith('.json') && e.name !== 'manifest.json') opt.push(path.relative(dir, f).split(path.sep).join('/'));
    }
  };
  scan(dir);
  m.published_files = opt.sort();
  fs.writeFileSync(mpath, JSON.stringify(m));
}
for (const a of entries) finishManifest(path.join(data, path.dirname(a.manifest)));

const packed = [];
if (!flag('--no-pack')) {
  for (const a of entries) {
    const r = await packFolder(path.join(data, path.dirname(a.manifest)));
    if (!r) continue;
    files -= r.files - 1;
    bytes += r.gzip - r.raw;
    packed.push(`${a.id} ${r.files} files ${(r.raw / 1e6).toFixed(1)} MB -> meshopt ${(r.meshopt / 1e6).toFixed(1)} MB -> gzip ${(r.gzip / 1e6).toFixed(1)} MB`);
  }
}
for (const l of packed) console.log(`packed ${l}`);
console.log(`published ${entries.map((a) => a.id).join(', ')}: ${files} files, ${(bytes / 1e6).toFixed(1)} MB of data -> ${OUT}`);
