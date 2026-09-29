/**
 * Client of the r3x gateway (protocol v1, `rust/crates/r3x-gateway`): typed commands with
 * acks, retained state from `hello` + `state` deltas, typed events and runtime logs.
 * Types are generated from `r3x-contracts`; never hand-mirror them here.
 *
 * Auth: the same local token as the rest of the panel (`#token=` from `./r3x`), sent as the
 * `token` query parameter because browsers cannot set headers on a WebSocket.
 */

import type { Ack } from './generated/Ack';
import type { AudioMeta } from './generated/AudioMeta';
import type { ClientMessage } from './generated/ClientMessage';
import type { Command } from './generated/Command';
import type { Envelope } from './generated/Envelope';
import type { Event } from './generated/Event';
import type { Hello } from './generated/Hello';
import type { LogLine } from './generated/LogLine';
import type { RetainedState } from './generated/RetainedState';
import type { StateUpdate } from './generated/StateUpdate';

export type { Ack, AudioMeta, Command, Event, Hello, LogLine, RetainedState };

/** Where the event came from: the turn id and backend wall clock ride on the envelope. */
export interface EventMeta {
  conversationId: string | null;
  wall: number;
}

export interface GatewayHandlers {
  onHello?(h: Hello): void;
  onState?(s: RetainedState, changed: StateUpdate['domain']): void;
  onEvent?(e: Event, meta: EventMeta): void;
  onLog?(l: LogLine, wall: number): void;
  onStatus?(connected: boolean): void;
  /** Runtime TTS audio (Phase 2): the latest `audio` meta and one binary frame. */
  onAudio?(meta: AudioMeta | null, pcm: ArrayBuffer): void;
}

/** Apply a `state` delta; whole-domain replacement, as the bus sends it. */
export function applyState(s: RetainedState, u: StateUpdate): RetainedState {
  return { ...s, [u.domain]: u.state } as RetainedState;
}

export function gatewayUrl(token: string): string {
  const override = new URLSearchParams(location.search).get('gw');
  const host = override ?? `${location.hostname || '127.0.0.1'}:8780`;
  return `ws://${host}/?token=${encodeURIComponent(token)}`;
}

export class GatewayClient {
  connected = false;
  state: RetainedState | null = null;
  hello: Hello | null = null;
  private ws?: WebSocket;
  private retry?: ReturnType<typeof setTimeout>;
  private seq = 0;
  private readonly pending = new Map<string, (a: Ack) => void>();
  private readonly handlers: GatewayHandlers[] = [];
  private audioMeta: AudioMeta | null = null;

  constructor(private readonly url: string) {}

  subscribe(h: GatewayHandlers) {
    this.handlers.push(h);
  }

  start() {
    this.open();
  }

  /** Send a typed command; resolves with its ack (a rejection if offline or no reply). */
  send(command: Command, timeoutMs = 10000): Promise<Ack> {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.resolve({ status: 'rejected', reason: 'r3x gateway is not connected' });
    }
    const id = `p${++this.seq}`;
    const msg: ClientMessage = { id, kind: 'command', body: command };
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({ status: 'rejected', reason: 'no reply from the r3x gateway' });
      }, timeoutMs);
      this.pending.set(id, (a) => {
        clearTimeout(timer);
        resolve(a);
      });
      ws.send(JSON.stringify(msg));
    });
  }

  /** Mic audio for the current push-to-talk: an `audio` meta, then binary PCM frames. */
  sendAudioMeta(meta: AudioMeta): boolean {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const msg: ClientMessage = { kind: 'audio', body: meta };
    ws.send(JSON.stringify(msg));
    return true;
  }

  sendAudio(pcm: Int16Array<ArrayBuffer> | ArrayBuffer): boolean {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(pcm);
    return true;
  }

  private each(fn: (h: GatewayHandlers) => void) {
    for (const h of this.handlers) {
      try {
        fn(h);
      } catch (e) {
        console.error('gateway handler failed', e);
      }
    }
  }

  private open() {
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.schedule();
      return;
    }
    this.ws = ws;
    ws.binaryType = 'arraybuffer';
    ws.onmessage = (m) => {
      if (m.data instanceof ArrayBuffer) {
        const pcm = m.data;
        this.each((h) => h.onAudio?.(this.audioMeta, pcm));
        return;
      }
      let env: Envelope;
      try {
        env = JSON.parse(String(m.data)) as Envelope;
      } catch {
        return; // garbage
      }
      this.onEnvelope(env);
    };
    ws.onclose = () => {
      if (this.connected) this.each((h) => h.onStatus?.(false));
      this.connected = false;
      for (const done of this.pending.values()) done({ status: 'rejected', reason: 'connection lost' });
      this.pending.clear();
      this.schedule();
    };
    ws.onerror = () => ws.close();
  }

  private onEnvelope(env: Envelope) {
    switch (env.kind) {
      case 'hello':
        this.hello = env.body;
        this.state = env.body.state;
        if (!this.connected) {
          this.connected = true;
          this.each((h) => h.onStatus?.(true));
        }
        this.each((h) => h.onHello?.(env.body));
        break;
      case 'state':
        if (!this.state) return;
        this.state = applyState(this.state, env.body);
        this.each((h) => h.onState?.(this.state!, env.body.domain));
        break;
      case 'event': {
        const meta = { conversationId: env.conversation_id ?? null, wall: env.t_wall };
        this.each((h) => h.onEvent?.(env.body, meta));
        break;
      }
      case 'audio':
        this.audioMeta = env.body;
        break;
      case 'log':
        this.each((h) => h.onLog?.(env.body, env.t_wall));
        break;
      case 'ack': {
        const done = env.re ? this.pending.get(env.re) : undefined;
        if (done) {
          this.pending.delete(env.re!);
          done(env.body);
        }
        break;
      }
    }
  }

  private schedule() {
    clearTimeout(this.retry);
    this.retry = setTimeout(() => this.open(), 2000);
  }
}
