import { describe, expect, it } from 'vitest';
import { formatView, parseView, type ViewState } from '../src/viewer/link';

describe('viewer link', () => {
  it('round-trips a view', () => {
    const v: ViewState = {
      at: { kind: 'lib', id: 'hunter' }, look: 'inspect', ctx: 'ghost', explode: 0.35, fasteners: false,
      variants: { head_mech: 'hunter' },
      joints: [{ node: 'r3x_droid/base/hunter_head', joint: 'head_tilt', value: -12 }],
      cam: [-0.307, 0.798, 0.931, 0, 0.86, 0.019],
    };
    const h = formatView(v);
    expect(h).toBe('#at=lib:hunter&look=inspect&ctx=ghost&x=0.35&fast=0&v=head_mech:hunter&j=r3x_droid/base/hunter_head/head_tilt:-12&cam=-0.307,0.798,0.931,0,0.86,0.019');
    expect(parseView(h)).toEqual(v);
  });
  it('drops what it does not understand', () => {
    expect(parseView('#at=nope:x&look=bogus&x=abc&cam=1,2')).toEqual({ at: { kind: 'list' } });
    expect(parseView('')).toBeNull();
    expect(parseView('#at=build')).toEqual({ at: { kind: 'build' } });
  });
});
