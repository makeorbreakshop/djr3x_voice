/**
 * Which optional files a published snapshot has (publish-viewer.mjs: each manifest's `published_files`,
 * index.json's `files`), so the viewer asks only for those: each part folder's uvtransfer.json, a
 * folder's interference.json and the root's checks.json were ~22 guaranteed 404s a load. Anywhere no
 * listing covers (the dev server's mech/out) a file may exist: ask, as before.
 */
const known: { dir: string; files: Set<string> }[] = [];

const abs = (u: string) => new URL(u.split('?')[0], (globalThis as { location?: { href: string } }).location?.href ?? 'http://localhost/').href;

/** `files` (paths from `dirUrl`) are all the optional files under `dirUrl`. */
export function notePublished(dirUrl: string, files: unknown) {
  if (!Array.isArray(files)) return;
  const dir = abs(dirUrl).replace(/[^/]*$/, '');
  if (known.some((k) => k.dir === dir)) return;
  known.push({ dir, files: new Set(files.filter((f): f is string => typeof f === 'string').map((f) => abs(dir + f))) });
}

/** False only when a listing covers the URL's folder and does not name it. */
export function mayExist(url: string): boolean {
  const u = abs(url);
  // the deepest listing that covers it (a folder's own, over the root's)
  let best: { dir: string; files: Set<string> } | null = null;
  for (const k of known) if (u.startsWith(k.dir) && (!best || k.dir.length > best.dir.length)) best = k;
  return !best || best.files.has(u);
}
