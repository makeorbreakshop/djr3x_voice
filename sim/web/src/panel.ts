/**
 * The control panel around the 3D droid: push-to-talk, typed turns, the conversation,
 * music / DJ / eye / mode controls, a CLI, service health, and the live log + event stream.
 *
 * Everything here goes through the LiveLink to SimBridgeService, which turns each action
 * into the same bus event the terminal, mouse or capture service would have emitted.
 */

import { Ack, Hello, LiveEvent, LiveLink, LogRecord, ServiceState } from './link';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const clockTime = (epochS: number) =>
  new Date(epochS * 1000).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

const EYE_PATTERNS = ['idle', 'engaged', 'listening', 'thinking', 'speaking', 'happy', 'sad', 'angry', 'surprised', 'flash', 'startup', 'error'];
/** Too chatty for the event feed by default. */
const NOISY_TOPICS = new Set(['speech.synthesis.amplitude', 'llm.response.chunk']);
const MAX_LOG_ROWS = 1500;
/** BaseEventPayload bookkeeping: present on every event, rarely what you are looking for. */
const PAYLOAD_NOISE = new Set(['timestamp', 'event_id', 'schema_version']);
const compact = (d: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(d).filter(([k, v]) => !PAYLOAD_NOISE.has(k) && v !== null && v !== undefined));

type Phase = 'offline' | 'idle' | 'engaging' | 'listening' | 'thinking' | 'speaking';

interface Turn {
  id: string;
  li: HTMLLIElement;
  you: HTMLElement;
  rex: HTMLElement | null;
  meta: HTMLElement;
  startedAt: number;
  stoppedAt?: number;
  firstReplyAt?: number;
  speechAt?: number;
  streamed: string;
  finalized: boolean;
  actions: string[];
}

export class ControlPanel {
  private connected = false;
  private mode = 'IDLE';
  private phase: Phase = 'offline';
  private listening = false;
  private pttDownAt = 0;
  private pttToggled = false;
  private pttBusy = false;
  private spaceDown = false;

  private turns = new Map<string, Turn>();
  private lastTurn: Turn | null = null;

  private library: string[] = [];
  private services: Record<string, ServiceState> = {};
  private logPaused = false;
  private logKinds = new Set(['log', 'event']);
  private errCount = 0;
  private activeTab = 'talk';
  private cliHistory: string[] = [];
  private cliIndex = -1;

  constructor(private readonly link: LiveLink) {
    link.subscribe({
      onHello: (h) => this.onHello(h),
      onEvent: (e) => this.onEvent(e),
      onStatus: (on) => this.onStatus(on),
      onLog: (r) => this.addLog(r),
    });
    this.bindTabs();
    this.bindTalk();
    this.bindControls();
    this.bindLogs();
    this.renderEyes();
    this.setPhase('offline');
  }

  // ------------------------------------------------------------------ link

  private onStatus(on: boolean) {
    this.connected = on;
    document.body.classList.toggle('live', on);
    $('offline-note').hidden = on;
    for (const id of ['ptt', 'say-in', 'say-send']) ($(id) as HTMLButtonElement).disabled = !on;
    if (!on) {
      this.listening = false;
      this.pttToggled = false;
      this.setPhase('offline');
      this.addLog({ t: Date.now() / 1000, level: 'WARNING', name: 'panel', msg: 'Lost connection to CantinaOS - retrying every 2 s' });
    } else {
      this.setPhase('idle');
    }
  }

  private onHello(h: Hello) {
    this.setMode(h.mode ?? 'IDLE');
    this.listening = Boolean(h.listening);
    if (h.services) this.services = { ...h.services };
    this.renderServices();
    if (h.music) {
      this.library = h.music.tracks ?? [];
      this.renderLibrary();
      this.setNowPlaying(h.music.playing ? h.music.current : null);
    }
    this.setDj(Boolean(h.dj_active));
    if (h.log_level) ($('log-level') as HTMLSelectElement).value = h.log_level;
    // Replay history so a refresh keeps the recent past.
    $('log').innerHTML = '';
    const history: { t: number; row: () => void }[] = [];
    for (const r of h.logs ?? []) history.push({ t: r.t, row: () => this.addLog(r, true) });
    for (const e of h.events ?? []) history.push({ t: e.wall ?? 0, row: () => this.addEventRow(e, true) });
    history.sort((a, b) => a.t - b.t).forEach((x) => x.row());
    this.scrollLog();
    for (const e of h.events ?? []) this.trackConversation(e, true);
    this.setPhase(this.listening ? 'listening' : 'idle');
  }

  private onEvent(e: LiveEvent) {
    const d = e.data;
    switch (e.topic) {
      case 'system.mode.change':
        this.setMode(String(d.new_mode ?? this.mode));
        break;
      case 'voice.listening.started':
        this.listening = true;
        this.setPhase('listening');
        break;
      case 'voice.listening.stopped':
        this.listening = false;
        this.pttToggled = false;
        this.setPhase('thinking');
        break;
      case 'mouse.recording.stopped':
        this.setPhase('thinking');
        break;
      case 'llm.response':
        // A turn that ends without speech (TTS off or failed, an action with no reply)
        // must not leave the panel stuck on "thinking".
        if (d.is_complete) {
          clearTimeout(this.thinkingTimer);
          this.thinkingTimer = window.setTimeout(() => {
            if (this.phase === 'thinking') this.setPhase('idle');
          }, 6000);
        }
        break;
      case 'speech.generation.started':
      case 'speech.synthesis.started':
        this.setPhase('speaking');
        break;
      case 'speech.generation.complete':
      case 'speech.synthesis.ended':
        if (this.phase === 'speaking') this.setPhase('idle');
        break;
      case 'music.playback.started': {
        const t = d.track as Record<string, unknown> | string | undefined;
        this.setNowPlaying((typeof t === 'object' && t ? String(t.name ?? t.title ?? '') : String(t ?? '')) || 'Unknown track');
        break;
      }
      case 'music.playback.stopped':
        this.setNowPlaying(null);
        break;
      case 'music.library.updated': {
        const tracks = d.tracks;
        if (tracks && typeof tracks === 'object') {
          this.library = (Array.isArray(tracks) ? tracks.map(String) : Object.keys(tracks)).sort();
          this.renderLibrary();
        }
        break;
      }
      case 'dj.mode.changed':
        this.setDj(Boolean(d.is_active));
        break;
      case 'cli.response':
        this.addCliOut(String(d.message ?? ''), Boolean(d.is_error));
        break;
      case 'service_status':
      case 'service.status.update': {
        const name = String(d.service_name ?? d.service ?? '');
        if (name) {
          this.services[name] = { status: String(d.status ?? '').split('.').pop()!, message: String(d.message ?? ''), t: Date.now() / 1000 };
          this.renderServices();
        }
        break;
      }
    }
    this.trackConversation(e, false);
    if (!NOISY_TOPICS.has(e.topic)) this.addEventRow(e);
  }

  // ------------------------------------------------------------------ state

  private setMode(m: string) {
    this.mode = m.toUpperCase();
    document.querySelectorAll<HTMLButtonElement>('[data-mode-btn]').forEach((b) =>
      b.classList.toggle('on', b.dataset.modeBtn === this.mode));
  }

  private setPhase(p: Phase) {
    if (!this.connected) p = 'offline';
    this.phase = p;
    const labels: Record<Phase, string> = {
      offline: 'offline', idle: this.mode.toLowerCase(), engaging: 'engaging…', listening: 'listening',
      thinking: 'thinking', speaking: 'speaking',
    };
    const badge = $('state-badge');
    badge.dataset.state = p;
    badge.textContent = labels[p];
    const ptt = $('ptt');
    ptt.dataset.state = p;
    const label = ptt.querySelector('.ptt-label')!;
    label.textContent =
      p === 'listening' ? (this.pttToggled ? 'Listening… click to send' : 'Listening… release to send')
      : p === 'engaging' ? 'Starting the mic…'
      : p === 'thinking' ? 'Thinking…'
      : p === 'speaking' ? 'R3X is talking'
      : p === 'offline' ? 'CantinaOS offline'
      : 'Hold to talk';
  }

  private setNowPlaying(track: string | null) {
    $('now-track').textContent = track || 'Nothing playing';
    $('now-state').classList.toggle('on', Boolean(track));
  }

  private setDj(on: boolean) {
    const el = $('dj-state');
    el.textContent = on ? 'on' : 'off';
    el.classList.toggle('on', on);
  }

  // ------------------------------------------------------------------ tabs

  private bindTabs() {
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => {
      b.onclick = () => this.showTab(b.dataset.tab!);
    });
    let saved: string | null = null;
    try {
      saved = localStorage.getItem('r3x.tab');
    } catch {
      /* storage blocked */
    }
    if (saved && document.querySelector(`[data-tab="${saved}"]`)) this.showTab(saved);
  }

  private showTab(name: string) {
    this.activeTab = name;
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) =>
      b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    document.querySelectorAll<HTMLElement>('[data-body]').forEach((el) => (el.hidden = el.dataset.body !== name));
    $('panel').classList.toggle('wide', name === 'logs');
    if (name === 'logs') {
      this.errCount = 0;
      this.renderErrCount();
      this.scrollLog();
    }
    try {
      localStorage.setItem('r3x.tab', name);
    } catch {
      /* storage blocked */
    }
    window.dispatchEvent(new Event('resize')); // the 3D view re-centres in the space left
  }

  // ------------------------------------------------------------------ talk

  private bindTalk() {
    const ptt = $<HTMLButtonElement>('ptt');
    ptt.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      ptt.setPointerCapture(e.pointerId);
      this.pttPress();
    });
    ptt.addEventListener('pointerup', () => this.pttRelease());
    ptt.addEventListener('pointercancel', () => this.pttRelease());
    ptt.addEventListener('contextmenu', (e) => e.preventDefault());

    addEventListener('keydown', (e) => {
      if (e.code !== 'Space' || e.repeat || this.typing(e)) return;
      e.preventDefault();
      this.spaceDown = true;
      this.pttPress();
    });
    addEventListener('keyup', (e) => {
      if (e.code !== 'Space' || !this.spaceDown) return;
      e.preventDefault();
      this.spaceDown = false;
      this.pttDownAt = 0; // Space is always hold-to-talk
      void this.pttStop();
    });

    $<HTMLFormElement>('say-form').onsubmit = async (e) => {
      e.preventDefault();
      const input = $<HTMLInputElement>('say-in');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      this.flash(await this.link.send({ action: 'say', text }));
    };

    $('conv-clear').onclick = () => {
      this.turns.clear();
      this.lastTurn = null;
      $('conv').innerHTML = '<li class="empty">Cleared.</li>';
    };
  }

  private typing(e: KeyboardEvent) {
    const t = e.target as HTMLElement | null;
    return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
  }

  /** Press: start, or - if a click already toggled the mic on - stop and send. */
  private pttPress() {
    if (!this.connected) return;
    if (this.listening && this.pttToggled) {
      this.pttToggled = false;
      this.pttDownAt = 0;
      void this.pttStop();
      return;
    }
    this.pttDownAt = performance.now();
    void this.pttStart();
  }

  /** Release after a hold sends the turn; a quick click leaves the mic on (toggle). */
  private pttRelease() {
    if (!this.pttDownAt) return;
    const held = performance.now() - this.pttDownAt;
    this.pttDownAt = 0;
    if (held < 300) {
      this.pttToggled = true;
      if (this.listening) this.setPhase('listening');
      return;
    }
    void this.pttStop();
  }

  private async pttStart() {
    if (this.pttBusy) return;
    this.pttBusy = true;
    if (!this.listening) this.setPhase('engaging');
    const ack = await this.link.send({ action: 'ptt', state: 'start' });
    this.pttBusy = false;
    if (!ack.ok && ack.message !== 'released before the mic started') {
      this.pttToggled = false;
      this.flash(ack);
      if (!this.listening) this.setPhase('idle');
    }
  }

  private async pttStop() {
    this.pttToggled = false;
    const ack = await this.link.send({ action: 'ptt', state: 'stop' });
    if (!ack.ok) this.flash(ack);
    if (!this.listening && this.phase === 'engaging') this.setPhase('idle');
  }

  private flashTimer?: number;
  private thinkingTimer?: number;
  private flash(a: Ack) {
    const el = $('ptt-msg');
    if (a.ok) {
      el.hidden = true;
      return;
    }
    el.textContent = a.message;
    el.hidden = false;
    clearTimeout(this.flashTimer);
    this.flashTimer = window.setTimeout(() => (el.hidden = true), 6000);
  }

  // ------------------------------------------------------------------ conversation

  private turnFor(id: string, at: number): Turn {
    let turn = this.turns.get(id);
    if (turn) return turn;
    const conv = $('conv');
    conv.querySelector('.empty')?.remove();
    const li = document.createElement('li');
    li.className = 'turn';
    li.innerHTML = `<div class="you pending"></div><div class="meta"></div>`;
    conv.appendChild(li);
    turn = {
      id, li, you: li.querySelector('.you')!, rex: null, meta: li.querySelector('.meta')!,
      startedAt: at, streamed: '', finalized: false, actions: [],
    };
    this.turns.set(id, turn);
    this.lastTurn = turn;
    while (conv.children.length > 60) conv.firstElementChild?.remove();
    return turn;
  }

  private rexBubble(turn: Turn, fresh: boolean) {
    if (!turn.rex || fresh) {
      const div = document.createElement('div');
      div.className = 'rex';
      turn.li.insertBefore(div, turn.meta);
      turn.rex = div;
    }
    return turn.rex;
  }

  private trackConversation(e: LiveEvent, replay: boolean) {
    const d = e.data;
    const id = typeof d.conversation_id === 'string' ? d.conversation_id : null;
    const at = e.wall ?? Date.now() / 1000;
    let turn: Turn | null = null;

    switch (e.topic) {
      case 'voice.listening.started':
        if (!id) return;
        turn = this.turnFor(id, at);
        turn.you.textContent = d.source === 'panel' ? '' : 'listening…';
        break;
      case 'transcription.interim':
      case 'transcription.final': {
        turn = (id && this.turns.get(id)) || this.lastTurn;
        if (turn && typeof d.text === 'string' && d.text && turn.you.classList.contains('pending')) turn.you.textContent = d.text;
        break;
      }
      case 'voice.listening.stopped':
        if (!id) return;
        turn = this.turnFor(id, at);
        turn.stoppedAt = at;
        turn.you.classList.remove('pending');
        turn.you.textContent = typeof d.transcript === 'string' && d.transcript ? d.transcript : '(nothing heard)';
        if (d.source === 'panel') turn.you.classList.add('typed');
        break;
      case 'intent.detected':
        turn = (id && this.turns.get(id)) || this.lastTurn;
        if (turn && d.intent_name) turn.actions.push(String(d.intent_name));
        break;
      case 'llm.response': {
        turn = (id && this.turns.get(id)) || this.lastTurn;
        if (!turn) return;
        const text = typeof d.text === 'string' ? d.text : '';
        const tools = Array.isArray(d.tool_calls) ? d.tool_calls : [];
        for (const t of tools) {
          const call = (t ?? {}) as Record<string, unknown>;
          const fn = call.function as Record<string, unknown> | undefined;
          const name = typeof call.name === 'string' ? call.name : typeof fn?.name === 'string' ? fn.name : null;
          if (name) turn.actions.push(name);
        }
        turn.firstReplyAt ??= at;
        if (!d.is_complete) {
          const bubble = this.rexBubble(turn, turn.finalized);
          turn.finalized = false;
          turn.streamed += text;
          bubble.textContent = turn.streamed;
        } else if (text) {
          const bubble = this.rexBubble(turn, turn.finalized);
          bubble.textContent = text;
          turn.streamed = '';
          turn.finalized = true;
        }
        break;
      }
      case 'speech.generation.started':
      case 'speech.synthesis.started':
        turn = (id && this.turns.get(id)) || this.lastTurn;
        if (turn) turn.speechAt ??= at;
        break;
      default:
        return;
    }
    if (turn) this.renderMeta(turn);
    if (!replay) {
      const conv = $('conv');
      conv.scrollTop = conv.scrollHeight;
    }
  }

  private renderMeta(t: Turn) {
    const bits: string[] = [clockTime(t.startedAt)];
    if (t.stoppedAt && t.firstReplyAt) bits.push(`reply ${Math.round((t.firstReplyAt - t.stoppedAt) * 1000)} ms`);
    if (t.stoppedAt && t.speechAt) bits.push(`voice ${Math.round((t.speechAt - t.stoppedAt) * 1000)} ms`);
    const actions = [...new Set(t.actions)].map((a) => `<span class="act">${esc(a)}</span>`).join('');
    t.meta.innerHTML = `${esc(bits.join(' · '))}${actions}`;
  }

  // ------------------------------------------------------------------ controls

  private bindControls() {
    document.addEventListener('click', (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-cli]');
      if (!b) return;
      void this.runCli(b.dataset.cli!);
    });
    $<HTMLFormElement>('music-form').onsubmit = (e) => {
      e.preventDefault();
      const q = $<HTMLInputElement>('music-q').value.trim();
      void this.runCli(q ? `play music ${q}` : 'play music');
    };
    $<HTMLInputElement>('music-q').oninput = () => this.renderLibrary();

    const cliIn = $<HTMLInputElement>('cli-in');
    $<HTMLFormElement>('cli-form').onsubmit = (e) => {
      e.preventDefault();
      const line = cliIn.value.trim();
      if (!line) return;
      this.cliHistory.push(line);
      this.cliIndex = -1;
      cliIn.value = '';
      void this.runCli(line);
    };
    cliIn.onkeydown = (e) => {
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      if (!this.cliHistory.length) return;
      e.preventDefault();
      const n = this.cliHistory.length;
      this.cliIndex = e.key === 'ArrowUp'
        ? (this.cliIndex < 0 ? n - 1 : Math.max(0, this.cliIndex - 1))
        : (this.cliIndex < 0 ? -1 : this.cliIndex + 1 >= n ? -1 : this.cliIndex + 1);
      cliIn.value = this.cliIndex < 0 ? '' : this.cliHistory[this.cliIndex];
    };
  }

  private async runCli(line: string) {
    this.addCliOut(`> ${line}`, false, true);
    const ack = await this.link.send({ action: 'cli', text: line });
    if (!ack.ok) this.addCliOut(ack.message, true);
  }

  private renderEyes() {
    $('eye-patterns').innerHTML = EYE_PATTERNS.map((p) => `<button class="chip" data-cli="eye pattern ${p}">${p}</button>`).join('');
  }

  private renderLibrary() {
    const q = $<HTMLInputElement>('music-q').value.trim().toLowerCase();
    const hits = q ? this.library.filter((t) => t.toLowerCase().includes(q)) : this.library;
    $('library').innerHTML = hits.slice(0, 200).map((t) =>
      `<li><button class="track" data-cli="play music ${esc(t)}" title="Play ${esc(t)}">${esc(t)}</button></li>`).join('');
    $('library-count').textContent = this.library.length
      ? `${hits.length} of ${this.library.length} tracks${q && !hits.length ? ' - Play still tries a semantic search' : ''}`
      : 'Library not loaded yet.';
  }

  private addCliOut(text: string, error: boolean, echo = false) {
    if (!text) return;
    const out = $('cli-out');
    const li = document.createElement('li');
    li.className = echo ? 'echo' : error ? 'err' : '';
    li.textContent = text;
    out.appendChild(li);
    while (out.children.length > 80) out.firstElementChild?.remove();
    out.scrollTop = out.scrollHeight;
  }

  private renderServices() {
    const names = Object.keys(this.services).sort();
    const bad = names.filter((n) => /ERROR|DEGRADED/.test(this.services[n].status));
    $('svc-summary').textContent = names.length ? `${names.length} reporting${bad.length ? `, ${bad.length} unhealthy` : ''}` : '';
    $('services').innerHTML = names.map((n) => {
      const s = this.services[n];
      const cls = /ERROR/.test(s.status) ? 'bad' : /DEGRADED|STOPP/.test(s.status) ? 'meh' : /RUNNING/.test(s.status) ? 'ok' : '';
      return `<li title="${esc(s.message)}"><span class="dot ${cls}"></span>${esc(n)}<small>${esc(s.status)}</small></li>`;
    }).join('') || '<li class="empty">No status reports yet.</li>';
  }

  // ------------------------------------------------------------------ logs

  private bindLogs() {
    document.querySelectorAll<HTMLButtonElement>('[data-kind]').forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.kind!;
        if (this.logKinds.has(k)) this.logKinds.delete(k);
        else this.logKinds.add(k);
        b.classList.toggle('on', this.logKinds.has(k));
        this.applyLogFilter();
      };
    });
    $<HTMLInputElement>('log-filter').oninput = () => this.applyLogFilter();
    $<HTMLSelectElement>('log-level').onchange = async (e) => {
      const level = (e.target as HTMLSelectElement).value;
      const ack = await this.link.send({ action: 'log_level', level });
      this.addLog({ t: Date.now() / 1000, level: ack.ok ? 'INFO' : 'WARNING', name: 'panel', msg: ack.message });
    };
    $('log-pause').onclick = (e) => {
      this.logPaused = !this.logPaused;
      (e.currentTarget as HTMLElement).textContent = this.logPaused ? 'Resume' : 'Pause';
      (e.currentTarget as HTMLElement).classList.toggle('on', this.logPaused);
      if (!this.logPaused) this.scrollLog();
    };
    $('log-clear').onclick = () => {
      $('log').innerHTML = '';
      this.errCount = 0;
      this.renderErrCount();
    };
  }

  private addLog(r: LogRecord, replay = false) {
    const li = document.createElement('li');
    li.className = `k-log lv-${r.level.toLowerCase()}`;
    const short = r.name.replace(/^cantina_os\.(services\.)?/, '');
    li.innerHTML = `<time>${clockTime(r.t)}</time><b>${esc(r.level.slice(0, 4))}</b><i>${esc(short)}</i><span>${esc(r.msg)}</span>`;
    li.dataset.text = `${r.level} ${r.name} ${r.msg}`.toLowerCase();
    this.appendLogRow(li, replay);
    if (!replay && (r.level === 'ERROR' || r.level === 'CRITICAL') && this.activeTab !== 'logs') {
      this.errCount++;
      this.renderErrCount();
    }
  }

  private addEventRow(e: LiveEvent, replay = false) {
    const li = document.createElement('li');
    li.className = 'k-event';
    const summary = JSON.stringify(compact(e.data));
    li.innerHTML = `<time>${e.wall ? clockTime(e.wall) : ''}</time><b>EVT</b><i>${esc(e.topic)}</i><span>${esc(summary.length > 400 ? summary.slice(0, 400) + '…' : summary)}</span>`;
    li.dataset.text = `${e.topic} ${summary}`.toLowerCase();
    this.appendLogRow(li, replay);
  }

  private appendLogRow(li: HTMLLIElement, replay: boolean) {
    const list = $('log');
    li.hidden = !this.rowVisible(li);
    list.appendChild(li);
    while (list.children.length > MAX_LOG_ROWS) list.firstElementChild?.remove();
    if (!replay && !this.logPaused) this.scrollLog();
  }

  private rowVisible(li: HTMLElement) {
    const kind = li.classList.contains('k-event') ? 'event' : 'log';
    if (!this.logKinds.has(kind)) return false;
    const q = $<HTMLInputElement>('log-filter').value.trim().toLowerCase();
    return !q || (li.dataset.text ?? '').includes(q);
  }

  private applyLogFilter() {
    for (const li of Array.from($('log').children) as HTMLElement[]) li.hidden = !this.rowVisible(li);
    this.scrollLog();
  }

  private scrollLog() {
    if (this.logPaused) return;
    const list = $('log');
    list.scrollTop = list.scrollHeight;
  }

  private renderErrCount() {
    const el = $('err-count');
    el.hidden = this.errCount === 0;
    el.textContent = String(this.errCount);
  }
}
