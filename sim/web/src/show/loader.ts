/**
 * The repo-root `show/` folder, bundled by Vite (vite.config.ts allows serving it). In dev,
 * editing a show file hot-reloads the page; `npm run build` inlines the whole catalogue.
 */
import { Catalog } from './catalog';

const files = import.meta.glob<unknown>(['../../../../show/clips/*.json', '../../../../show/cues/*.json', '../../../../show/sequences/*.json'], {
  eager: true,
  import: 'default',
});
const idle = import.meta.glob<unknown>('../../../../show/idle.json', { eager: true, import: 'default' });

/** Path of each file relative to the repo root, e.g. `show/clips/nod.json`. */
const rel = (p: string) => p.replace(/^(\.\.\/)+/, '');

export function loadCatalog(): Catalog {
  const cat = new Catalog();
  for (const [path, doc] of Object.entries(files).sort(([a], [b]) => a.localeCompare(b))) cat.add(doc, rel(path));
  const idleDoc = Object.values(idle)[0];
  if (idleDoc !== undefined) cat.setIdle(idleDoc);
  return cat;
}

/** File paths (repo-relative) by id, for error messages. */
export const SHOW_FILES = Object.keys(files).map(rel);
