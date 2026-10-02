import { describe, expect, it } from 'vitest';
import { contextParts, motionSystems, type TreeNode } from '../src/workbench/systems';
import { focusJoints } from '../src/workbench/direct';
import type { MAssembly, Manifest } from '../src/workbench/manifest';

const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { existsSync(u: URL): boolean; readFileSync(u: URL, enc: string): string };
const load = (id: string): MAssembly | null => {
  const u = new URL(`../../../mech/out/${id}/manifest.json`, import.meta.url);
  return fs.existsSync(u) ? (JSON.parse(fs.readFileSync(u, 'utf8')) as Manifest).root : null;
};
const HUNTER = load('hunter_head');
const COLUMN = load('column_internals');

describe('a focus moves only its own joints', () => {
  const sc = { kind: 'system', joints: [{ node: { key: 'droid/head' }, joint: { id: 'visor' } }] };
  it("the focus's joints, unless Shift widens it; none for no focus or a library design", () => {
    expect([...focusJoints(sc, false)!]).toEqual(['droid/head:visor']);
    expect(focusJoints(sc, true)).toBeNull();
    expect(focusJoints(null, false)).toBeNull();
    expect(focusJoints({ ...sc, kind: 'library' }, false)).toBeNull();
  });
});

describe.skipIf(!HUNTER || !COLUMN)('a focus keeps what it is mounted to (built manifests)', () => {
  it("the visor: its servos' mounts and what they are bolted to, on the head", () => {
    const node: TreeNode = { key: 'h', asm: HUNTER!, children: [] };
    const visor = motionSystems(node).find((s) => s.joints.some((j) => j.joint.id === 'visor'))!;
    const ctx = contextParts(visor.joints, visor.parts);
    expect(ctx.has('visor_mount_l')).toBe(true);
    expect(ctx.has('visor_mount_r')).toBe(true);
    // all on the head (the side that stays put), none of the visor's own
    const link = new Map(HUNTER!.parts.map((p) => [p.id, p.link]));
    for (const id of ctx) {
      expect(link.get(id)).toBe('head');
      expect(visor.parts.has(id)).toBe(false);
    }
    // two steps out: the mounts and what they sit on - more than the mounts alone
    expect(ctx.size).toBeGreaterThan(2);
    expect(contextParts(visor.joints, visor.parts, 1).size).toBeLessThan(ctx.size);
  });

  it('the neck: parts of the column that its sled and neck mate or fasten to', () => {
    const node: TreeNode = { key: 'c', asm: COLUMN!, children: [] };
    const neck = motionSystems(node).find((s) => s.joints.some((j) => j.joint.id === 'head_lift'))!;
    const ctx = contextParts(neck.joints, neck.parts);
    const link = new Map(COLUMN!.parts.map((p) => [p.id, p.link]));
    const parents = new Set(neck.joints.map((j) => j.joint.parent_link));
    for (const id of ctx) expect(parents.has(link.get(id)!)).toBe(true);
  });
});
