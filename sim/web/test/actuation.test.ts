import { describe, expect, it } from 'vitest';
import { brakeSpeed, JerkLimitedFollower, minJerkDuration } from '../src/actuation/trajectory';
import { Actuation } from '../src/actuation/pipeline';
import { compileMaestro, MaestroScript, MaestroScriptError } from '../src/actuation/maestro';
import type { JointSpec } from '../src/rig';

/** Actuation.update caps each call at 0.1 s (background-tab guard), so step in frames. */
function run(a: Actuation, seconds: number) {
  for (let t = 0; t < seconds - 1e-9; t += 0.02) a.update(0.02);
}

const J = (name: string, min: number, max: number, type: 'revolute' | 'prismatic' = 'revolute'): JointSpec =>
  ({ name, parent: null, pivot: [0, 0, 0], axis: [0, 1, 0], min, max, type });

const JOINTS: JointSpec[] = [
  J('head_pan', -70, 70), J('head_lift', -20, 20, 'prismatic'), J('head_tilt', -20, 25), J('visor', -15, 30),
  J('hero_shoulder', -35, 45), J('hero_wrist', -90, 90), J('torso_lower', -60, 60), J('torso_top', -45, 45),
];

describe('jerk-limited follower', () => {
  it('reaches the target without exceeding v/a limits', () => {
    const f = new JerkLimitedFollower({ vMax: 150, aMax: 600, jMax: 4000 }, -66, 66);
    f.setTarget(60);
    let vPeak = 0;
    let aPeak = 0;
    let xPeak = 0;
    for (let i = 0; i < 600; i++) {
      f.step(0.005);
      vPeak = Math.max(vPeak, Math.abs(f.v));
      aPeak = Math.max(aPeak, Math.abs(f.a));
      xPeak = Math.max(xPeak, f.x);
    }
    expect(xPeak).toBeLessThan(60.5); // overshoot stays a fraction of a degree
    expect(f.x).toBeCloseTo(60, 1);
    expect(vPeak).toBeLessThanOrEqual(150 + 1e-9);
    expect(aPeak).toBeLessThanOrEqual(600 + 1e-9);
  });

  it('clamps targets to the soft range and never overshoots a soft limit by more than a degree', () => {
    const f = new JerkLimitedFollower({ vMax: 300, aMax: 2000, jMax: 15000 }, -10, 10);
    f.setTarget(500);
    let xMax = -Infinity;
    for (let i = 0; i < 400; i++) {
      f.step(0.005);
      xMax = Math.max(xMax, f.x);
    }
    expect(f.target).toBe(10);
    expect(xMax).toBeLessThanOrEqual(10);
  });

  it('brake speed is zero at zero distance and grows with distance', () => {
    expect(brakeSpeed(0, 600)).toBeCloseTo(0);
    expect(brakeSpeed(10, 600)).toBeGreaterThan(brakeSpeed(1, 600));
  });

  it('keeps jerk within jMax', () => {
    const f = new JerkLimitedFollower({ vMax: 150, aMax: 600, jMax: 4000 }, -66, 66);
    f.setTarget(60);
    let aPrev = 0;
    let jPeak = 0;
    for (let i = 0; i < 600; i++) {
      f.step(0.005);
      jPeak = Math.max(jPeak, Math.abs(f.a - aPrev) / 0.005);
      aPrev = f.a;
    }
    expect(jPeak).toBeLessThanOrEqual(4000 * 1.05); // discrete-time slack
  });

  it('min-jerk duration matches the worked example (90 deg head pan ~1.13 s)', () => {
    expect(minJerkDuration(90, { vMax: 150, aMax: 600, jMax: 4000 })).toBeCloseTo(1.125, 2);
  });
});

describe('actuation pipeline (R-3X Animation mechanics)', () => {
  it('keeps the channel numbers from the R-3X Animation show script', () => {
    const a = new Actuation(JOINTS, {}, 'r3x_animation');
    const byName = Object.fromEntries(a.channels.map((c) => [c.cfg.name, c.cfg.ch]));
    expect(byName).toEqual({ neck: 0, headlift: 1, headtilt: 2, visor: 3, elbow: 4, hand: 5, lowarm: 6, heroarm: 7 });
  });

  it('emits whole-microsecond frames at 50 Hz on the custom controller', () => {
    const a = new Actuation(JOINTS, {}, 'r3x_animation');
    run(a, 1.0);
    expect(a.frameNo).toBeGreaterThanOrEqual(49);
    expect(a.frameNo).toBeLessThanOrEqual(50);
    expect(a.controller.type).toBe('custom');
    expect(a.usPerUnit).toBe(1);
    // Rest pose: head lift centre pulse 1500 us.
    expect(a.lastFrame.targets[1]).toBe(1500);
  });

  it('maps the head lift in mm through the rack and pinion', () => {
    const a = new Actuation(JOINTS, {}, 'r3x_animation');
    const lift = a.byJoint.get('head_lift')!;
    // 0.21 mm per servo degree on a 270 deg servo (7.41 us/deg): +10 mm = +352.8 us.
    expect(lift.valueToUs(10)).toBeCloseTo(1500 + (10 / 0.21) * (2000 / 270), 3);
    a.command('head_lift', 10);
    run(a, 3);
    expect(lift.value).toBeCloseTo(10, 0);
  });

  it('a script target overrides the follower and moves the servo', () => {
    const a = new Actuation(JOINTS, {}, 'r3x_animation');
    a.setTarget(1, 8000); // 2000 us, as in the sample show
    run(a, 1.5);
    const lift = a.byNumber.get(1)!;
    expect(lift.us).toBe(2000);
    expect(lift.value).toBeGreaterThan(13);
    a.releaseAll();
    expect(lift.directUs).toBeNull();
    expect(lift.follower.x).toBeCloseTo(lift.servoToValue(lift.usToServo(2000)), 3);
  });

  it('the servo plant lags the command (a real servo is not instantaneous)', () => {
    const a = new Actuation(JOINTS, {}, 'r3x_animation');
    a.setTarget(0, 8832); // neck to one extreme
    run(a, 0.1);
    const neck = a.byNumber.get(0)!;
    const settled = neck.servoToValue(neck.usToServo(2208));
    expect(Math.abs(neck.value)).toBeLessThan(Math.abs(settled));
  });
});

describe('Maestro script interpreter', () => {
  const SHOW = `
  # headlift
  begin
    6400 headlift
    4956 visor
    500 delay
    8000 headlift
    5297 visor
    500 delay
  repeat
  sub headlift
    1 servo
    return
  sub visor
    3 servo
    return`;

  it('runs subroutine calls, servo and delay on the sim clock', () => {
    const writes: [number, number][] = [];
    const s = new MaestroScript(SHOW, {
      setTarget: (ch, v) => { writes.push([ch, v]); return true; },
      setSpeed: () => {}, setAccel: () => {}, getPosition: () => 0, anyMoving: () => false,
    });
    s.run(0);
    expect(writes).toEqual([[1, 6400], [3, 4956]]);
    s.run(499);
    expect(writes.length).toBe(2);
    s.run(500);
    expect(writes.slice(2)).toEqual([[1, 8000], [3, 5297]]);
    s.run(1000);
    expect(writes.slice(4)).toEqual([[1, 6400], [3, 4956]]); // repeat loops
  });

  it('supports if/else and arithmetic', () => {
    const got: number[] = [];
    const s = new MaestroScript('3 4 plus 7 equals if 1000 else 2000 endif 0 servo quit', {
      setTarget: (_c, v) => { got.push(v); return true; },
      setSpeed: () => {}, setAccel: () => {}, getPosition: () => 0, anyMoving: () => false,
    });
    s.run(0);
    expect(got).toEqual([1000]);
    expect(s.running).toBe(false);
  });

  it('reports unsupported commands with their line', () => {
    expect(() => compileMaestro('1 2 servo\nfrobnicate')).toThrow(MaestroScriptError);
    expect(() => compileMaestro('1 2 servo\nfrobnicate')).toThrow(/line 2/);
  });
});
