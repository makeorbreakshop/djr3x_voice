import { describe, expect, it } from 'vitest';
import { Downsampler, toInt16 } from '../src/voice/remote';

describe('remote voice capture', () => {
  it('downsamples 48 kHz to 16 kHz in streaming blocks', () => {
    const ds = new Downsampler(48000);
    let n = 0;
    for (let b = 0; b < 100; b++) n += ds.push(new Float32Array(480).fill(0.5)).length; // 1 s
    expect(Math.abs(n - 16000)).toBeLessThanOrEqual(2);
  });

  it('passes 16 kHz through and clamps to int16', () => {
    const f = Float32Array.from([0, 1, -1, 2]);
    expect(new Downsampler(16000).push(f)).toBe(f);
    expect(Array.from(toInt16(f))).toEqual([0, 32767, -32767, 32767]);
  });
});
