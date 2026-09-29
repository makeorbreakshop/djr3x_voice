import { fileURLToPath } from 'node:url';
import { defineConfig, searchForWorkspaceRoot } from 'vite';

// The show files (repo-root show/, shared with CantinaOS) are bundled through
// import.meta.glob in src/show/loader.ts; the dev server must be allowed to read them.
const SHOW = fileURLToPath(new URL('../../show', import.meta.url));

export default defineConfig({
  server: {
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), SHOW] },
  },
});
