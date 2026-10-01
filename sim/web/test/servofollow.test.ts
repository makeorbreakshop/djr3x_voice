import { describe, expect, it } from 'vitest';
import { ServoFollower, TorqueModel } from '../src/mechrig/torque';

/** Peak load of `servo` while a 30 fps pose stream jumps `joint` from 0 to `to` and holds. */
function peak(follow: boolean, joint: string, to: number, servo: string) {
  const tm = new TorqueModel();
  const f = new ServoFollower();
  let worst = 0;
  for (let i = 0; i < 90; i++) {
    const t = i / 30;
    const pose = { [joint]: i < 3 ? 0 : to };
    const loads = tm.update(follow ? f.step(pose, t) : pose, t);
    worst = Math.max(worst, loads.find((l) => l.servo === servo)!.load);
  }
  return worst;
}

describe('Build load meter: a servo chasing a jumping pose', () => {
  it('a slider jump is no longer a 200 %+ single-frame spike', () => {
    expect(peak(false, 'head_pan', 90, 'col_pan_servo')).toBeGreaterThan(1); // the raw artifact
    expect(peak(true, 'head_pan', 90, 'col_pan_servo')).toBeLessThan(0.7);
  });

  it('arrives on the target and holds it (a held pose is gravity only)', () => {
    const f = new ServoFollower();
    let out: Record<string, number> = {};
    for (let i = 0; i < 60; i++) out = f.step({ head_pan: 90, head_lift: 30 }, i / 30);
    expect(out.head_pan).toBe(90);
    expect(out.head_lift).toBe(30);
  });

  it('a pose that truly needs more than the servo has still reads over the limit', () => {
    // the hero arm straight out is a gravity load: the follower changes nothing about it
    const tm = new TorqueModel();
    const f = new ServoFollower();
    let load = 0;
    for (let i = 0; i < 60; i++) load = tm.update(f.step({ hero_shoulder: 45 }, i / 30), i / 30).find((l) => l.servo === 'hero_shoulder_servo')!.load;
    const still = new TorqueModel().update({ hero_shoulder: 45 }, 0).find((l) => l.servo === 'hero_shoulder_servo')!.load;
    expect(load).toBeCloseTo(still, 3);
  });
});
