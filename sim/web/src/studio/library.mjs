// Dev-server routes for Studio's audio lane (vite.config.ts registers the plugin):
//   GET /studio/library        -> [{name, url, bpm?, first_beat_s?, beats?}] for the music library
//   GET /studio/audio/<file>   -> the audio file
// The library is MUSIC_DIR, else the repo's audio/music (as r3x-music); beat grids come from
// the r3x-beats / CantinaOS cache (R3X_BEAT_CACHE_DIR, else ~/.cache/dj-r3x/beats), matched
// by path + size + mtime. Nothing here writes anywhere.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = fileURLToPath(new URL('../../../../', import.meta.url));
const AUDIO = /\.(mp3|wav|ogg|m4a|flac|aac)$/i;
const TYPES = { mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', m4a: 'audio/mp4', flac: 'audio/flac', aac: 'audio/aac' };

function musicDir() {
  const env = process.env.MUSIC_DIR;
  if (env && fs.existsSync(env)) return env;
  for (const d of ['audio/music', 'cantina_os/assets/music']) if (fs.existsSync(path.join(REPO, d))) return path.join(REPO, d);
  return null;
}

function beatCache() {
  const dir = process.env.R3X_BEAT_CACHE_DIR || path.join(os.homedir(), '.cache/dj-r3x/beats');
  const byPath = new Map();
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => n.endsWith('.json')); } catch { return byPath; }
  for (const n of names) {
    try {
      const j = JSON.parse(fs.readFileSync(path.join(dir, n), 'utf8'));
      if (j.path && j.bpm) byPath.set(j.path, j);
    } catch { /* a bad cache file is just a miss */ }
  }
  return byPath;
}

export function studioLibrary() {
  return {
    name: 'r3x-studio-library',
    configureServer(server) {
      server.middlewares.use('/studio/library', (_req, res) => {
        const dir = musicDir();
        const cache = beatCache();
        const rows = !dir ? [] : fs.readdirSync(dir).filter((n) => AUDIO.test(n)).sort().map((name) => {
          const full = path.join(dir, name);
          const st = fs.statSync(full);
          const c = cache.get(full);
          const fresh = c && c.size === st.size && Math.abs(c.mtime - st.mtimeMs / 1000) < 1;
          return { name, url: `/studio/audio/${encodeURIComponent(name)}`, ...(fresh ? { bpm: c.bpm, first_beat_s: c.first_beat_s, beats: c.beats } : {}) };
        });
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify(rows));
      });
      server.middlewares.use('/studio/audio/', (req, res, next) => {
        const dir = musicDir();
        const name = decodeURIComponent((req.url || '').replace(/^\//, '').split('?')[0]);
        if (!dir || !name || name.includes('/') || name.includes('..') || !AUDIO.test(name)) return next();
        const full = path.join(dir, name);
        if (!fs.existsSync(full)) return next();
        res.setHeader('content-type', TYPES[name.split('.').pop().toLowerCase()] || 'application/octet-stream');
        fs.createReadStream(full).pipe(res);
      });
    },
  };
}
