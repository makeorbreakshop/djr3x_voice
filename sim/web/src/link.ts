/**
 * Live link to a running CantinaOS (SimBridgeService, ws://127.0.0.1:8765, token-protected).
 * Reconnects forever; the sim runs its own demo whenever the link is down.
 *
 * Two-way: besides the event stream it carries the backend's log records, a state
 * snapshot on connect, and panel commands (push-to-talk, typed turns, CLI lines), each
 * answered by an ack.
 */

const TOKEN_KEY = 'r3x-token';

/**
 * The backend's access token (SimBridge refuses clients without it - any web page can reach
 * localhost). `./r3x` opens the panel at `#token=...`; it is kept in localStorage (only this
 * origin can read it) and removed from the address bar, so later reloads and tabs work too.
 */
export function accessToken(): string {
  const m = /(?:^#|&)token=([^&]+)/.exec(location.hash);
  let token = m ? decodeURIComponent(m[1]) : '';
  if (m) {
    history.replaceState(null, '', location.pathname + location.search);
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage blocked: this page load still has the token */
    }
  } else {
    try {
      token = localStorage.getItem(TOKEN_KEY) ?? '';
    } catch {
      /* no storage: stay unauthenticated; ./r3x prints the link */
    }
  }
  return token;
}

export interface LiveEvent {
  topic: string;
  data: Record<string, unknown>;
  t: number;
  /** Backend wall clock (epoch seconds). Absent on events from an older bridge. */
  wall?: number;
}

export interface LogRecord {
  t: number;
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | string;
  name: string;
  msg: string;
}

export interface ServiceState {
  status: string;
  message: string;
  t: number;
}

export interface Hello {
  mode: string | null;
  services?: Record<string, ServiceState>;
  music?: { tracks: string[]; current: string | null; playing: boolean };
  dj_active?: boolean;
  listening?: boolean;
  log_level?: string;
  logs?: LogRecord[];
  events?: LiveEvent[];
}

export interface Ack {
  ok: boolean;
  message: string;
}

export type Command =
  | { action: 'ptt'; state: 'start' | 'stop' }
  | { action: 'say'; text: string }
  | { action: 'cli'; text: string }
  | { action: 'log_level'; level: string };

export interface LiveHandlers {
  onHello(hello: Hello): void;
  onEvent(ev: LiveEvent): void;
  onStatus(connected: boolean): void;
  onLog?(rec: LogRecord): void;
}

export class LiveLink {
  connected = false;
  private retry?: number;
  private ws?: WebSocket;
  private readonly listeners: Partial<LiveHandlers>[] = [];
  private readonly pending = new Map<string, (a: Ack) => void>();
  private seq = 0;

  constructor(private readonly url: string, h: LiveHandlers, private readonly role: 'panel' | 'sim' = 'panel') {
    this.listeners.push(h);
  }

  /** Add another consumer of the same stream (the control panel, next to the sim). */
  subscribe(h: Partial<LiveHandlers>) {
    this.listeners.push(h);
  }

  start() {
    this.open();
  }

  /** Send a command; resolves with the backend's ack, or a failed ack if offline or slow. */
  send(cmd: Command, timeoutMs = 8000): Promise<Ack> {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return Promise.resolve({ ok: false, message: 'CantinaOS is not connected' });
    }
    const id = `c${++this.seq}`;
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        this.pending.delete(id);
        resolve({ ok: false, message: 'no reply from CantinaOS' });
      }, timeoutMs);
      this.pending.set(id, (a) => {
        clearTimeout(timer);
        resolve(a);
      });
      ws.send(JSON.stringify({ type: 'cmd', id, ...cmd }));
    });
  }

  private emit<K extends keyof LiveHandlers>(k: K, ...args: Parameters<NonNullable<LiveHandlers[K]>>) {
    for (const l of this.listeners) {
      const fn = l[k] as ((...a: unknown[]) => void) | undefined;
      if (!fn) continue;
      try {
        fn.apply(l, args);
      } catch (e) {
        console.error(`live link ${k} handler failed`, e);
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
    ws.onopen = () => {
      this.connected = true;
      ws.send(JSON.stringify({ type: 'hello', role: this.role }));
      this.emit('onStatus', true);
    };
    ws.onmessage = (m) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(String(m.data));
      } catch {
        return;
      }
      switch (msg.type) {
        case 'hello':
          this.emit('onHello', msg as unknown as Hello);
          break;
        case 'event':
          if (typeof msg.topic === 'string') {
            this.emit('onEvent', {
              topic: msg.topic,
              data: (msg.data as Record<string, unknown>) ?? {},
              t: Number(msg.t ?? 0),
              wall: typeof msg.wall === 'number' ? msg.wall : undefined,
            });
          }
          break;
        case 'log':
          this.emit('onLog', msg as unknown as LogRecord);
          break;
        case 'ack': {
          const done = this.pending.get(String(msg.id));
          if (done) {
            this.pending.delete(String(msg.id));
            done({ ok: Boolean(msg.ok), message: String(msg.message ?? '') });
          }
          break;
        }
      }
    };
    ws.onclose = () => {
      if (this.connected) this.emit('onStatus', false);
      this.connected = false;
      for (const done of this.pending.values()) done({ ok: false, message: 'connection lost' });
      this.pending.clear();
      this.schedule();
    };
    ws.onerror = () => ws.close();
  }

  private schedule() {
    clearTimeout(this.retry);
    this.retry = window.setTimeout(() => this.open(), 2000);
  }
}
