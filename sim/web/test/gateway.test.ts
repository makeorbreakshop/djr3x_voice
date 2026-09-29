import { afterEach, describe, expect, it, vi } from 'vitest';
import { GatewayClient } from '../src/gateway';
import type { Envelope } from '../src/generated/Envelope';
import type { RetainedState } from '../src/generated/RetainedState';

/** Just enough WebSocket for the client: records sends, lets the test push messages. */
class FakeSocket {
  static last: FakeSocket;
  static OPEN = 1;
  readyState = 1;
  sent: string[] = [];
  onmessage?: (m: { data: string }) => void;
  onclose?: () => void;
  onerror?: () => void;
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.onclose?.();
  }
  push(env: Partial<Envelope> & { kind: string; body: unknown }) {
    this.onmessage?.({ data: JSON.stringify({ v: 1, seq: 1, t_mono: 0, t_wall: 1, source: 'system', ...env }) });
  }
}

const state = {
  stage: { mode: 'show', outputs: { neck: true }, layers: {}, brain: true, autonomy: true, frozen: false },
  conversation: { phase: 'idle', conversation_id: null, ptt_owner: null },
  engagement: { engagement: 'idle' },
  music: { playing: false, track: null, volume: 1, ducked: false, library: [], position_s: 0, position_t: 0, paused: false },
  dj: { active: false, current: null, next: null, commentary: 'none' },
  perf: { frozen: false, runs: [], puppet: {} },
  lights: { eye_pattern: null, eye_color: null, chest_mode: null, stage_cue: null },
  services: { services: {} },
} as RetainedState;

afterEach(() => vi.unstubAllGlobals());

describe('GatewayClient', () => {
  it('tracks retained state and resolves commands by ack id', async () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const gw = new GatewayClient('ws://x/?token=t');
    const modes: string[] = [];
    gw.subscribe({ onState: (s) => modes.push(s.stage.mode) });
    gw.start();
    const ws = FakeSocket.last;
    ws.push({ kind: 'hello', body: { client: { name: 'panel', source: 'ui', classes: ['stage'] }, state, profile: null } });
    expect(gw.connected).toBe(true);

    const pending = gw.send({ class: 'stage', type: 'set_mode', mode: 'bench' });
    const sent = JSON.parse(ws.sent[0]);
    expect(sent).toMatchObject({ kind: 'command', body: { class: 'stage', type: 'set_mode', mode: 'bench' } });
    ws.push({ kind: 'state', body: { domain: 'stage', state: { ...state.stage, mode: 'bench' } } });
    ws.push({ kind: 'ack', re: 'other', body: { status: 'rejected', reason: 'not mine' } });
    ws.push({ kind: 'ack', re: sent.id, body: { status: 'accepted' } });
    await expect(pending).resolves.toEqual({ status: 'accepted' });
    expect(modes).toEqual(['bench']);
    expect(gw.state?.stage.mode).toBe('bench');
    expect(gw.state?.engagement.engagement).toBe('idle');
  });

  it('delivers performer frames', () => {
    vi.stubGlobal('WebSocket', FakeSocket);
    const gw = new GatewayClient('ws://x/?token=t');
    const got: number[] = [];
    gw.subscribe({ onFrames: (f) => got.push(f.joints.head_pan) });
    gw.start();
    FakeSocket.last.push({ kind: 'frames', body: { t_mono: 1, joints: { head_pan: 12 }, lights: { eyes: [[1, 2, 3]] } } });
    expect(got).toEqual([12]);
  });

  it('rejects immediately when offline', async () => {
    const gw = new GatewayClient('ws://x/');
    await expect(gw.send({ class: 'intent', type: 'ptt_stop' })).resolves.toMatchObject({ status: 'rejected' });
  });
});
