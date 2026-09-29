import { fileURLToPath } from 'node:url';
import { defineConfig, searchForWorkspaceRoot } from 'vite';
// Studio's audio lane: the music library + cached beat grids (dev server only).
import { studioLibrary } from './src/studio/library.mjs';

// The show files (repo-root show/, shared with CantinaOS) are bundled through
// import.meta.glob in src/show/loader.ts; the dev server must be allowed to read them.
const SHOW = fileURLToPath(new URL('../../show', import.meta.url));
// The Robot Profile the embedded performer loads (src/performer.ts).
const PROFILES = fileURLToPath(new URL('../../profiles', import.meta.url));

const page = (f: string) => fileURLToPath(new URL(f, import.meta.url));

export default defineConfig(({ mode }) => ({
  plugins: [studioLibrary()],
  // `--mode visit` (npm run build:visit): only the public page (plan Phase 10), with relative
  // asset paths so it can be hosted statically under any prefix or iframed. Otherwise the
  // panel/sim, voice.html (Phase 2 hold-to-talk, iframe-able) and visit.html together.
  base: mode === 'visit' ? './' : '/',
  build:
    mode === 'visit'
      ? { outDir: 'dist-visit', rollupOptions: { input: { visit: page('./visit.html') } } }
      : { rollupOptions: { input: { main: page('./index.html'), voice: page('./voice.html'), visit: page('./visit.html') } } },
  server: {
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), SHOW, PROFILES] },
  },
}));
