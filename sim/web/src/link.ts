/**
 * Live link to a running CantinaOS (SimBridgeService, ws://127.0.0.1:8765).
 * Reconnects forever; the sim runs its own demo whenever the link is down.
 */

export interface LiveEvent {
  topic: string;
  data: Record<string, unknown>;
  t: number;
}

export interface LiveHandlers {
  onHello(mode: string | null): void;
  onEvent(ev: LiveEvent): void;
  onStatus(connected: boolean): void;
}

export class LiveLink {
  connected = false;
  private retry?: number;

  constructor(private readonly url: string, private readonly h: LiveHandlers) {}

  start() {
    this.open();
  }

  private open() {
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.schedule();
      return;
    }
    ws.onopen = () => {
      this.connected = true;
      this.h.onStatus(true);
    };
    ws.onmessage = (m) => {
      let msg: { type: string; mode?: string | null; topic?: string; data?: Record<string, unknown>; t?: number };
      try {
        msg = JSON.parse(String(m.data));
      } catch {
        return;
      }
      if (msg.type === 'hello') this.h.onHello(msg.mode ?? null);
      else if (msg.type === 'event' && msg.topic) this.h.onEvent({ topic: msg.topic, data: msg.data ?? {}, t: msg.t ?? 0 });
    };
    ws.onclose = () => {
      if (this.connected) this.h.onStatus(false);
      this.connected = false;
      this.schedule();
    };
    ws.onerror = () => ws.close();
  }

  private schedule() {
    clearTimeout(this.retry);
    this.retry = window.setTimeout(() => this.open(), 2000);
  }
}
