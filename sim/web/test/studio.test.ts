import { describe, expect, it } from 'vitest';
import { bake, type ClipDoc, evalKeys, fromDoc, lintMarks, type Key, segmentPeakV, toDoc } from '../src/studio/model';
import { maxError, reduce, type Sample } from '../src/studio/reduce';
import { beatsIn, charTimings } from '../src/studio/audio';

const CLIPS = import.meta.glob('../../../show/clips/*.json', { eager: true, import: 'default' }) as Record<string, ClipDoc>;
const PROFILE = Object.values(import.meta.glob('../../../profiles/r3x/robot.json', { eager: true, import: 'default' }))[0] as {
  joints: { name: string; v_max: number }[];
};
const vMax = (j: string) => PROFILE.joints.find((x) => x.name === j)!.v_max;

describe('clip (de)serialisation', () => {
  it('round-trips every committed clip exactly', () => {
    expect(Object.keys(CLIPS).length).toBeGreaterThan(20);
    for (const [path, doc] of Object.entries(CLIPS)) expect(toDoc(fromDoc(doc)), path).toEqual(doc);
  });

  it('writes a single-ease track as the track ease and bakes a mixed one', () => {
    const c = fromDoc(Object.values(CLIPS).find((d) => d.id === 'nod')!);
    const tr = c.tracks[0];
    for (const k of tr.keys) k.ease = 'linear';
    expect(toDoc(c).tracks[tr.joint].ease).toBe('linear');
    // One linear segment inside a min-jerk track: resampled, and the baked curve follows it.
    const keys: Key[] = [{ t: 0, v: 0, ease: 'minjerk' }, { t: 1, v: 10, ease: 'linear' }, { t: 2, v: 0, ease: 'minjerk' }];
    const b = bake(keys);
    expect(b.ease).toBe('minjerk');
    expect(b.keys.length).toBeGreaterThan(3);
    const baked: Key[] = b.keys.map(([t, v]) => ({ t, v, ease: 'minjerk' }));
    expect(Math.abs(evalKeys(baked, 1.5) - evalKeys(keys, 1.5))).toBeLessThan(0.01);
    // A step becomes a 1 ms jump.
    expect(bake([{ t: 0, v: 0, ease: 'minjerk' }, { t: 1, v: 0, ease: 'minjerk' }, { t: 1.5, v: 5, ease: 'step' }, { t: 2, v: 0, ease: 'step' }]).keys)
      .toContainEqual([1.999, 5]);
  });

  it('pulls joint and time out of lint messages for one clip', () => {
    const m = lintMarks(['x.head_pan: 99 at t=0.5 outside -60.0..60.0', 'x.visor: peak velocity 900 > vMax 300 (segment at t=1.2)', 'nod: fine', 'show/clips/x.json: bad'], 'x');
    expect(m.map((x) => [x.joint, x.t])).toEqual([['head_pan', 0.5], ['visor', 1.2], [undefined, undefined]]);
  });
});

describe('take -> keys', () => {
  const take = (f: (t: number) => number, dur: number): Sample[] =>
    Array.from({ length: Math.round(dur * 50) + 1 }, (_, i) => ({ t: 10 + i / 50, v: f(i / 50) }));

  it('reduces a smooth take to few keys within eps and under vMax', () => {
    const s = take((t) => 20 * Math.sin(t * 1.5), 4);
    const keys = reduce(s, { eps: 0.5, vMax: vMax('head_pan') });
    expect(keys[0].t).toBe(0);
    expect(keys.length).toBeLessThan(40); // of 201 samples; min-jerk keys each come to rest
    expect(maxError(s, keys)).toBeLessThan(0.75); // eps, plus what the 60 ms key floor leaves
    for (let i = 1; i < keys.length; i++) expect(segmentPeakV(keys[i - 1], keys[i])).toBeLessThanOrEqual(vMax('head_pan') + 1e-6);
  });

  it('never keys faster than the channel can move', () => {
    const v = vMax('visor');
    const s = take((t) => (t < 1 ? 0 : 40), 3); // an instant 40-unit flick
    const keys = reduce(s, { eps: 0.3, vMax: v });
    for (let i = 1; i < keys.length; i++) expect(segmentPeakV(keys[i - 1], keys[i])).toBeLessThanOrEqual(v + 1e-6);
    expect(keys[keys.length - 1].v).toBeCloseTo(40, 0);
  });
});

describe('audio lane', () => {
  it('grids beats from the cache or from bpm', () => {
    expect(beatsIn({ bpm: 120, firstBeat: 0.25, beats: [] }, 0, 1.3)).toEqual([0.25, 0.75, 1.25]);
    expect(beatsIn({ bpm: 120, firstBeat: 0, beats: [0.1, 0.6, 1.1] }, 0.5, 2)).toEqual([0.6, 1.1]);
    expect(charTimings('abcd', { dur: 2 })).toEqual([0, 0.5, 1, 1.5]);
  });
});

// Lint integration runs against the built WASM (gitignored); skipped without a build.
const GLUE = import.meta.glob('../src/wasm/r3x_performer.js');
const SHOW = import.meta.glob(['../../../show/clips/*.json', '../../../show/cues/*.json', '../../../show/sequences/*.json', '../../../show/idle.json'], { eager: true, query: '?raw', import: 'default' }) as Record<string, string>;

describe.skipIf(!Object.keys(GLUE).length)('lint + preview through the wasm performer', () => {
  it('flags an edited clip at the joint and time, and previews on the body', async () => {
    const m = (await Object.values(GLUE)[0]()) as {
      initSync(o: { module: Uint8Array }): unknown;
      lintShow(p: string, f: string): string;
      WasmPerformer: new (p: string, f: string, s: number) => { preview(c: string, at: number, hold: boolean): void; tick(t: number): string; command(c: string): void };
    };
    const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { readFileSync(u: URL): Uint8Array };
    m.initSync({ module: fs.readFileSync(new URL('../src/wasm/r3x_performer_bg.wasm', import.meta.url)) });
    const files = Object.fromEntries(Object.entries(SHOW).map(([k, v]) => [k.replace(/^(\.\.\/)+/, ''), v]));
    const profile = JSON.stringify(Object.values(import.meta.glob('../../../profiles/r3x/robot.json', { eager: true, import: 'default' }))[0]);
    const c = fromDoc(CLIPS['../../../show/clips/nod.json']);
    c.id = 'studio_test';
    c.tracks[0].keys[2].v = 400; // far outside the tilt range
    const doc = toDoc(c);
    const lint = JSON.parse(m.lintShow(profile, JSON.stringify({ ...files, 'show/clips/studio_test.json': JSON.stringify(doc) }))) as { errors: string[] };
    const marks = lintMarks(lint.errors, 'studio_test');
    expect(marks.some((x) => x.joint === 'head_tilt' && x.t === doc.tracks.head_tilt.keys[2][0])).toBe(true);
    expect(lintMarks(lint.errors, 'nod')).toEqual([]);

    const p = new m.WasmPerformer(profile, JSON.stringify(files), 3);
    p.command(JSON.stringify({ cmd: 'alive', breathing: false, saccades: false, gaze_wander: false, speech_bob: false }));
    p.tick(0);
    const pan = { ...doc, id: 'pan', tracks: { head_pan: { mode: 'override', keys: [[0, 0], [1, 30]], ease: 'linear' } } };
    p.preview(JSON.stringify(pan), 0.5, true);
    const f = JSON.parse(p.tick(0.02)) as { targets: Record<string, number> };
    expect(f.targets.head_pan).toBeCloseTo(15, 5);
  });
});
