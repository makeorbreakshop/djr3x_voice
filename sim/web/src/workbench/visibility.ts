/**
 * Build's one visibility model: every part is drawn solid, ghosted or hidden, and every control only
 * sets that state. Pure (no three.js, no DOM) so the rules are tested on their own (test/visibility.test.ts);
 * Workbench.refresh() asks `partState` for each part and draws it.
 *
 * The state of a part, strongest first (the precedence; refresh() keeps Instructions above all of it):
 *
 *   0. Instructions (the guide) decide everything on their own: the section alone, placed-so-far. (refresh)
 *   1. Variants: a part of an option not picked, or of a hidden variant node, is hidden.
 *   2. `replaced_by`: a part our build replaces is hidden, except in the library design that has it.
 *   3. Isolate: with an isolation, only the isolated parts are drawn.
 *   4. Steps: a part still to come in the current step is hidden (the step's context: ghosted, faintly).
 *   5. The tree: an override on the part or its nearest overridden ancestor node (`overrides`), else the
 *      look preset's default for that part; outside the focus, never more visible than the context row
 *      (the "Rest of the droid" row: solid, ghost or hidden).
 *
 * The look presets (the three buttons) are defaults for the tree, not separate rules:
 *
 *   Exterior   what the finished droid shows from outside, solid (painted); everything inside hidden. A
 *              library design's own parts are solid whatever: it is what was opened (a frame or a column
 *              has nothing the finished droid shows, and Exterior drew it empty).
 *   Mechanism  shells hidden, internals solid. A focus keeps its own shells as ghosts, unless the context
 *              row is hidden: then they go too (Hide read as "the ghosts stay" was the Hide that did not work).
 *   X-ray      shells ghosted, internals solid.
 *
 * Pressing a preset clears the overrides (the tree shows the preset); any override after that is a custom
 * view, and the look buttons show none selected (Workbench.custom).
 */

export type Vis = 'solid' | 'ghost' | 'hidden';
export type Preset = 'exterior' | 'mechanism' | 'inspect';
/** The focus's kind (null: the whole build). */
export type FocusKind = 'system' | 'assembly' | 'library' | null;

/** A part as the model needs it: its class, whether the finished droid shows it, where it hangs. */
export interface VPart {
  id: string;
  shell: boolean;
  /** Seen from outside the finished droid (workbench.ts exposed()). */
  outside: boolean;
  /** In the focus (always, with no focus). */
  inScope: boolean;
  /** Its assembly nodes' keys, the root first (the tree's path to it). */
  path: string[];
}

const RANK: Record<Vis, number> = { solid: 0, ghost: 1, hidden: 2 };
/** The less visible of two states (the context row caps what is outside the focus). */
export const lessVisible = (a: Vis, b: Vis): Vis => (RANK[a] >= RANK[b] ? a : b);

/** An override key for one part (node keys are assembly paths; a part's is its id behind `~`). */
export const partKey = (id: string) => `~${id}`;

/** The look preset's state for a part (rule 5's default). */
export function presetState(look: Preset, p: Pick<VPart, 'shell' | 'outside' | 'inScope'>, focus: FocusKind, context: Vis): Vis {
  if (look === 'exterior') return p.outside || (focus === 'library' && p.inScope) ? 'solid' : 'hidden';
  if (!p.shell) return 'solid';
  if (look === 'inspect') return 'ghost';
  // Mechanism: shells hidden. With a focus, shells are ghosts: its own (unless the context row is hidden)
  // and the rest of the droid's (the context row caps those: treeState)
  if (!focus) return 'hidden';
  return p.inScope && context === 'hidden' ? 'hidden' : 'ghost';
}

/** The override that applies to a part: its own, else its nearest overridden ancestor's. */
export function overrideFor(p: Pick<VPart, 'id' | 'path'>, overrides: ReadonlyMap<string, Vis>): Vis | undefined {
  const own = overrides.get(partKey(p.id));
  if (own) return own;
  for (let i = p.path.length - 1; i >= 0; i--) {
    const o = overrides.get(p.path[i]);
    if (o) return o;
  }
  return undefined;
}

/** Rule 5: the tree's state for a part (what its row shows). */
export function treeState(p: VPart, look: Preset, focus: FocusKind, overrides: ReadonlyMap<string, Vis>, context: Vis): Vis {
  const s = overrideFor(p, overrides) ?? presetState(look, p, focus, context);
  return p.inScope ? s : lessVisible(s, context);
}

/** Rules 1-5 (refresh() runs the guide, rule 0, before this). */
export function partState(p: VPart, f: { variantHidden: boolean; replacedOut: boolean; isolatedOut: boolean; toCome: boolean; stepContext: boolean },
  look: Preset, focus: FocusKind, overrides: ReadonlyMap<string, Vis>, context: Vis): Vis {
  if (f.variantHidden || f.replacedOut || f.isolatedOut) return 'hidden';
  if (f.toCome) return f.stepContext ? 'ghost' : 'hidden';
  return treeState(p, look, focus, overrides, context);
}

/** A node's row: the one state its parts share, or 'mixed'. Empty: null. */
export function aggregate(states: Iterable<Vis>): Vis | 'mixed' | null {
  let out: Vis | null = null;
  for (const s of states) {
    if (out === null) out = s;
    else if (out !== s) return 'mixed';
  }
  return out;
}

/**
 * Set a row's state: an override on `key`, applied to everything under it (the overrides of its
 * descendants and their parts are dropped), or none (`null`: back to the preset). An override that only
 * restates what every part under it would show anyway is not kept, so a link carries only real changes.
 * `under(k)`: whether override key `k` lies under `key`; `defaults`: the states the parts under `key`
 * would have without this override.
 */
export function setOverride(overrides: Map<string, Vis>, key: string, state: Vis | null, under: (k: string) => boolean, defaults: () => Vis[]) {
  for (const k of [...overrides.keys()]) if (k !== key && under(k)) overrides.delete(k);
  overrides.delete(key);
  if (!state) return;
  const d = defaults();
  if (d.length && d.every((s) => s === state)) return;
  overrides.set(key, state);
}
