import { describe, expect, it } from 'vitest';
import { recorderView } from '../src/recorder';

describe('Record section: the runtime recorder status', () => {
  it('needs a connected runtime', () => {
    expect(recorderView(undefined, false)).toMatchObject({ available: false, recording: false, warn: true });
  });
  it('idle shows where takes go, recording shows the folder', () => {
    expect(recorderView({ status: 'stopped', detail: 'Saves to /Users/b/Movies/R3X' } as never, true)).toEqual({
      recording: false,
      available: true,
      note: 'Saves to /Users/b/Movies/R3X',
      warn: false,
    });
    const v = recorderView({ status: 'running', detail: 'Recording to /Users/b/Movies/R3X/r3x-1' } as never, true);
    expect(v.recording).toBe(true);
    expect(v.note).toContain('/Users/b/Movies/R3X/r3x-1');
  });
  it('a failed take says why and can be retried', () => {
    expect(recorderView({ status: 'error', detail: 'recording did not start: permission' } as never, true)).toMatchObject({
      recording: false,
      available: true,
      warn: true,
      note: 'recording did not start: permission',
    });
  });
});
