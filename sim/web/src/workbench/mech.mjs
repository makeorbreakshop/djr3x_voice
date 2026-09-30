// Dev-server route for Build mode (vite.config.ts registers the plugin):
//   GET /mech/out/<path> -> the file under the repo's mech/out/ (manifest.json, GLB, STL, 3MF)
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
};

export function mechOut() {
  return {
    name: 'r3x-mech-out',
    configureServer(server) {
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
        if (ext !== 'json' && ext !== 'glb') {
          res.setHeader('content-disposition', `attachment; filename="${path.basename(full)}"`);
        }
        fs.createReadStream(full).pipe(res);
      });
    },
  };
}
