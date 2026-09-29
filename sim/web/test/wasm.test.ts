import { describe, expect, it } from 'vitest';

// The Rust performer (rust/crates/r3x-performer-wasm) built by `npm run build:wasm`. The
// output is gitignored, so without a build these tests skip.
const GLUE = import.meta.glob('../src/wasm/r3x_performer.js');
const built = Object.keys(GLUE).length > 0;
const PROFILE = Object.values(import.meta.glob('../../../profiles/r3x/robot.json', { eager: true, import: 'default' }))[0];
const SHOW = import.meta.glob(['../../../show/clips/*.json', '../../../show/cues/*.json', '../../../show/sequences/*.json', '../../../show/idle.json'], { eager: true, import: 'default' });
const FIXTURE = Object.values(import.meta.glob('../../../show/tests/fixtures/fx_cue_basic.json', { eager: true, import: 'default' }))[0] as { items: { id: string }[]; fixture: { root: string; bpm: number } };
const GOLDEN = Object.values(import.meta.glob('../../../show/tests/golden/fx_cue_basic.json', { eager: true, import: 'default' }))[0];

/** The slice of the generated bindings used here (the .d.ts only exists after a build). */
interface Wasm {
  initSync(o: { module: Uint8Array }): unknown;
  WasmPerformer: new (profile: string, show: string, seed: number) => {
    command(c: string): void;
    tick(t: number): string;
    events(): string;
    free(): void;
  };
  expandShow(files: string, root: string, bpm?: number): string;
  lintShow(profile: string, files: string): string;
}

async function load(): Promise<Wasm> {
  const m = (await Object.values(GLUE)[0]()) as Wasm;
  // No node types in this package: reach fs through a computed specifier.
  const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { readFileSync(u: URL): Uint8Array };
  m.initSync({ module: fs.readFileSync(new URL('../src/wasm/r3x_performer_bg.wasm', import.meta.url)) });
  return m;
}

const showFiles = () => JSON.stringify(Object.fromEntries(Object.entries(SHOW).map(([k, v]) => [k.replace(/^(\.\.\/)+/, ''), v])));

describe.skipIf(!built)('wasm performer', () => {
  it('loads, performs and produces frames and events', async () => {
    const w = await load();
    const p = new w.WasmPerformer(JSON.stringify(PROFILE), showFiles(), 7);
    p.command(JSON.stringify({ cmd: 'perform', id: 'nod' }));
    let frame: { joints: Record<string, number>; eyes: number[][]; chest: number[][] } | null = null;
    for (let i = 0; i < 60; i++) frame = JSON.parse(p.tick(i / 60));
    expect(Object.keys(frame!.joints)).toContain('head_tilt');
    expect(frame!.eyes).toHaveLength(14);
    expect(frame!.chest).toHaveLength(33);
    const events = JSON.parse(p.events()) as { type: string; run?: { id: string } }[];
    expect(events.find((e) => e.type === 'started')?.run?.id).toBe('nod');
    p.free();
  });

  it('expands the parity fixture to the golden file', async () => {
    const w = await load();
    const files = JSON.stringify(Object.fromEntries(FIXTURE.items.map((it) => [`fixture/${it.id}.json`, it])));
    expect(JSON.parse(w.expandShow(files, FIXTURE.fixture.root, FIXTURE.fixture.bpm))).toEqual(GOLDEN);
  });

  it('lints the show folder against the robot profile', async () => {
    const w = await load();
    expect(JSON.parse(w.lintShow(JSON.stringify(PROFILE), showFiles())).errors).toEqual([]);
  });
});
