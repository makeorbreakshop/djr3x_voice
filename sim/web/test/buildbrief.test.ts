import { describe, expect, it } from 'vitest';
import { brief } from '../src/workbench/buildpanel';

describe('Build checks: summary as cause and fix', () => {
  it('splits an explained summary into cause -> fix', () => {
    const b = brief({ summary: 'explained: the cross is open at its centre -> fix: heat-set a short M4 insert' });
    expect(b.cause).toBe('the cross is open at its centre');
    expect(b.fix).toBe('heat-set a short M4 insert');
  });
  it('shows the first finding with a count, coordinates dropped and rounded in the list', () => {
    const b = brief({ summary: 'a x b: 3.00 mm deep at [82.5, 31.299999237060547, -0.6]; c x d: 2.86 mm deep at [1.0, 2.0, 3.0]' });
    expect(b.cause).toBe('a x b: 3.00 mm deep (+1 more)');
    expect(b.fix).toBe('');
    expect(b.items[0]).toContain('31.3');
    expect(b.items).toHaveLength(2);
  });
});
