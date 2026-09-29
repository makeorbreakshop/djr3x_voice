/**
 * The repo-root `show/` folder as raw text, bundled by Vite (vite.config.ts allows serving
 * it), keyed by repo-relative path (`show/clips/nod.json`, `show/idle.json`) - the form the
 * Rust performer's catalogue loads. In dev, editing a show file hot-reloads the page.
 */
const files = import.meta.glob<string>(
  ['../../../../show/clips/*.json', '../../../../show/cues/*.json', '../../../../show/sequences/*.json', '../../../../show/idle.json'],
  { eager: true, query: '?raw', import: 'default' },
);

/** `{path: json text}`, sorted by path, as `WasmPerformer` takes it. */
export const SHOW_FILES: Record<string, string> = Object.fromEntries(
  Object.entries(files)
    .map(([p, text]) => [p.replace(/^(\.\.\/)+/, ''), text] as const)
    .sort(([a], [b]) => a.localeCompare(b)),
);
