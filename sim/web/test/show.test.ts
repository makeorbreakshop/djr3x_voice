import { describe, expect, it } from 'vitest';
import { Catalog } from '../src/show/catalog';
import { expand } from '../src/show/expand';
import { lintCatalog, jointLimits, SFX_STEMS } from '../src/show/lint';
import { loadCatalog, SHOW_FILES } from '../src/show/loader';
import { ShowPlayer, type PlayerHost, type RunInfo, type EndReason } from '../src/show/player';
import { BodyCompositor } from '../src/show/body';
import { evalTrack } from '../src/show/curve';
import { IdleRunner, pick } from '../src/show/idle';
import { tierAllows, type Clip, type DeptAction } from '../src/show/types';
import { norm } from '../src/show/catalog';
import rigLimits from '../src/show/rig_limits.json';
import kitSfx from '../src/show/kit_sfx.json';

// Read through Vite (no node types in this package). The gitignored model/sfx globs are
// simply empty on a machine without the kit.
const stem = (p: string) => p.replace(/^.*\//, '');
const byName = (g: Record<string, unknown>) => Object.fromEntries(Object.entries(g).map(([k, v]) => [stem(k), v]));
const FIXTURES = byName(import.meta.glob('../../../show/tests/fixtures/*.json', { eager: true, import: 'default' }));
const GOLDEN = byName(import.meta.glob('../../../show/tests/golden/*.json', { eager: true, import: 'default' }));
const RIG_JSON = Object.values(import.meta.glob('../public/model/rig.json', { eager: true, import: 'default' }))[0];
const SFX_INDEX = Object.values(import.meta.glob('../public/sfx/index.json', { eager: true, import: 'default' }))[0];
const fixtures = Object.keys(FIXTURES).sort();

function fixtureCatalog(f: string) {
  const doc = FIXTURES[f] as { items: unknown[]; fixture: { root: string; bpm: number } };
  return { cat: new Catalog(doc.items), root: doc.fixture.root as string, bpm: doc.fixture.bpm as number };
}

/** Run the real player against a fake clock; record what it dispatches, as an expansion. */
function playerExpansion(cat: Catalog, root: string, bpm: number) {
  const out: Record<string, unknown>[] = [];
  let t0 = 0;
  const host: PlayerHost = {
    dispatch: (a, ctx) => out.push({ t: Math.round((ctx.at - t0) * 1000) / 1000, ...a }),
    speechActive: () => false,
    liveBpm: () => bpm,
  };
  const p = new ShowPlayer(cat, host, { waitGraceS: 0 });
  t0 = 10.0137; // an arbitrary anchor: nothing may depend on it
  p.perform(root, { source: 'timeline', now: t0 });
  // Uneven frame times, including a stall: scheduling must not accumulate them.
  const dts = [1 / 60, 1 / 144, 0.05, 1 / 60, 0.2, 1 / 30];
  for (let t = t0, i = 0; t < t0 + 30 && p.running().length; i++) {
    t += dts[i % dts.length];
    p.update(t);
  }
  return out.sort((a, b) => (a.t as number) - (b.t as number));
}

describe('golden parity (show/tests)', () => {
  it('has fixtures and a golden file for each', () => {
    expect(fixtures.length).toBeGreaterThan(0);
    for (const f of fixtures) expect(GOLDEN[f]).toBeDefined();
  });

  for (const f of fixtures) {
    it(`expand(${f}) reproduces the golden file`, () => {
      const { cat, root, bpm } = fixtureCatalog(f);
      expect(cat.errors).toEqual([]);
      expect(expand(root, cat, { bpm })).toEqual(GOLDEN[f]);
    });

    it(`the clock-driven player dispatches ${f} at the golden times`, () => {
      const { cat, root, bpm } = fixtureCatalog(f);
      expect(playerExpansion(cat, root, bpm)).toEqual(GOLDEN[f]);
    });
  }
});

describe('show linter', () => {
  const cat = loadCatalog();
  const res = lintCatalog(cat);

  it('loads the show folder', () => {
    expect(SHOW_FILES.length).toBeGreaterThan(30);
    expect(cat.list('clip').length).toBeGreaterThanOrEqual(25);
    expect(cat.list('cue').length).toBeGreaterThanOrEqual(12);
    expect(cat.list('sequence').length).toBeGreaterThanOrEqual(6);
  });

  it('every file validates, every id resolves, every clip is performable', () => {
    expect(res.errors).toEqual([]);
  });

  it('prints warnings (non-fatal)', () => {
    if (res.warnings.length) console.info(`show lint warnings:\n  ${res.warnings.join('\n  ')}`);
    expect(Array.isArray(res.warnings)).toBe(true);
  });

  it('every show item expands without error', () => {
    for (const it of cat.items.values()) if (it.kind !== 'clip') expect(() => expand(it.id, cat, { bpm: 120 })).not.toThrow();
  });

  it('catches what it claims to catch', () => {
    const bad = new Catalog([
      { id: 'too_fast', kind: 'clip', tier: 'free', description: 'x', duration: 0.3,
        tracks: { torso_top: { mode: 'additive', keys: [[0, 0], [0.15, 20], [0.3, 0]] } } },
      { id: 'claw', kind: 'clip', tier: 'free', description: 'x', duration: 1,
        tracks: { hero_claw_l: { mode: 'override', keys: [[0, 0], [1, 10]] } } },
      { id: 'coupled', kind: 'clip', tier: 'free', requires: 'extended', description: 'x', duration: 1,
        tracks: { hero_claw_r: { mode: 'override', keys: [[0, 0], [1, 10]] } } },
      { id: 'far', kind: 'clip', tier: 'free', description: 'x', duration: 2,
        tracks: { head_pan: { mode: 'override', keys: [[0, 0], [2, 69]] } } },
      { id: 'c1', kind: 'cue', tier: 'free', description: 'x', actions: [{ at: 0, do: 'clip', id: 'nope' }, { at: 0, do: 'sfx', id: 'kazoo' }, { at: 0, do: 'chest', command: 'Q1' }] },
      { id: 's1', kind: 'sequence', tier: 'free', description: 'x', track: [{ at: 0, sequence: 's2' }] },
      { id: 's2', kind: 'sequence', tier: 'show', description: 'x', track: [{ at: 0, sequence: 's3' }] },
      { id: 's3', kind: 'sequence', tier: 'show', description: 'x', track: [{ at: 0, sequence: 's4' }] },
      { id: 's4', kind: 'sequence', tier: 'show', description: 'x', track: [{ at: 0, do: 'unduck' }] },
    ], { after_s: 10, choices: [{ id: 's2', weight: 1 }] });
    const e = lintCatalog(bad).errors.join('\n');
    expect(e).toMatch(/too_fast\.torso_top: peak velocity/);
    expect(e).toMatch(/too_fast\.torso_top: peak acceleration/);
    expect(e).toMatch(/claw\.hero_claw_l: extended joint/);
    expect(e).toMatch(/coupled\.hero_claw_r: coupled joint/);
    expect(e).toMatch(/far\.head_pan: 69 .* outside/);
    expect(e).toMatch(/c1: unknown clip "nope"/);
    expect(e).toMatch(/c1: sfx "kazoo"/);
    expect(e).toMatch(/c1: chest "Q1"/);
    expect(e).toMatch(/s1: nesting deeper than 3/);
    expect(e).toMatch(/s1 \(free\) plays sequence s2 \(show\)/);
    expect(e).toMatch(/idle.json: s2 is show/);
  });

  it('the committed rig limits match the generated rig.json (when built)', () => {
    if (!RIG_JSON) return; // model not built on this machine: the committed table stands
    const rig = RIG_JSON as { joints: { name: string; min: number; max: number; type?: string }[] };
    const got = Object.fromEntries(rig.joints.map((j) => [j.name, { min: j.min, max: j.max, type: j.type ?? 'revolute' }]));
    expect(got).toEqual(rigLimits.joints);
  });

  it('the committed kit sfx list matches public/sfx (when present)', () => {
    if (!SFX_INDEX) return;
    expect((SFX_INDEX as string[]).map((f) => f.replace(/\.[^.]+$/, ''))).toEqual(kitSfx.stems);
    expect(SFX_STEMS.get(norm('air_horn'))).toBe('Air Horn');
  });

  it('soft limits are the pipeline\'s', () => {
    const l = jointLimits();
    expect(l.get('head_pan')).toMatchObject({ lo: -66, hi: 66, vMax: 150, base: true, primary: true });
    expect(l.get('torso_middle')?.base).toBe(false);
  });
});

describe('tiers', () => {
  it('follow the SPEC table', () => {
    expect(tierAllows('free', 'idle')).toBe(true);
    expect(tierAllows('cheap', 'idle')).toBe(false);
    expect(tierAllows('cheap', 'jev')).toBe(true);
    expect(tierAllows('show', 'jev')).toBe(false);
    expect(tierAllows('show', 'idle')).toBe(false);
    for (const s of ['claude', 'timeline', 'ui', 'cli'] as const) expect(tierAllows('show', s)).toBe(true);
  });
});

// ------------------------------------------------------------------ player behaviour

const clip = (id: string, duration = 1, extra: Partial<Clip> = {}): Clip => ({
  id, kind: 'clip', tier: 'free', description: id, duration,
  tracks: { head_tilt: { mode: 'additive', keys: [[0, 0], [duration / 2, 5], [duration, 0]] } }, ...extra,
});

function rig(items: unknown[], opts: { bpm?: () => number | null; speech?: () => boolean } = {}) {
  const log: { t: number; a: DeptAction; run: RunInfo }[] = [];
  const events: { type: string; run: RunInfo; reason?: EndReason }[] = [];
  const host: PlayerHost = {
    dispatch: (a, ctx) => log.push({ t: ctx.at, a, run: ctx.run }),
    speechActive: opts.speech ?? (() => false),
    liveBpm: opts.bpm ?? (() => null),
    started: (run) => events.push({ type: 'started', run }),
    ended: (run, reason) => events.push({ type: 'ended', run, reason }),
  };
  const cat = new Catalog(items);
  expect(cat.errors).toEqual([]);
  return { p: new ShowPlayer(cat, host), log, events, cat };
}

const runFor = (p: ShowPlayer, from: number, to: number, dt = 1 / 60) => {
  for (let t = from; t <= to + 1e-9; t += dt) p.update(t);
};

describe('show player', () => {
  it('rejects tier violations with ended(rejected) and nothing else', () => {
    const { p, log, events } = rig([{ id: 'big', kind: 'sequence', tier: 'show', description: 'x', track: [{ at: 0, do: 'unduck' }] }]);
    expect(p.perform('big', { source: 'jev', now: 0 })).toBeNull();
    expect(events.map((e) => [e.type, e.reason])).toEqual([['ended', 'rejected']]);
    expect(log).toEqual([]);
    expect(p.perform('big', { source: 'claude', now: 0 })).not.toBeNull();
  });

  it('a wait for speech_end slides every later item by the real speech duration', () => {
    let speaking = false;
    const { p, log } = rig([
      { id: 'talk', kind: 'sequence', tier: 'show', description: 'x', track: [
        { at: 0, do: 'speak', text: 'hi' }, { at: 0, do: 'wait', for: 'speech_end' }, { at: 1, do: 'unduck' }] },
    ], { speech: () => speaking });
    p.perform('talk', { now: 0 });
    speaking = true;
    runFor(p, 0, 2.5);
    speaking = false; // 2.5 s of speech
    runFor(p, 2.5 + 1 / 60, 5);
    const un = log.find((l) => l.a.do === 'unduck')!;
    expect(un.t).toBeGreaterThan(3.49);
    expect(un.t).toBeLessThan(3.55);
  });

  it('a beat clock chases a tempo change without losing its place', () => {
    let bpm = 120;
    const { p, log } = rig([
      { id: 'beats', kind: 'sequence', tier: 'free', description: 'x', clock: 'beat', track: [
        { at: 0, do: 'unduck' }, { at: 4, do: 'unduck' }, { at: 8, do: 'unduck' }] },
    ], { bpm: () => bpm });
    p.perform('beats', { now: 0 });
    runFor(p, 0, 1); // 2 beats at 120
    bpm = 60;
    runFor(p, 1 + 1 / 60, 12);
    const ts = log.map((l) => l.t);
    expect(ts[0]).toBeCloseTo(0, 6);
    expect(ts[1]).toBeCloseTo(1 + 2, 1); // 2 beats left at 60 bpm
    expect(ts[2]).toBeCloseTo(3 + 4, 1);
  });

  it('loops are anchored: lap n starts at exactly n * length', () => {
    const { p, log } = rig([
      { id: 'lp', kind: 'sequence', tier: 'free', description: 'x', loop: true, length: 0.7, track: [{ at: 0.1, do: 'unduck' }] },
    ]);
    p.perform('lp', { now: 0 });
    runFor(p, 0, 10, 1 / 37);
    expect(log.length).toBe(15);
    log.forEach((l, i) => expect(l.t).toBeCloseTo(0.1 + i * 0.7, 9));
    p.stop({ id: 'lp' });
    expect(p.running()).toEqual([]);
  });

  it('a new sequence on the same layer interrupts; other layers keep running', () => {
    const { p, events } = rig([
      clip('nod'),
      { id: 'a', kind: 'sequence', tier: 'show', description: 'x', track: [{ at: 5, do: 'unduck' }] },
      { id: 'b', kind: 'sequence', tier: 'show', description: 'x', track: [{ at: 5, do: 'unduck' }] },
    ]);
    p.perform('a', { now: 0 });
    p.perform('nod', { now: 0.1 });
    p.perform('b', { now: 1 });
    expect(events.filter((e) => e.type === 'ended').map((e) => [e.run.id, e.reason])).toEqual([['a', 'interrupted']]);
    expect(p.running().map((r) => `${r.layer}:${r.id}`)).toEqual(['gesture:nod', 'show:b']);
  });

  it('a gesture inside interruptible_after queues the next request (latest wins)', () => {
    const { p, events } = rig([clip('nod', 1, { interruptible_after: 0.4 }), clip('shake'), clip('tilt')]);
    p.perform('nod', { now: 0 });
    p.perform('shake', { now: 0.1 });
    p.perform('tilt', { now: 0.2 });
    expect(p.queued('gesture')).toBe('tilt');
    runFor(p, 0.2, 0.45);
    expect(p.running().map((r) => r.id)).toEqual(['tilt']);
    expect(events.filter((e) => e.type === 'started').map((e) => e.run.id)).toEqual(['nod', 'tilt']);
  });

  it('freeze stops everything and rejects until released', () => {
    const { p, events } = rig([clip('nod')]);
    p.perform('nod', { now: 0 });
    p.freeze(true, 0.1);
    expect(p.running()).toEqual([]);
    expect(p.perform('nod', { now: 0.2 })).toBeNull();
    p.freeze(false, 0.3);
    expect(p.perform('nod', { now: 0.4 })).not.toBeNull();
    expect(events.filter((e) => e.type === 'ended').map((e) => e.reason)).toEqual(['interrupted', 'rejected']);
  });

  it('a lights action in a track is an action, not a cue reference', () => {
    const { p, log, cat } = rig([{ id: 'lt', kind: 'sequence', tier: 'free', description: 'x', track: [{ at: 0, do: 'lights', cue: 'red_flash', hold: 1 }] }]);
    expect(expand('lt', cat)).toEqual([{ t: 0, do: 'lights', cue: 'red_flash', fade: 0, hold: 1 }]);
    p.perform('lt', { now: 0 });
    expect(log.map((l) => l.a)).toEqual([{ do: 'lights', cue: 'red_flash', fade: 0, hold: 1 }]);
  });

  it('params scale clip intensity and speed, clamped to the SPEC ranges', () => {
    const { p, log } = rig([clip('nod'), { id: 'c', kind: 'cue', tier: 'free', description: 'x', actions: [{ at: 0, do: 'clip', id: 'nod', speed: 1.5, intensity: 1.2 }] }]);
    p.perform('c', { now: 0, params: { intensity: 1.5, speed: 2 } });
    expect(log[0].a).toMatchObject({ do: 'clip', intensity: 1.5, speed: 2 });
  });
});

// ------------------------------------------------------------------ body compositor

describe('body compositor', () => {
  const over: Clip = {
    id: 'o', kind: 'clip', tier: 'free', description: 'x', duration: 1,
    tracks: { head_pan: { mode: 'override', keys: [[0, 30], [1, 30]] }, visor: { mode: 'additive', keys: [[0, 0], [0.5, -6], [1, 0]] } },
  };

  it('override blends in over the joint-class blend and back out after the clip', () => {
    const b = new BodyCompositor();
    b.play({ runId: 'r', clip: over, layer: 'gesture', t0: 0 });
    expect(b.apply({ head_pan: 10 }, 0).head_pan).toBeCloseTo(10);
    expect(b.apply({ head_pan: 10 }, 0.1).head_pan).toBeCloseTo(20); // head blend 0.2 s, half-way
    expect(b.apply({ head_pan: 10 }, 0.5).head_pan).toBeCloseTo(30);
    expect(b.apply({ head_pan: 10 }, 1.1).head_pan).toBeCloseTo(20);
    expect(b.apply({ head_pan: 10 }, 1.25).head_pan).toBeCloseTo(10);
  });

  it('intensity scales the offset from the pose the override started over', () => {
    const b = new BodyCompositor();
    b.play({ runId: 'r', clip: over, layer: 'gesture', t0: 0, intensity: 0.5 });
    b.apply({ head_pan: 10 }, 0);
    expect(b.apply({ head_pan: 10 }, 0.5).head_pan).toBeCloseTo(20);
  });

  it('an interrupt freezes the clip and fades its weight (no cut)', () => {
    const b = new BodyCompositor();
    b.play({ runId: 'r', clip: over, layer: 'gesture', t0: 0 });
    b.apply({ head_pan: 0, visor: 0 }, 0.25);
    b.release('r', 0.25);
    const v1 = b.apply({ head_pan: 0, visor: 0 }, 0.26);
    expect(v1.head_pan).toBeGreaterThan(25); // not cut
    expect(v1.visor).toBeLessThan(-2.5);
    const v2 = b.apply({ head_pan: 0, visor: 0 }, 0.6);
    expect(v2.head_pan).toBeCloseTo(0);
    expect(v2.visor).toBeCloseTo(0);
  });

  it('an interrupted additive body clip fades over the body blend, not a cut', () => {
    const b = new BodyCompositor();
    const ring: Clip = { id: 'r', kind: 'clip', tier: 'free', description: 'x', duration: 2,
      tracks: { torso_top: { mode: 'additive', keys: [[0, 0], [1, 10], [2, 0]] } } };
    b.play({ runId: 'r', clip: ring, layer: 'gesture', t0: 0 });
    b.apply({ torso_top: 0 }, 1);
    b.release('r', 1);
    const mid = b.apply({ torso_top: 0 }, 1.175).torso_top; // half of the 0.35 s body blend
    expect(mid).toBeCloseTo(5, 0);
    expect(b.apply({ torso_top: 0 }, 1.4).torso_top).toBeCloseTo(0);
  });

  it('owned joints hold; unowned joints stay live; overrides outside owns are masked', () => {
    const b = new BodyCompositor();
    b.own('s', 'show', ['visor'], 0);
    b.play({ runId: 's', clip: over, layer: 'show', owns: ['visor'], t0: 0 });
    b.apply({ head_pan: 5, visor: 2 }, 0);
    const p = b.apply({ head_pan: 12, visor: 9 }, 0.5);
    expect(p.head_pan).toBeCloseTo(12); // override masked: gaze stays live
    expect(p.visor).toBeCloseTo(2 - 6); // held at 2, plus the additive
  });

  it('the show layer composes above the gesture layer', () => {
    const b = new BodyCompositor();
    b.play({ runId: 'g', clip: over, layer: 'gesture', t0: 0 });
    b.play({ runId: 's', clip: { ...over, tracks: { head_pan: { mode: 'override', keys: [[0, -20], [1, -20]] } } }, layer: 'show', t0: 0 });
    b.apply({ head_pan: 0 }, 0);
    expect(b.apply({ head_pan: 0 }, 0.5).head_pan).toBeCloseTo(-20);
  });

  it('freeze holds the output and releases over 0.5 s', () => {
    const b = new BodyCompositor();
    b.apply({ head_pan: 10 }, 0);
    b.freeze(true, 0);
    expect(b.apply({ head_pan: 40 }, 0.2).head_pan).toBeCloseTo(10);
    b.freeze(false, 1);
    expect(b.apply({ head_pan: 40 }, 1.25).head_pan).toBeCloseTo(25);
    expect(b.apply({ head_pan: 40 }, 1.6).head_pan).toBeCloseTo(40);
  });

  it('min-jerk keys by default', () => {
    const tr = { mode: 'override' as const, keys: [[0, 0], [1, 10]] as [number, number][] };
    expect(evalTrack(tr, 0.5)).toBeCloseTo(5);
    expect(evalTrack(tr, 0.25)).toBeCloseTo(10 * (10 * 0.25 ** 3 - 15 * 0.25 ** 4 + 6 * 0.25 ** 5));
    expect(evalTrack({ ...tr, ease: 'step' }, 0.9)).toBe(0);
  });
});

describe('idle policy', () => {
  it('waits after_s of quiet, then performs a weighted choice; music switches lists', () => {
    const got: string[] = [];
    const idle = new IdleRunner({ after_s: 10, choices: [{ id: 'a', weight: 1 }], while_music: [{ id: 'm', weight: 1 }] }, 0);
    const perform = (id: string) => (got.push(id), `${id}#1`);
    idle.update(5, { eligible: true, music: false }, perform, () => {});
    expect(got).toEqual([]);
    idle.update(10, { eligible: true, music: false }, perform, () => {});
    expect(got).toEqual(['a']);
    const stopped: string[] = [];
    idle.update(11, { eligible: true, music: true }, perform, (r) => stopped.push(r));
    expect(stopped).toEqual(['a#1']);
    idle.update(21, { eligible: true, music: true }, perform, () => {});
    expect(got).toEqual(['a', 'm']);
  });

  it('pick respects weights', () => {
    const list = [{ id: 'x', weight: 3 }, { id: 'y', weight: 1 }];
    expect(pick(list, () => 0.7)).toBe('x');
    expect(pick(list, () => 0.8)).toBe('y');
  });
});
