import { describe, expect, it } from 'vitest';
import { trackBpm } from '../src/tempo';

describe('live tempo from music.playback.started', () => {
  it('reads the analysed track bpm', () => {
    expect(trackBpm({ track: { title: 'Batuu Boogie', bpm: 119.9 } })).toBe(119.9);
    expect(trackBpm({ track: { bpm: '128' } })).toBe(128);
  });

  it('is null when the track has no tempo yet (the slider stays in charge)', () => {
    expect(trackBpm({ track: { title: 'x', bpm: null } })).toBeNull();
    expect(trackBpm({ track: { title: 'x' } })).toBeNull();
    expect(trackBpm({})).toBeNull();
    expect(trackBpm(null)).toBeNull();
    expect(trackBpm({ track: 'Cantina Band' })).toBeNull();
  });

  it('rejects implausible values rather than driving the desk with them', () => {
    expect(trackBpm({ track: { bpm: 0 } })).toBeNull();
    expect(trackBpm({ track: { bpm: -5 } })).toBeNull();
    expect(trackBpm({ track: { bpm: 999 } })).toBeNull();
    expect(trackBpm({ track: { bpm: Number.NaN } })).toBeNull();
    expect(trackBpm({ track: { bpm: 'fast' } })).toBeNull();
  });
});
