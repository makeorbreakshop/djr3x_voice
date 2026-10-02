// Dev-server route for Build mode (vite.config.ts registers the plugin):
//   GET /mech/out/<path> -> the file under the repo's mech/out/ (manifest.json, GLB, uv.bin, STL, 3MF)
// mech/out/ is written by the workbench (`mech/.venv/bin/python -m workbench build <asm>`).
// It is gitignored and holds converted third-party meshes, so it is served only by the dev
// server and never bundled into a build.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = fileURLToPath(new URL('../../../../mech/out/', import.meta.url));
const TYPES = {
  json: 'application/json', glb: 'model/gltf-binary', stl: 'model/stl',
  '3mf': 'model/3mf', png: 'image/png',
  // <mesh>.uv.bin: the Original's UVs transferred onto a part (mech/workbench/uvtransfer.py)
  bin: 'application/octet-stream',
};

export function mechOut() {
  return {
    name: 'r3x-mech-out',
    configureServer(server) {
      // Live reload: when the workbench rewrites a manifest (`workbench build <asm> --sketch --watch`),
      // tell the page; Build reloads the open assembly in place (camera, joints and picks kept).
      let timer = null;
      const changed = new Set();
      try {
        fs.watch(OUT, { recursive: true }, (_e, name) => {
          const rel = String(name || '').split(path.sep).join('/');
          if (!rel.endsWith('manifest.json')) return;
          changed.add(rel);
          clearTimeout(timer);
          timer = setTimeout(() => {
            server.ws.send({ type: 'custom', event: 'r3x:mech-manifest', data: { paths: [...changed], t: Date.now() } });
            changed.clear();
          }, 120);
        });
      } catch (e) {
        server.config.logger.warn(`mech/out not watched (${e.message}): Build will not live-reload`);
      }
      server.middlewares.use('/mech/out/', (req, res, next) => {
        const rel = decodeURIComponent((req.url || '').split('?')[0]).replace(/^\/+/, '');
        const full = path.resolve(OUT, rel);
        if (!rel || !full.startsWith(OUT) || rel.split('/').some((s) => s.startsWith('.'))) return next();
        const ext = full.split('.').pop().toLowerCase();
        if (!(ext in TYPES) || !fs.existsSync(full) || !fs.statSync(full).isFile()) {
          res.statusCode = 404;
          return res.end();
        }
        res.setHeader('content-type', TYPES[ext]);
        res.setHeader('cache-control', 'no-store');
        if (ext !== 'json' && ext !== 'glb' && ext !== 'bin') {
          res.setHeader('content-disposition', `attachment; filename="${path.basename(full)}"`);
        }
        fs.createReadStream(full).pipe(res);
      });
    },
  };
}
