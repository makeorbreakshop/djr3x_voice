// Player-level parity traces for rust/crates/r3x-performer-core (plan Phase 3).
//
// Runs the TypeScript show player + body compositor + idle policy, the procedural
// Performer, the actuation pipeline over scripted inputs and writes
// what they produce to rust/crates/r3x-performer-core/tests/parity/perf_*.json. The Rust
// tests replay the same inputs and must match. Randomness goes through a seeded mulberry32
// (Math.random is replaced while a scenario runs), identical to r3x_performer_core::rng.
//
// Regenerate only when the TS reference changes on purpose:  node scripts/gen-performer-parity.mjs
import { createServer } from 'vite';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, '../../../rust/crates/r3x-performer-core/tests/parity');

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function withRandom(seed, fn) {
  const orig = Math.random;
  Math.random = mulberry32(seed);
  try { return fn(); } finally { Math.random = orig; }
}

const vite = await createServer({ root: resolve(here, '..'), server: { middlewareMode: true, hmr: false }, appType: 'custom', logLevel: 'error' });
const load = (p) => vite.ssrLoadModule(p);
const { loadCatalog } = await load('/src/show/loader.ts');
const { ShowPlayer } = await load('/src/show/player.ts');
const { BodyCompositor } = await load('/src/show/body.ts');
const { IdleRunner } = await load('/src/show/idle.ts');
const { Performer } = await load('/src/behavior.ts');
const { Actuation } = await load('/src/actuation/pipeline.ts');
const rigLimits = (await load('/src/show/rig_limits.json')).default;

const catalog = loadCatalog();
const JOINTS = Object.keys(rigLimits.joints).sort();
/** A fixed procedural pose (no behaviour randomness) so overrides and owns show. */
const BASE = Object.fromEntries(JOINTS.map((j, i) => [j, (i % 7) - 3 + i * 0.25]));

/**
 * Player + body + idle wired as sim/web/src/main.ts wires them offline. Ops:
 * perform {id, source?, params?, layer?} | stop {sel} | freeze {on} | bpm {value}
 * | speech {on} | music {on} | poke | tick (update everything, sample the pose).
 */
function runShow(ops, { seed = 1, idle: useIdle = false } = {}) {
  const events = [];
  const frames = [];
  let speech = false, bpm = null, music = false, now = 0;
  const body = new BodyCompositor();
  const idle = new IdleRunner(useIdle ? catalog.idle : null, 0, mulberry32(seed));
  const host = {
    dispatch(a, ctx) {
      events.push({ t: now, ev: 'dispatch', run: ctx.run.run_id, at: ctx.at, a });
      if (a.do === 'clip') {
        const c = catalog.clip(a.id);
        if (c) body.play({ runId: ctx.run.run_id, clip: c, intensity: a.intensity, speed: a.speed, layer: ctx.run.layer, owns: ctx.run.owns, t0: ctx.at });
      }
    },
    speechActive: () => speech,
    liveBpm: () => bpm,
    started(r) {
      events.push({ t: now, ev: 'started', run: r.run_id, id: r.id, layer: r.layer, owns: r.owns });
      if (r.owns) body.own(r.run_id, r.layer, r.owns, now);
    },
    ended(r, reason) {
      events.push({ t: now, ev: 'ended', run: r.run_id, id: r.id, reason });
      body.release(r.run_id, now);
      idle.ended(r.run_id, now);
    },
  };
  const player = new ShowPlayer(catalog, host);
  const stopRun = (run) => player.stop({ id: run }, now);
  for (const op of ops) {
    now = op.t;
    switch (op.op) {
      case 'perform':
        if ((op.source ?? 'ui') !== 'idle') idle.poke(now, stopRun);
        player.perform(op.id, { source: op.source ?? 'ui', params: op.params ?? {}, layer: op.layer, now });
        break;
      case 'stop': player.stop(op.sel, now); break;
      case 'freeze': player.freeze(op.on, now); body.freeze(op.on, now); if (op.on) idle.poke(now, stopRun); break;
      case 'bpm': bpm = op.value; break;
      case 'speech': speech = op.on; break;
      case 'music': music = op.on; break;
      case 'poke': idle.poke(now, stopRun); break;
      case 'tick': {
        player.update(now);
        if (useIdle) {
          const eligible = !player.frozen && player.running().every((r) => r.run_id === idle.current || r.layer === 'background');
          idle.update(now, { eligible, music }, (id) => player.perform(id, { source: 'idle', now }), stopRun);
        }
        const pose = body.apply({ ...BASE }, now);
        frames.push({ t: now, pose: JOINTS.map((j) => pose[j] ?? 0) });
        break;
      }
    }
  }
  return { joints: JOINTS, base: JOINTS.map((j) => BASE[j]), ops, events, frames };
}

/** Uneven frame times (a 60/144 Hz mix with a stall), anchored at t0. */
function ticks(t0, t1, ops = []) {
  const dts = [1 / 60, 1 / 144, 0.05, 1 / 60, 0.2, 1 / 30, 1 / 60, 1 / 60];
  const out = [];
  for (let t = t0, i = 0; t <= t1; t += dts[i++ % dts.length]) out.push({ t, op: 'tick' });
  return [...out, ...ops].sort((a, b) => a.t - b.t || (a.op === 'tick' ? 1 : 0) - (b.op === 'tick' ? 1 : 0));
}

const scenarios = {
  // Overrides blending in and out across layers, a sequence that owns joints, an
  // interrupted gesture, intensity/speed params, freeze and its 0.5 s release.
  blend: runShow(ticks(0, 9, [
    { t: 0.2, op: 'perform', id: 'nod' },
    { t: 0.5, op: 'perform', id: 'dj_intro', source: 'timeline' },
    { t: 1.3, op: 'perform', id: 'tilt_curious', params: { intensity: 1.4, speed: 0.7 } },
    { t: 2.1, op: 'perform', id: 'lean_in' },
    { t: 3.0, op: 'perform', id: 'malfunction', source: 'timeline' },
    { t: 4.6, op: 'stop', sel: { layer: 'show' } },
    { t: 5.2, op: 'perform', id: 'arm_throw', params: { intensity: 0.6 } },
    { t: 5.5, op: 'freeze', on: true },
    { t: 5.7, op: 'perform', id: 'nod' },
    { t: 6.4, op: 'freeze', on: false },
    { t: 6.5, op: 'perform', id: 'greet' },
  ])),
  // Queueing inside interruptible_after (latest wins), same-layer interrupts, stop by
  // id / layer / all, a tier rejection and an unknown id.
  interruption: runShow(ticks(0, 8, [
    { t: 0.1, op: 'perform', id: 'arm_wave' },
    { t: 0.15, op: 'perform', id: 'nod' },
    { t: 0.2, op: 'perform', id: 'head_shake' },
    { t: 1.0, op: 'perform', id: 'crowd_hype', source: 'claude' },
    { t: 1.5, op: 'perform', id: 'transition_hype', source: 'jev' },
    { t: 1.6, op: 'perform', id: 'no_such_thing' },
    { t: 2.0, op: 'perform', id: 'dj_intro', source: 'claude' },
    { t: 2.4, op: 'perform', id: 'fist_pump' },
    { t: 2.5, op: 'stop', sel: { id: 'fist_pump' } },
    { t: 3.0, op: 'perform', id: 'double_take' },
    { t: 3.05, op: 'perform', id: 'bow' },
    { t: 4.0, op: 'stop', sel: { layer: 'show' } },
    { t: 4.5, op: 'perform', id: 'excited' },
    { t: 5.0, op: 'speech', on: true },
    { t: 5.0, op: 'perform', id: 'thinking' },
    { t: 6.0, op: 'speech', on: false },
    { t: 7.0, op: 'stop', sel: { all: true } },
  ])),
  // Beat clocks chasing a live tempo that changes and disappears, loops, a speech wait.
  beat: runShow(ticks(0, 24, [
    { t: 0, op: 'bpm', value: 118 },
    { t: 0.3, op: 'perform', id: 'bop_loop', source: 'timeline' },
    { t: 2.0, op: 'perform', id: 'crowd_hype', source: 'timeline' },
    { t: 5.2, op: 'bpm', value: 96 },
    { t: 9.0, op: 'bpm', value: null },
    { t: 11.0, op: 'bpm', value: 140 },
    { t: 12.0, op: 'perform', id: 'transition_hype', source: 'timeline' },
    { t: 13.0, op: 'speech', on: true },
    { t: 15.5, op: 'speech', on: false },
    { t: 18.0, op: 'perform', id: 'dj_intro', source: 'timeline' },
    { t: 19.0, op: 'speech', on: true },
    { t: 21.0, op: 'speech', on: false },
  ])),
  // The weighted idle policy over the real show/idle.json: quiet, music, interaction pokes.
  idle: runShow(ticks(0, 150, [
    { t: 70, op: 'music', on: true },
    { t: 71, op: 'bpm', value: 120 },
    { t: 110, op: 'music', on: false },
    { t: 111, op: 'bpm', value: null },
    { t: 112, op: 'poke' },
    { t: 125, op: 'perform', id: 'nod' },
  ]).filter((o) => o.op !== 'tick' || Math.round(o.t * 60) % 2 === 0), { seed: 42, idle: true }),
};

/** The procedural Performer through every activity; Math.random seeded. */
function runBehavior(seed) {
  const joints = JOINTS;
  const rig = { joints: new Map(joints.map((j) => [j, {}])) };
  const ops = [
    { t: 0, act: 'idle' }, { t: 8, act: 'engaged' }, { t: 14, act: 'listening' }, { t: 17, act: 'thinking' },
    { t: 19, act: 'speaking' }, { t: 26, act: 'engaged' }, { t: 28, act: 'dj' }, { t: 36, act: 'idle' },
  ];
  return withRandom(seed, () => {
    const pose = {};
    const p = new Performer(rig, (j, v) => (pose[j] = v));
    const frames = [];
    let last = 0;
    const amp = (t) => (t >= 19 && t < 26 ? Math.max(0, Math.sin(t * 9.5) * Math.sin(t * 2.3 + 1)) : 0);
    const dts = [1 / 60, 1 / 60, 1 / 30, 1 / 144];
    let k = 0;
    for (let t = 0; t <= 40; t += dts[k++ % dts.length]) {
      for (const o of ops) if (o.t <= t && !o.done) { p.setActivity(o.act, t); o.done = true; }
      const dt = Math.min(0.05, t - last);
      last = t;
      const energy = t > 30 ? 1.4 : 1;
      p.update(t, dt, { amplitude: amp(t), lookTarget: null, bpm: 118, energy });
      frames.push({ t, dt, amplitude: amp(t), energy, pose: k % 4 === 0 ? joints.map((j) => pose[j] ?? 0) : null });
    }
    return { seed, joints, activities: ops.map(({ t, act }) => ({ t, act })), bpm: 118, frames };
  });
}

/** The actuation pipeline (r3x_animation) following moving targets; plant latency seeded. */
function runActuation(seed) {
  const joints = Object.entries(rigLimits.joints).map(([name, j]) => ({ name, parent: null, pivot: [0, 0, 0], axis: [0, 1, 0], min: j.min, max: j.max, type: j.type }));
  return withRandom(seed, () => {
    const a = new Actuation(joints, {}, 'r3x_animation');
    const frames = [];
    const target = (j, t) => ({ head_pan: 70 * Math.sin(t * 1.3), head_tilt: t > 2 ? 18 : -5, head_lift: 15 * Math.sin(t * 0.7), visor: t % 1 < 0.5 ? -12 : 20,
      hero_shoulder: 40, hero_wrist: -80 * Math.cos(t), torso_lower: t > 1 ? 30 : 0, torso_top: -25 }[j] ?? 0);
    const dts = [1 / 60, 1 / 60, 0.02, 1 / 144, 0.05];
    let k = 0;
    for (let t = 0; t <= 4; ) {
      const dt = dts[k++ % dts.length];
      t += dt;
      for (const j of Object.keys(rigLimits.joints)) a.command(j, target(j, t));
      a.update(dt);
      const v = a.jointValues();
      frames.push({ t, dt, frame: a.lastFrame.frame, targets: a.lastFrame.targets, values: [...v.keys()].sort().map((j) => v.get(j)) });
    }
    return { seed, profile: 'r3x_animation', joints: [...a.jointValues().keys()].sort(), frames };
  });
}

mkdirSync(OUT, { recursive: true });
const write = (name, data) => {
  writeFileSync(resolve(OUT, `perf_${name}.json`), JSON.stringify(data));
  console.log(`perf_${name}.json`);
};
for (const [name, data] of Object.entries(scenarios)) write(name, data);
write('behavior', runBehavior(7));
write('actuation', runActuation(11));
await vite.close();
