import { describe, expect, it } from 'vitest';
import { initial, OWNER, step, view, type PttInput, type PttSend, type PttState } from '../src/ptt';

/** Feed inputs; collect what would be sent. */
function run(inputs: PttInput[], from: PttState = { ...initial(), connected: true }) {
  let s = from;
  const sent: PttSend[] = [];
  for (const i of inputs) {
    const r = step(s, i);
    s = r.state;
    if (r.send) sent.push(r.send);
  }
  return { s, sent };
}

const server = (phase: 'idle' | 'listening' | 'thinking' | 'speaking', owner: string | null = null): PttInput =>
  ({ kind: 'server', phase, owner });
const ack = (of: 'start' | 'stop', ok = true): PttInput => ({ kind: 'ack', of, ok });
const down = (repeat = false, typing = false): PttInput => ({ kind: 'space_down', repeat, typing });
const up: PttInput = { kind: 'space_up' };
const click: PttInput = { kind: 'click' };

describe('push-to-talk', () => {
  it('click starts, click again stops and sends', () => {
    let r = run([click]);
    expect(r.sent).toEqual(['ptt_start']);
    expect(view(r.s).look).toBe('starting');
    r = run([ack('start'), server('listening', OWNER)], r.s);
    expect(view(r.s)).toMatchObject({ look: 'listening', label: 'Listening… click to send' });
    r = run([click], r.s);
    expect(r.sent).toEqual(['ptt_stop']);
    expect(view(r.s).look).toBe('sending');
    r = run([ack('stop'), server('thinking')], r.s);
    expect(view(r.s).look).toBe('thinking');
    r = run([server('speaking')], r.s);
    expect(view(r.s).label).toBe('R3X is talking');
    expect(run([click], r.s).sent).toEqual([]); // the mic would record R3X
  });

  it('a double click while the start is pending sends one start', () => {
    expect(run([click, click]).sent).toEqual(['ptt_start']);
  });

  it('Space is hold-to-talk and ignores auto-repeat', () => {
    let r = run([down(), ack('start'), server('listening', OWNER), down(true), down(true)]);
    expect(r.sent).toEqual(['ptt_start']);
    expect(view(r.s).label).toBe('Listening… release Space to send');
    r = run([up], r.s);
    expect(r.sent).toEqual(['ptt_stop']);
  });

  it('the start ack can arrive before the listening state: the turn is still ours', () => {
    // A state update from another domain re-sends the old phase in between.
    let r = run([down(), ack('start'), server('idle')]);
    expect(view(r.s).label).toBe('Listening… release Space to send');
    r = run([up], r.s);
    expect(r.sent).toEqual(['ptt_stop']);
    r = run([click, ack('start'), server('thinking'), server('listening', OWNER)]);
    expect(view(r.s).label).toBe('Listening… click to send');
    r = run([server('thinking')], r.s);
    expect(r.s).toMatchObject({ live: false, mode: null });
  });

  it('a Space tap shorter than the start still stops once the start is acked', () => {
    let r = run([down(), up]);
    expect(r.sent).toEqual(['ptt_start']);
    r = run([ack('start')], r.s);
    expect(r.sent).toEqual(['ptt_stop']);
  });

  it('Space typed into a field is not push-to-talk', () => {
    const r = step({ ...initial(), connected: true }, down(false, true));
    expect(r.send).toBeUndefined();
    expect(r.handled).toBeFalsy();
  });

  it('Space during a clicked turn sends it; its keyup does nothing more', () => {
    const r = run([click, ack('start'), server('listening', OWNER), down(), ack('stop'), up]);
    expect(r.sent).toEqual(['ptt_start', 'ptt_stop']);
  });

  it("does not stop a turn someone else owns", () => {
    const r = run([server('listening', 'mouse'), click, down(), up]);
    expect(r.sent).toEqual([]);
    expect(view(r.s)).toMatchObject({ look: 'busy', label: 'Listening (click-anywhere)' });
  });

  it('a refused start goes back to idle', () => {
    const r = run([click, ack('start', false)]);
    expect(view(r.s).look).toBe('idle');
    expect(r.s.mode).toBeNull();
  });

  it('offline and brain-off states say so and do nothing', () => {
    expect(view(initial()).look).toBe('offline');
    expect(run([click], initial()).sent).toEqual([]);
    const off = run([{ kind: 'enabled', enabled: false }, click]);
    expect(off.sent).toEqual([]);
    expect(view(off.s).label).toMatch(/off in this mode/);
  });

  it('losing the link mid-turn resets', () => {
    const r = run([click, ack('start'), server('listening', OWNER), { kind: 'link', connected: false }]);
    expect(r.s).toMatchObject({ connected: false, pending: null, mode: null });
  });
});
