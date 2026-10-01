import { describe, expect, it } from 'vitest';
import { checkVerdict } from '../src/workbench/buildpanel';
import { panelDefault } from '../src/layout';
import { initial, step, view } from '../src/ptt';

describe('Build Checks: one verdict across checks, overlaps and torque', () => {
  const none = { pass: 0, warn: 0, fail: 0, explained: 0 };
  it('counts unexplained overlaps as things to fix, so the tab agrees with the overlap list', () => {
    const v = checkVerdict({ checks: { ...none, pass: 1 }, overlapsOpen: 10, overlapsExplained: 27, torqueFail: 0, torqueWarn: 0 });
    expect(v.toFix).toBe(10);
    expect(v.summary).toBe('10 to fix · 27 explained · 1 passing');
  });
  it('adds failing checks and torque, and a slow servo to watch', () => {
    const v = checkVerdict({ checks: { ...none, fail: 2, warn: 1 }, overlapsOpen: 1, overlapsExplained: 0, torqueFail: 1, torqueWarn: 1, torquePass: 6 });
    expect(v).toEqual({ toFix: 4, warn: 2, summary: '4 to fix · 2 to watch · 6 passing' });
  });
  it('says nothing when there is nothing', () => {
    expect(checkVerdict({ checks: none, overlapsOpen: 0, overlapsExplained: 0, torqueFail: 0, torqueWarn: 0 })).toEqual({ toFix: 0, warn: 0, summary: '' });
  });
});

describe('Panel defaults per mode', () => {
  it('opens the Scene panel as Build navigator, a rail elsewhere and on phones', () => {
    expect(panelDefault('scene', 'build', 1600)).toBe(false);
    expect(panelDefault('scene', 'other', 1600)).toBe(true);
    expect(panelDefault('scene', 'build', 420)).toBe(true);
  });
  it('folds the R3X panel to its rail in Studio only', () => {
    expect(panelDefault('r3x', 'studio', 1600)).toBe(true);
    expect(panelDefault('r3x', 'other', 1600)).toBe(false);
  });
});

describe('Talk button copy', () => {
  it('idle: the label says click, the hint only adds Space', () => {
    const s = step(step(initial(), { kind: 'link', connected: true }).state, { kind: 'server', phase: 'idle', owner: null }).state;
    expect(view(s)).toMatchObject({ look: 'idle', label: 'Click to talk', hint: 'or hold Space' });
  });
});
