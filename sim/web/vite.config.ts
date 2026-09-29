import { fileURLToPath } from 'node:url';
import { defineConfig, searchForWorkspaceRoot } from 'vite';
// Studio's audio lane: the music library + cached beat grids (dev server only).
import { studioLibrary } from './src/studio/library.mjs';

// The show files (repo-root show/, shared with CantinaOS) are bundled through
// import.meta.glob in src/show/loader.ts; the dev server must be allowed to read them.
const SHOW = fileURLToPath(new URL('../../show', import.meta.url));
// The Robot Profile the embedded performer loads (src/performer.ts).
const PROFILES = fileURLToPath(new URL('../../profiles', import.meta.url));

export default defineConfig({
  plugins: [studioLibrary()],
  // voice.html: standalone hold-to-talk page (Phase 2 remote voice), iframe-able.
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        voice: fileURLToPath(new URL('./voice.html', import.meta.url)),
      },
    },
  },
  server: {
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), SHOW, PROFILES] },
  },
});
