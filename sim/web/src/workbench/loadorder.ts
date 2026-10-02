/**
 * Which parts a load brings in first (the shared viewer's progressive load, workbench.ts doLoad). The
 * first view waits for these only; the rest of the build streams in behind as context (`fillPending`).
 *
 *   a library link (#at=lib:hunter)   the design's parts (its assemblies, their subtrees when `deep`,
 *                                     its class filters) and the fasteners of those assemblies
 *   no link (the Library landing)     the shells: the droid's outside, the view's whole extent
 *   anything else                     everything (null): a system or assembly link frames parts that
 *                                     may be anywhere in the tree, so it waits for the whole build
 */
import type { MAssembly, MPart } from './manifest';
import { libraryFrom } from './systems';

export interface FirstParts {
  parts: Set<MPart>;
  /** Assemblies whose fasteners come first too. */
  nodes: Set<MAssembly>;
}

/** `want`: a library design id, '' for the landing (shells), null for no ordering. */
export function firstParts(root: MAssembly, want: string | null): FirstParts | null {
  if (want === null) return null;
  const parts = new Set<MPart>();
  const nodes = new Set<MAssembly>();
  const kids = (a: MAssembly) => (a.children ?? []).filter((c): c is MAssembly => 'parts' in c);
  if (want === '') {
    const walk = (a: MAssembly) => {
      for (const p of a.parts ?? []) if (p.class === 'shell') parts.add(p);
      kids(a).forEach(walk);
    };
    walk(root);
    return parts.size ? { parts, nodes } : null;
  }
  const item = libraryFrom(root).find((x) => x.id === want);
  if (!item) return null;
  const take = (a: MAssembly, sub: boolean) => {
    nodes.add(a);
    for (const p of a.parts ?? []) {
      if (item.only && !item.only.includes(p.class)) continue;
      if (item.except?.includes(p.class)) continue;
      parts.add(p);
    }
    if (sub) kids(a).forEach((c) => take(c, true));
  };
  const walk = (a: MAssembly) => {
    if (item.nodes.includes(a.id)) take(a, !!item.deep);
    kids(a).forEach(walk);
  };
  walk(root);
  return parts.size ? { parts, nodes } : null;
}
