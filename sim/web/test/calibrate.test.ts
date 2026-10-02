import { describe, expect, it } from 'vitest';
import { CalSession, pressTrim, Trims } from '../src/calibrate';
import type { Actuator } from '../src/generated/Actuator';

const neck: Actuator = {
  name: 'neck',
  joints: { head_pan: 1 },
  driver: 'r3x_servo',
  channel: 0,
  extended: false,
  calibration: {
    center_us: 1490, center_value: 0, trim_us: 0, invert: false, gear: 1.5,
    pulse_min_us: 500, pulse_max_us: 2500, range_deg: 270, status: 'assumed',
  },
};

describe('calibration wizard', () => {
  it('jogs from the current centre, clamped to the pulse range', () => {
    const s = new CalSession(neck);
    expect(s.jog(10)).toEqual({ class: 'perf', type: 'cal_jog', actuator: 'neck', us: 1500 });
    expect(s.jogTo(9000)).toMatchObject({ us: 2500 });
    expect(s.jog(-5000)).toMatchObject({ us: 500 });
  });

  it('saves centre, direction and both limit ends, and refuses half-marked input', () => {
    const s = new CalSession(neck);
    expect(s.save()).toBe('mark the centre first');
    s.jogTo(1502);
    s.markCentre();
    s.invert = true;
    expect(s.save()).toEqual({ class: 'perf', type: 'cal_save', actuator: 'neck', center_us: 1502, invert: true });
    s.jogTo(1900);
    s.markLimit(0);
    expect(s.save()).toBe('mark both limit ends, or neither');
    s.jogTo(1600);
    s.markLimit(1);
    expect(s.save()).toBe('the centre must lie between the limit ends');
    s.jogTo(1100);
    s.markLimit(1);
    expect(s.save()).toMatchObject({ center_us: 1502, limits_us: [1900, 1100] });
  });
});

const visor = (side: 'l' | 'r', channel: number, invert: boolean): Actuator => ({
  name: `visor_${side}`,
  joints: { visor: 1 },
  driver: 'r3x_servo',
  channel,
  extended: false,
  calibration: {
    center_us: 1500, center_value: 0, trim_us: 0, invert, gear: 1,
    pulse_min_us: 500, pulse_max_us: 2500, range_deg: 270, status: 'assumed',
  },
});

describe('servo trims', () => {
  it('steps, resets and saves one servo trim as cal_trim commands, clamped to the pulse range', () => {
    const t = new Trims([neck, visor('l', 3, true), visor('r', 18, false)]);
    expect(t.step('visor_r', 5)).toEqual({ class: 'perf', type: 'cal_trim', actuator: 'visor_r', trim_us: 5, save: false });
    expect(t.step('visor_r', -1)).toMatchObject({ trim_us: 4, save: false });
    expect(t.save('visor_r')).toEqual({ class: 'perf', type: 'cal_trim', actuator: 'visor_r', trim_us: 4, save: true });
    expect(t.reset('visor_r')).toMatchObject({ trim_us: 0 });
    expect(t.set('neck', 5000)).toMatchObject({ trim_us: 1010 }); // centre 1490 + 1010 = the 2500 us end
    expect(t.step('nope', 1)).toBeNull();
  });

  it('finds the mirrored pair on one joint, left first', () => {
    const t = new Trims([neck, visor('r', 18, false), visor('l', 3, true)]);
    expect(t.pair('visor_r')!.map((a) => a.name)).toEqual(['visor_l', 'visor_r']);
    expect(t.pair('visor_l')!.map((a) => a.name)).toEqual(['visor_l', 'visor_r']);
    expect(t.pair('neck')).toBeNull();
  });

  it('the panel trim controls send cal_trim (stepper, pair view, reset, save)', async () => {
    const sent: unknown[] = [];
    const send = async (c: unknown) => (sent.push(c), true);
    const t = new Trims([visor('l', 3, true), visor('r', 18, false)]);
    await pressTrim(t, { name: 'visor_l', step: 5 }, send);
    expect(sent.at(-1)).toEqual({ class: 'perf', type: 'cal_trim', actuator: 'visor_l', trim_us: 5, save: false });
    await pressTrim(t, { name: t.pair('visor_l')![1].name, step: -1 }, send); // the pair view's other side
    expect(sent.at(-1)).toEqual({ class: 'perf', type: 'cal_trim', actuator: 'visor_r', trim_us: -1, save: false });
    await pressTrim(t, { name: 'visor_l', save: true }, send);
    expect(sent.at(-1)).toEqual({ class: 'perf', type: 'cal_trim', actuator: 'visor_l', trim_us: 5, save: true });
    await pressTrim(t, { name: 'visor_l', reset: true }, send);
    expect(sent.at(-1)).toMatchObject({ actuator: 'visor_l', trim_us: 0, save: false });
    expect(await pressTrim(t, { name: 'nope', step: 1 }, send)).toBe(false);
  });
});
