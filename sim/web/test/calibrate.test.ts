import { describe, expect, it } from 'vitest';
import { CalSession } from '../src/calibrate';
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
