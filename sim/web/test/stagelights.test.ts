import { describe, expect, it } from 'vitest';
import { CHARACTER, GROUPS, RIGS, StageLights, characterFloor, characterLit, flux, resolveCue, type Group, type RGB } from '../src/stagelights';

const close = (a: RGB, b: RGB, eps = 1e-9) => a.every((v, i) => Math.abs(v - b[i]) < eps);
const lerp = (a: RGB, b: RGB, t: number): RGB => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * t) as RGB;
const snapshot = (l: StageLights) => Object.fromEntries(GROUPS.map((g) => [g, [...l.out[g]] as RGB])) as Record<Group, RGB>;

describe('cues', () => {
  it('resolve against the rig base; aliases fan out to their groups', () => {
    const rig = RIGS.venue_cycle;
    const c = resolveCue(rig, 'blue_white');
    // Split cue: the two rack washes differ.
    expect(close(c.rack_wash_l, c.rack_wash_r)).toBe(false);
    // `walls` sets both wall washes.
    expect(close(c.wall_wash_l, c.wall_wash_r)).toBe(true);
    // Unnamed groups come from the base (practicals at white).
    expect(close(c.practicals, flux({ color: 0xffffff, level: 1 }))).toBe(true);
  });

  it('every cue in every rig resolves, with finite non-negative flux', () => {
    for (const rig of Object.values(RIGS)) {
      for (const name of Object.keys(rig.cues)) {
        const c = resolveCue(rig, name);
        for (const g of GROUPS) for (const v of c[g]) expect(v >= 0 && Number.isFinite(v)).toBe(true);
      }
      for (const m of Object.values(rig.modes)) for (const n of m.list ?? []) expect(rig.cues[n]).toBeDefined();
      expect(rig.cues[rig.initial]).toBeDefined();
    }
  });

  it('flux decodes sRGB hex to linear light times the dimmer', () => {
    expect(close(flux({ color: 0xffffff, level: 0.5 }), [0.5, 0.5, 0.5])).toBe(true);
    const [r, g] = flux({ color: 0x808000, level: 1 });
    expect(r).toBeCloseTo(0.2158605, 6); // sRGB 128 -> linear
    expect(g).toBeCloseTo(0.2158605, 6);
  });
});

describe('crossfade', () => {
  it('interpolates linearly in light from the current output to the target', () => {
    const l = new StageLights({ rig: 'venue_cycle', cue: 'cool_white' });
    const a = snapshot(l);
    const b = resolveCue(RIGS.venue_cycle, 'orange_red');
    l.goCue('orange_red', 2);
    l.update(0.5);
    for (const g of GROUPS) expect(close(l.out[g], lerp(a[g], b[g], 0.25), 1e-9)).toBe(true);
    l.update(0.5);
    for (const g of GROUPS) expect(close(l.out[g], lerp(a[g], b[g], 0.5), 1e-9)).toBe(true);
    expect(l.fade).toBeCloseTo(0.5, 9);
  });

  it('an interrupted fade restarts from where the light is, with no jump', () => {
    const l = new StageLights({ rig: 'venue_cycle', cue: 'cool_white' });
    l.goCue('orange_red', 2);
    l.update(1);
    const mid = snapshot(l);
    l.goCue('blue_white', 1);
    for (const g of GROUPS) expect(close(l.out[g], mid[g])).toBe(true);
    l.update(0.5);
    const b = resolveCue(RIGS.venue_cycle, 'blue_white');
    for (const g of GROUPS) expect(close(l.out[g], lerp(mid[g], b[g], 0.5), 1e-9)).toBe(true);
  });

  it('fade 0 snaps', () => {
    const l = new StageLights({ rig: 'venue_cycle', cue: 'cool_white' });
    l.goCue('amber', 0);
    const b = resolveCue(RIGS.venue_cycle, 'amber');
    for (const g of GROUPS) expect(close(l.out[g], b[g])).toBe(true);
  });
});

describe('large dt', () => {
  it('a fade never overshoots its target, however large the step', () => {
    const l = new StageLights({ rig: 'venue_cycle', cue: 'cool_white' });
    const a = snapshot(l);
    const b = resolveCue(RIGS.venue_cycle, 'blue_white');
    l.goCue('blue_white', 1);
    l.update(1000);
    expect(l.fade).toBe(1);
    for (const g of GROUPS) {
      expect(close(l.out[g], b[g])).toBe(true);
      for (let i = 0; i < 3; i++) {
        expect(l.out[g][i]).toBeLessThanOrEqual(Math.max(a[g][i], b[g][i]) + 1e-12);
        expect(l.out[g][i]).toBeGreaterThanOrEqual(Math.min(a[g][i], b[g][i]) - 1e-12);
      }
    }
  });

  it('a chase lands on the cue the elapsed time implies, fully faded in', () => {
    const l = new StageLights({ rig: 'venue_cycle' });
    l.setBpm(120); // 0.5 s beats; music steps every 2 bars = 8 beats = 4 s
    l.setMode('music');
    const list = RIGS.venue_cycle.modes.music.list!;
    // Entering music resumes at the cue already up if it is in the list (cool_white is).
    const start = list.indexOf(l.cue);
    expect(start).toBeGreaterThanOrEqual(0);
    l.update(4 * 7 + 1); // seven steps and 1 s into the eighth
    expect(l.cue).toBe(list[(start + 7) % list.length]);
    expect(l.fade).toBe(1);
    const b = resolveCue(RIGS.venue_cycle, l.cue);
    for (const g of GROUPS) expect(close(l.out[g], b[g])).toBe(true);
  });

  it('non-positive or non-finite dt is ignored', () => {
    const l = new StageLights();
    const v = l.version;
    l.update(0);
    l.update(-1);
    l.update(Number.NaN);
    expect(l.version).toBe(v);
  });
});

describe('beat stepping', () => {
  it('steps the chase every `bars` bars at the given BPM from the internal clock', () => {
    const l = new StageLights({ rig: 'venue_cycle' });
    l.setBpm(96); // 0.625 s beats; 2 bars = 8 beats = 5 s
    l.setMode('music');
    const list = RIGS.venue_cycle.modes.music.list!;
    const start = list.indexOf(l.cue);
    const dt = 1 / 60;
    let t = 0;
    const changes: number[] = [];
    let cur = l.cue;
    while (t < 16) {
      l.update(dt);
      t += dt;
      if (l.cue !== cur) {
        changes.push(t);
        cur = l.cue;
      }
    }
    expect(changes.length).toBe(3); // at 5, 10 and 15 s (no consecutive repeats in the list)
    changes.forEach((c, i) => expect(Math.abs(c - 5 * (i + 1))).toBeLessThan(dt * 1.01));
    expect(l.cue).toBe(list[(start + 3) % list.length]);
  });

  it('external beats drive the steps and re-phase the internal clock (no double count)', () => {
    const l = new StageLights({ rig: 'venue_cycle', cue: 'yellow_green' });
    l.setBpm(120);
    l.setMode('dj'); // 2 bars = 8 beats
    const list = RIGS.venue_cycle.modes.dj.list!;
    expect(l.cue).toBe(list[0]);
    // Beats arrive every 0.5 s, exactly at the BPM, alongside 60 fps updates.
    for (let b = 0; b < 7; b++) {
      l.beat();
      for (let f = 0; f < 30; f++) l.update(1 / 60);
    }
    expect(l.cue).toBe(list[0]);
    l.beat(); // the 8th beat
    l.update(1 / 60);
    expect(l.cue).toBe(list[1]);
  });

  it('a slow idle cycle steps on seconds, not beats', () => {
    const l = new StageLights({ rig: 'venue_cycle' });
    l.setMode('idle');
    const p = RIGS.venue_cycle.modes.idle;
    l.setBpm(300); // irrelevant in idle
    l.update(p.holdS! + p.fadeS - 0.01);
    expect(l.cue).toBe(p.list![0]);
    l.update(0.02);
    expect(l.cue).toBe(p.list![1]);
  });
});

describe('modes', () => {
  it('disneyland_2019: music puts the droid key on; idle takes it off (true paint); off is dark', () => {
    const l = new StageLights({ rig: 'disneyland_2019' });
    l.setMode('idle');
    l.update(10);
    expect(l.cue).toBe('interlude');
    expect(l.out.droid_key).toEqual([0, 0, 0]);
    expect(l.out.head_rim_l).toEqual([0, 0, 0]);
    l.setMode('music');
    l.update(10);
    expect(l.out.droid_key[0]).toBeGreaterThan(0.5);
    expect(l.out.head_rim_r[2]).toBeGreaterThan(0.5);
    l.setMode('off');
    l.update(10);
    const lum = (c: RGB) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    expect(lum(l.out.wall_wash_l)).toBeLessThan(0.1);
    expect(lum(l.out.droid_key)).toBe(0);
  });

  it('speaking holds the cue (no chase) and lifts the key; leaving speaking eases it back', () => {
    const l = new StageLights({ rig: 'venue_cycle' });
    l.setMode('music');
    l.update(1);
    const cue = l.cue;
    const base = [...l.out.droid_key] as RGB;
    l.setMode('speaking');
    l.update(30); // would be many chase steps in music
    expect(l.cue).toBe(cue);
    const lift = RIGS.venue_cycle.modes.speaking.keyLift!;
    expect(l.out.droid_key[0]).toBeCloseTo(base[0] * lift, 6);
    l.setMode('music');
    l.update(5);
    // Key lift is gone again (the chase may have moved on, so compare with the raw cue).
    const b = resolveCue(RIGS.venue_cycle, l.cue);
    expect(l.out.droid_key[0]).toBeCloseTo(b.droid_key[0], 6);
  });

  it('the key lift never compounds into later fades', () => {
    const l = new StageLights({ rig: 'venue_cycle', cue: 'amber' });
    l.setMode('speaking');
    l.update(5);
    l.goCue('amber', 1); // fade from the (lifted) output to the same cue
    l.setMode('music');
    l.setMode('speaking');
    l.update(5);
    const b = resolveCue(RIGS.venue_cycle, l.cue);
    expect(l.out.droid_key[0]).toBeCloseTo(b.droid_key[0] * RIGS.venue_cycle.modes.speaking.keyLift!, 6);
  });

  it('re-entering music resumes at the cue already up (no jump)', () => {
    const l = new StageLights({ rig: 'venue_cycle' });
    l.setMode('music');
    l.update(4.2); // step 1
    const cue = l.cue;
    l.setMode('speaking');
    l.setMode('music');
    expect(l.cue).toBe(cue);
  });

  it('setRig switches era and keeps the mode running', () => {
    const l = new StageLights({ rig: 'disneyland_2019' });
    l.setMode('idle');
    l.setRig('venue_cycle', 0);
    expect(l.rigPreset).toBe('venue_cycle');
    expect(RIGS.venue_cycle.modes.idle.list).toContain(l.cue);
  });

  it('defaults: disneyland_2019 at its photo-matched song cue', () => {
    const l = new StageLights();
    expect(l.rigPreset).toBe('disneyland_2019');
    expect(l.cue).toBe(RIGS.disneyland_2019.initial);
    expect(l.mode).toBeNull();
  });
});

describe('character light', () => {
  it('never drops below the rig reference, lets the desk raise it, leaves the room alone', () => {
    const floor = characterFloor('disneyland_2019');
    const ref = resolveCue(RIGS.disneyland_2019, RIGS.disneyland_2019.initial);
    // The idle interlude has the key and rims off; the floor puts them back at the reference.
    const idle = resolveCue(RIGS.disneyland_2019, 'interlude');
    expect(idle.droid_key).toEqual([0, 0, 0]);
    const into = Object.fromEntries(GROUPS.map((g) => [g, [0, 0, 0]])) as unknown as Record<Group, RGB>;
    const lit = characterLit(idle, floor, into);
    for (const g of CHARACTER) expect(close(lit[g], ref[g])).toBe(true);
    expect(close(lit.wall_wash_l, idle.wall_wash_l)).toBe(true);
    // A brighter cue shows through.
    const gold = resolveCue(RIGS.disneyland_2019, 'song_gold');
    expect(characterLit(gold, floor, into).droid_key[0]).toBeCloseTo(gold.droid_key[0]);
    expect(characterFloor('no_such_rig')).toEqual(floor);
  });
});
