/**
 * Scene > Record (`R`): the runtime records, the panel only drives it. One folder per take
 * (default `~/Movies/R3X`): `screen.mp4`, `mic.wav`, `r3x.wav` (R3X's own output mix) and
 * `session.json` - see `rust/crates/r3x-runtime/src/recorder.rs`. No browser prompts: macOS
 * asks once for Screen Recording / Microphone permission for the app running `./r3x`.
 *
 * State is the runtime's `recorder` service status (running = recording); `M` drops a marker.
 */

import type { GatewayClient, RetainedState } from './gateway';
import type { RecordAction } from './generated/RecordAction';

type Health = RetainedState['services']['services'][string];

export interface RecorderView {
  recording: boolean;
  /** Record is possible (a runtime with a recorder is connected). */
  available: boolean;
  note: string;
  warn: boolean;
}

/** What the Record section shows for the runtime's `recorder` status. */
export function recorderView(h: Health | undefined, connected: boolean): RecorderView {
  if (!connected) return { recording: false, available: false, note: 'Needs ./r3x', warn: true };
  if (!h) return { recording: false, available: false, note: 'This runtime has no recorder (rebuild with ./r3x).', warn: true };
  const detail = h.detail ?? '';
  switch (h.status) {
    case 'running':
      return { recording: true, available: true, note: `${detail}. M drops a marker.`, warn: false };
    case 'error':
    case 'degraded':
      return { recording: false, available: true, note: detail, warn: true };
    default:
      return { recording: false, available: true, note: detail, warn: false };
  }
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

export class Recorder {
  private view: RecorderView = recorderView(undefined, false);
  private since = 0;
  private timer = 0;
  private readonly $ = (id: string) => document.getElementById(id)!;

  constructor(private readonly gw: GatewayClient) {
    this.$('rec-go').onclick = () => void this.send(this.view.recording ? 'stop' : 'start');
    gw.subscribe({
      onHello: (h) => this.update(h.state),
      onState: (s) => this.update(s),
      onStatus: () => this.update(gw.state),
    });
    addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.key === 'r' || e.key === 'R') {
        if (!this.view.available) return;
        void this.send(this.view.recording ? 'stop' : 'start');
        e.preventDefault();
      } else if ((e.key === 'm' || e.key === 'M') && this.view.recording) {
        void this.send('marker');
        e.preventDefault();
      }
    });
    this.update(gw.state);
  }

  private async send(action: RecordAction) {
    const go = this.$('rec-go') as HTMLButtonElement;
    go.disabled = true;
    const a = await this.gw.send({ class: 'telemetry', type: 'record', action });
    go.disabled = !this.view.available;
    if (a.status === 'rejected') this.note(a.reason, true);
    else if (action === 'start') this.note('Starting…', false);
    else if (action === 'stop') this.note('Saving…', false);
    else this.note(`Marker at ${clock((Date.now() - this.since) / 1000)}`, false);
  }

  private note(text: string, warn: boolean) {
    const n = this.$('rec-note');
    n.textContent = text;
    n.classList.toggle('warn-line', warn);
  }

  private update(s: RetainedState | null) {
    const was = this.view.recording;
    this.view = recorderView(s?.services.services['recorder'], this.gw.connected);
    if (this.view.recording && !was) {
      this.since = Date.now();
      this.timer = window.setInterval(() => this.render(), 500);
    } else if (!this.view.recording && was) clearInterval(this.timer);
    this.note(this.view.note, this.view.warn);
    this.render();
  }

  private render() {
    const go = this.$('rec-go') as HTMLButtonElement;
    go.disabled = !this.view.available;
    go.classList.toggle('recording', this.view.recording);
    go.textContent = this.view.recording ? `Stop  ${clock((Date.now() - this.since) / 1000)}` : 'Record';
    document.body.classList.toggle('rec-on', this.view.recording);
  }
}
