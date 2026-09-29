/**
 * An in-memory set of show items (clips, cues, sequences) plus the idle policy, indexed by
 * id. Built from the repo's `show/` folder (loader.ts) or from a parity fixture.
 */
import type { Clip, Cue, IdlePolicy, Sequence, ShowItem } from './types';
import { validateIdle, validateItem } from './validate';

export class Catalog {
  readonly items = new Map<string, ShowItem>();
  /** Validation and naming problems found while building (the linter asserts none). */
  readonly errors: string[] = [];
  idle: IdlePolicy | null = null;

  constructor(items: Iterable<unknown> = [], idle?: unknown) {
    for (const x of items) this.add(x);
    if (idle !== undefined) this.setIdle(idle);
  }

  add(x: unknown, file?: string) {
    const errs = validateItem(x, file);
    const it = x as ShowItem;
    if (file && it && typeof it === 'object' && typeof it.id === 'string') {
      const stem = file.replace(/^.*\//, '').replace(/\.json$/, '');
      if (stem !== it.id) errs.push(`${file}: file name must equal id "${it.id}"`);
      const dir = /\/(clips|cues|sequences)\/[^/]+$/.exec(file)?.[1];
      const want = { clips: 'clip', cues: 'cue', sequences: 'sequence' }[dir ?? ''];
      if (want && it.kind !== want) errs.push(`${file}: a ${it.kind} in ${dir}/`);
    }
    if (errs.length) {
      this.errors.push(...errs);
      if (!it || typeof it !== 'object' || typeof it.id !== 'string') return;
    }
    if (this.items.has(it.id)) this.errors.push(`${file ?? it.id}: duplicate id "${it.id}" (ids are unique across kinds)`);
    this.items.set(it.id, it);
  }

  setIdle(x: unknown) {
    const errs = validateIdle(x);
    this.errors.push(...errs);
    if (!errs.length) this.idle = x as IdlePolicy;
  }

  get(id: string) { return this.items.get(id); }
  clip(id: string) { const x = this.items.get(id); return x?.kind === 'clip' ? (x as Clip) : undefined; }
  cue(id: string) { const x = this.items.get(id); return x?.kind === 'cue' ? (x as Cue) : undefined; }
  sequence(id: string) { const x = this.items.get(id); return x?.kind === 'sequence' ? (x as Sequence) : undefined; }

  list<K extends ShowItem['kind']>(kind: K): Extract<ShowItem, { kind: K }>[] {
    return [...this.items.values()].filter((x) => x.kind === kind).sort((a, b) => a.id.localeCompare(b.id)) as Extract<ShowItem, { kind: K }>[];
  }

  /**
   * Fuzzy resolve a name the way Reachy Mini resolves emotion names: exact id, then an
   * id/title/tag match ignoring case and separators.
   */
  resolve(name: string): ShowItem | undefined {
    const exact = this.items.get(name);
    if (exact) return exact;
    const n = norm(name);
    for (const it of this.items.values()) if (norm(it.id) === n || (it.title && norm(it.title) === n)) return it;
    for (const it of this.items.values()) if (it.tags?.some((t) => norm(t) === n)) return it;
    return undefined;
  }
}

export const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
