import { describe, expect, it } from 'vitest';
import { FramePacer } from '../src/pacer';

/** Frames drawn over `ms` of 60 Hz animation frames, with the drawn state from `state(t)`. */
function drawn(p: FramePacer, from: number, ms: number, state: (t: number) => unknown) {
  let n = 0;
  for (let i = 0; i < Math.round((ms * 60) / 1000); i++) {
    const t = from + (i * 1000) / 60;
    if (p.due(t, state(t))) n++;
  }
  return n;
}

const pose = (neck: number, eye = 100) => ({ joints: { neck, arm: 3 }, eyes: [[eye, 0, 0]], stage: null });
const near = (n: number, want: number) => Math.abs(n - want) <= 1;

describe('frame pacer', () => {
  it('still or slow (breathing) is the quiet rate; a fast joint or LED is the active cap', () => {
    const p = new FramePacer({ active: 30, quiet: 15 });
    // The state appearing is a change; then still.
    drawn(p, -600, 600, () => pose(0));
    expect(near(drawn(p, 0, 1000, () => pose(0)), 15)).toBe(true);
    // 2 deg/s drift: quiet.
    expect(near(drawn(p, 1000, 1000, (t) => pose(((t - 1000) / 1000) * 2)), 15)).toBe(true);
    // A 40 deg/s glance: active.
    expect(near(drawn(p, 2000, 1000, (t) => pose(2 + ((t - 2000) / 1000) * 40)), 30)).toBe(true);
    // Half a second after it stops, quiet again.
    drawn(p, 3000, 500, () => pose(42));
    expect(near(drawn(p, 3500, 1000, () => pose(42)), 15)).toBe(true);
    // Speech on the mouth LEDs: active.
    expect(near(drawn(p, 4500, 1000, (t) => pose(42, Math.floor(t / 50) % 2 ? 255 : 0)), 30)).toBe(true);
  });

  it('draws every frame while someone interacts, and for a moment after', () => {
    const p = new FramePacer({ active: 30, quiet: 15 });
    p.interact(0);
    expect(drawn(p, 0, 1000, () => pose(0))).toBe(60);
    expect(near(drawn(p, 2000, 1000, () => pose(0)), 15)).toBe(true);
  });

  it('disabled (still mode, ?pace=0) draws every frame', () => {
    const p = new FramePacer({ active: 30, quiet: 15 }, false);
    expect(drawn(p, 0, 1000, () => pose(0))).toBe(60);
  });

  it('settles 150 ms after the last real input; a bare hover keeps the rate but does not unsettle', () => {
    const p = new FramePacer({ active: 30, quiet: 15 });
    expect(p.settled(150, 0)).toBe(true);
    p.interact(1000);
    expect(p.settled(150, 1100)).toBe(false);
    expect(p.settled(150, 1150)).toBe(true);
    p.interact(1200, true); // hover: full rate, still settled
    expect(p.fps(1300)).toBe(Infinity);
    expect(p.settled(150, 1300)).toBe(true);
    const n = p.touches;
    p.touch(1400);
    expect(p.touches).toBe(n + 1);
  });
});
