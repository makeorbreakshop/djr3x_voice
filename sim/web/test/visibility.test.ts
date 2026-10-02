/**
 * Build's one visibility model (src/workbench/visibility.ts): the look presets as tree defaults, the
 * overrides down the assembly tree, the context row, mixed rows, and the precedence of variants,
 * replaced parts, isolate and steps over the tree.
 */
import { describe, expect, it } from 'vitest';
import { aggregate, overrideFor, partKey, partState, presetState, setOverride, treeState, type VPart, type Vis } from '../src/workbench/visibility';

const part = (id: string, o: Partial<VPart> = {}): VPart => ({ id, shell: false, outside: false, inScope: true, path: ['r3x', 'r3x/head'], ...o });
const shell = part('shell', { shell: true, outside: true });
const inner = part('servo');
const exposedMetal = part('arm', { outside: true });
const none = new Map<string, Vis>();
const flags = { variantHidden: false, replacedOut: false, isolatedOut: false, toCome: false, stepContext: false };

describe('presets set the tree', () => {
  it('Exterior: what the finished droid shows, solid; inside hidden', () => {
    expect(treeState(shell, 'exterior', null, none, 'ghost')).toBe('solid');
    expect(treeState(exposedMetal, 'exterior', null, none, 'ghost')).toBe('solid');
    expect(treeState(inner, 'exterior', null, none, 'ghost')).toBe('hidden');
  });
  it('Mechanism: shells hidden, internals solid', () => {
    expect(treeState(shell, 'mechanism', null, none, 'ghost')).toBe('hidden');
    expect(treeState(inner, 'mechanism', null, none, 'ghost')).toBe('solid');
  });
  it('X-ray: shells ghosted, internals solid', () => {
    expect(treeState(shell, 'inspect', null, none, 'ghost')).toBe('ghost');
    expect(treeState(inner, 'inspect', null, none, 'ghost')).toBe('solid');
  });
  it("Exterior draws a library design's own parts (Lower cage: nothing the droid shows)", () => {
    expect(presetState('exterior', inner, 'library', 'ghost')).toBe('solid');
    expect(presetState('exterior', inner, 'system', 'ghost')).toBe('hidden');
  });
  it("Mechanism keeps a focus's own shells as ghosts; a hidden context row drops them", () => {
    expect(treeState(shell, 'mechanism', 'library', none, 'ghost')).toBe('ghost');
    expect(treeState(shell, 'mechanism', 'library', none, 'hidden')).toBe('hidden');
    expect(treeState(shell, 'inspect', 'library', none, 'hidden')).toBe('ghost'); // X-ray is the ghosted shells
  });
});

describe('the context row: the rest of the droid', () => {
  const out = (p: VPart) => ({ ...p, inScope: false });
  it('ghost, hidden or solid, never more visible than the preset allows', () => {
    expect(treeState(out(inner), 'mechanism', 'library', none, 'ghost')).toBe('ghost');
    expect(treeState(out(inner), 'mechanism', 'library', none, 'hidden')).toBe('hidden');
    expect(treeState(out(inner), 'mechanism', 'library', none, 'solid')).toBe('solid');
    // Exterior hides what is inside the rest of the droid, whatever the row says
    expect(treeState(out(inner), 'exterior', 'library', none, 'solid')).toBe('hidden');
    // the rest's shells in Mechanism: ghosts with a focus (as the context row lets them)
    expect(treeState(out(shell), 'mechanism', 'system', none, 'ghost')).toBe('ghost');
  });
  it('an override inside the rest is still capped by the row', () => {
    const o = new Map<string, Vis>([['r3x/head', 'solid']]);
    expect(treeState(out(inner), 'mechanism', 'library', o, 'ghost')).toBe('ghost');
  });
});

describe('overrides down the tree', () => {
  it('a node sets everything under it; the nearest override wins; a part its own', () => {
    const o = new Map<string, Vis>([['r3x', 'hidden'], ['r3x/head', 'ghost']]);
    expect(overrideFor(inner, o)).toBe('ghost');
    expect(treeState(part('base', { path: ['r3x', 'r3x/base'] }), 'mechanism', null, o, 'ghost')).toBe('hidden');
    o.set(partKey('servo'), 'solid');
    expect(treeState(inner, 'mechanism', null, o, 'ghost')).toBe('solid');
  });
  it('an override beats the preset (head shells ghosted in Exterior)', () => {
    expect(treeState(shell, 'exterior', null, new Map([['r3x/head', 'ghost']]), 'ghost')).toBe('ghost');
  });
  it('setting a row drops the overrides under it, and keeps none that changes nothing', () => {
    const o = new Map<string, Vis>([['r3x/head/ears', 'ghost'], [partKey('servo'), 'hidden'], ['r3x/base', 'hidden']]);
    const under = (k: string) => k.startsWith('r3x/head/') || k === partKey('servo');
    setOverride(o, 'r3x/head', 'ghost', under, () => ['solid', 'ghost']);
    expect([...o]).toEqual([['r3x/base', 'hidden'], ['r3x/head', 'ghost']]);
    setOverride(o, 'r3x/head', 'solid', under, () => ['solid', 'solid']); // the preset already: nothing kept
    expect(o.has('r3x/head')).toBe(false);
    setOverride(o, 'r3x/base', null, () => false, () => []);
    expect(o.size).toBe(0);
  });
});

describe('a row shows one state or mixed', () => {
  it('aggregates', () => {
    expect(aggregate(['ghost', 'ghost'])).toBe('ghost');
    expect(aggregate(['solid', 'ghost'])).toBe('mixed');
    expect(aggregate([])).toBeNull();
  });
});

describe('precedence: variants, replaced, isolate, steps, then the tree', () => {
  const solid = new Map<string, Vis>([[partKey('servo'), 'solid']]);
  it('a hidden variant, a replaced part or an isolation hides whatever the tree says', () => {
    for (const k of ['variantHidden', 'replacedOut', 'isolatedOut'] as const) {
      expect(partState(inner, { ...flags, [k]: true }, 'mechanism', null, solid, 'ghost')).toBe('hidden');
    }
  });
  it('a part still to come in the step is hidden; the step context a ghost', () => {
    expect(partState(inner, { ...flags, toCome: true }, 'mechanism', null, solid, 'ghost')).toBe('hidden');
    expect(partState(inner, { ...flags, toCome: true, stepContext: true }, 'mechanism', null, solid, 'ghost')).toBe('ghost');
  });
  it('otherwise the tree', () => {
    expect(partState(shell, flags, 'inspect', null, none, 'ghost')).toBe('ghost');
    expect(partState(inner, flags, 'mechanism', null, new Map([['r3x/head', 'hidden']]), 'ghost')).toBe('hidden');
  });
});
