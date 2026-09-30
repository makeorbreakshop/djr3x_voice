import { describe, expect, it } from 'vitest';
import { PACKAGES, faceSlots, ownsLights, profileJsonWith } from '../src/electronics';
import { PROFILE_JSON } from '../src/performer';
import type { RGB } from '../src/leds';

// The Rust performer (npm run build:wasm); without a build the performer test skips.
const GLUE = import.meta.glob('../src/wasm/r3x_performer.js');
const built = Object.keys(GLUE).length > 0;

describe('electronics packages (profiles/electronics)', () => {
  it('ships the three packages, each light group laid out pixel for pixel', () => {
    expect(Object.keys(PACKAGES).sort()).toEqual(['community_morton', 'grnwave_full_led', 'r3x_native']);
    for (const p of Object.values(PACKAGES)) {
      for (const g of p.lights) expect(g.layout.length, `${p.id}.${g.name}`).toBe(g.pixels);
      for (const g of p.lights) expect(p.boards.some((b) => b.id === g.board)).toBe(true);
    }
    const px = (id: string) => Object.fromEntries(PACKAGES[id].lights.map((g) => [g.name, g.pixels]));
    expect(px('r3x_native')).toEqual({ eyes: 14, mouth: 8, chest: 33 });
    expect(px('grnwave_full_led')).toEqual({ body: 96, eyes: 2, mouth: 8 });
    expect(ownsLights(PACKAGES.grnwave_full_led)).toBe(true);
    expect(ownsLights(PACKAGES.r3x_native)).toBe(false);
  });

  it('the robot profile selects r3x_native and the performer gets the choice', () => {
    expect(JSON.parse(PROFILE_JSON).electronics).toBe('r3x_native');
    expect(JSON.parse(profileJsonWith(PROFILE_JSON, 'grnwave_full_led')).electronics).toBe('grnwave_full_led');
  });

  it('downmixes one LED per eye onto the face renderer slots', () => {
    const f = faceSlots({ eyes: [[100, 0, 0], [0, 0, 100]], mouth: [[1, 2, 3]] as RGB[] });
    expect(f.eyes).toHaveLength(14);
    expect(f.eyes[0][0]).toBeGreaterThan(0);
    expect(f.eyes[13][2]).toBeGreaterThan(0);
    expect(f.mouth).toHaveLength(8);
  });
});

interface Wasm {
  initSync(o: { module: Uint8Array }): unknown;
  WasmPerformer: new (profile: string, show: string, seed: number) => { command(c: string): void; tick(t: number): string; free(): void };
}

describe.skipIf(!built)('grnwave in the wasm performer', () => {
  it('frames carry the package groups when grnwave is selected, none for native', async () => {
    const m = (await Object.values(GLUE)[0]()) as Wasm;
    const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { readFileSync(u: URL): Uint8Array };
    m.initSync({ module: fs.readFileSync(new URL('../src/wasm/r3x_performer_bg.wasm', import.meta.url)) });
    const g = new m.WasmPerformer(profileJsonWith(PROFILE_JSON, 'grnwave_full_led'), '{}', 1);
    let f: { package: Record<string, RGB[]> } = JSON.parse(g.tick(0));
    for (let t = 0; t < 4; t += 0.05) f = JSON.parse(g.tick(t));
    expect(Object.fromEntries(Object.entries(f.package).map(([k, v]) => [k, v.length]))).toEqual({ body: 96, eyes: 2, mouth: 8 });
    expect(f.package.eyes.some((c) => c.some((v) => v > 0))).toBe(true);
    g.free();
    const n = new m.WasmPerformer(PROFILE_JSON, '{}', 1);
    expect(JSON.parse(n.tick(0)).package).toEqual({});
    n.free();
  });
});
