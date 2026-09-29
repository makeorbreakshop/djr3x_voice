/**
 * The control panel around the 3D droid: operating mode, push-to-talk, typed turns, the
 * conversation, the Drive panel (brain/autonomy/freeze, alive layers, emotes, outputs),
 * music / DJ, an advanced console, service health, and the live log + event stream.
 *
 * Every action is a typed command to the r3x gateway and waits for its ack; state comes from
 * the gateway's retained state. The SimBridge LiveLink is only read here, for CantinaOS's own
 * log lines (the 3D view follows the gateway's performer frames, see main.ts).
 */

import { GatewayClient, gatewayUrl } from './gateway';
import type { Ack, Command, Event as R3xEvent, EventMeta, Hello, RetainedState } from './gateway';
import type { Engagement } from './generated/Engagement';
import type { OperatingMode } from './generated/OperatingMode';
import { accessToken, LiveLink, LogRecord } from './link';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const clockTime = (epochS: number) =>
  new Date(epochS * 1000).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });

const EYE_PATTERNS = ['idle', 'engaged', 'listening', 'thinking', 'speaking', 'happy', 'sad', 'angry', 'surprised', 'flash', 'startup', 'error'];
/** Too chatty for the event feed by default. */
const NOISY_TOPICS = new Set(['conversation.reply_delta', 'conversation.transcript']);
const MAX_LOG_ROWS = 1500;
const topicOf = (e: R3xEvent) => `${e.domain}.${e.type}`;
const ok = (a: Ack) => a.status === 'accepted';
const reason = (a: Ack) => (a.status === 'rejected' ? a.reason : '');
/** tracing's level names for the panel's DEBUG/INFO/WARNING/ERROR select. */
const TRACING_LEVEL: Record<string, string> = { DEBUG: 'debug', INFO: 'info', WARNING: 'warn', ERROR: 'error' };

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
  readonly gw: GatewayClient;
  private connected = false;
  private mode = 'IDLE';
  private emotes: string[] = [];
  private phase: Phase = 'offline';
  private listening = false;
  private pttDownAt = 0;
  private pttToggled = false;
  private pttBusy = false;
  private spaceDown = false;

  private turns = new Map<string, Turn>();
  private lastTurn: Turn | null = null;

  private library: string[] = [];
  private logPaused = false;
  private logKinds = new Set(['log', 'event']);
  private errCount = 0;
  private activeTab = 'talk';
  private cliHistory: string[] = [];
  private cliIndex = -1;

  constructor(link: LiveLink) {
    this.gw = new GatewayClient(gatewayUrl(accessToken()));
    this.gw.subscribe({
      onHello: (h) => this.onHello(h),
      onState: (s) => this.render(s),
      onEvent: (e, m) => this.onEvent(e, m),
      onStatus: (on) => this.onStatus(on),
      onLog: (l, wall) => this.addLog({ t: wall, level: l.level, name: l.target, msg: l.message }),
    });
    // CantinaOS's own log lines (SimBridge); read-only.
    link.subscribe({
      onHello: (h) => {
        for (const r of h.logs ?? []) this.addLog(r, true);
        this.scrollLog();
      },
      onLog: (r) => this.addLog(r),
    });
    this.bindTabs();
    this.bindTalk();
    this.bindControls();
    this.bindDrive();
    this.bindLogs();
    this.renderEyes();
    this.setPhase('offline');
    if (!new URLSearchParams(location.search).has('offline')) this.gw.start();
  }

  /** Send a typed command; a rejection is shown where the operator is looking. */
  private async cmd(c: Command): Promise<Ack> {
    const a = await this.gw.send(c);
    if (!ok(a)) this.toast(reason(a));
    return a;
  }

  // ------------------------------------------------------------------ link

  private onStatus(on: boolean) {
    this.connected = on;
    document.body.classList.toggle('live', on);
    $('offline-note').hidden = on;
    for (const id of ['ptt', 'say-in', 'say-send']) ($(id) as HTMLButtonElement).disabled = !on;
    const pill = $('st-gw');
    pill.textContent = on ? 'R3X' : 'NO R3X';
    pill.classList.toggle('on', on);
    if (!on) {
      this.listening = false;
      this.pttToggled = false;
      this.setPhase('offline');
      this.addLog({ t: Date.now() / 1000, level: 'WARNING', name: 'panel', msg: 'Lost connection to the r3x gateway - retrying every 2 s' });
    }
  }

  private onHello(h: Hello) {
    this.emotes = h.profile?.emotes ?? [];
    this.render(h.state);
  }

  /** Everything the panel shows about state comes from here. */
  private render(s: RetainedState) {
    this.setMode(s.engagement.engagement);
    this.setStage(s);
    const listening = s.conversation.phase === 'listening';
    if (listening !== this.listening) {
      this.listening = listening;
      if (!listening) this.pttToggled = false;
    }
    if (this.phase !== 'engaging' || listening) this.setPhase(s.conversation.phase);
    if (s.music.library.join('\n') !== this.library.join('\n')) {
      this.library = s.music.library;
      this.renderLibrary();
    }
    this.setNowPlaying(s.music.playing ? s.music.track?.title || 'Unknown track' : null);
    this.setDj(s.dj.active);
    this.renderServices(s);
    this.renderDrive(s);
  }

  private onEvent(e: R3xEvent, m: EventMeta) {
    if (e.domain === 'conversation' && e.type === 'reply') {
      // A turn that ends without speech (TTS off or failed) must not stay on "thinking".
      clearTimeout(this.thinkingTimer);
      this.thinkingTimer = window.setTimeout(() => {
        if (this.phase === 'thinking') this.setPhase('idle');
      }, 6000);
    }
    if (e.domain === 'ops' && e.type === 'console') this.addCliOut(e.message, e.is_error);
    this.trackConversation(e, m);
    if (!NOISY_TOPICS.has(topicOf(e))) this.addEventRow(e, m);
  }

  // ------------------------------------------------------------------ state

  /** Engagement (STARTUP/IDLE/AMBIENT/INTERACTIVE). */
  private setMode(m: Engagement) {
    this.mode = m.toUpperCase();
    document.querySelectorAll<HTMLButtonElement>('[data-engage]').forEach((b) =>
      b.classList.toggle('on', b.dataset.engage === m));
  }

  /** Operating mode (Show/Bench/Studio). */
  private setStage(s: RetainedState) {
    document.querySelectorAll<HTMLButtonElement>('[data-stage-mode]').forEach((b) =>
      b.classList.toggle('on', b.dataset.stageMode === s.stage.mode));
    const brainOff = !s.stage.brain;
    $('brain-off').hidden = !brainOff || !this.connected;
    for (const id of ['ptt', 'say-in', 'say-send']) ($(id) as HTMLButtonElement).disabled = !this.connected || brainOff;
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
      this.flash(await this.gw.send({ class: 'intent', type: 'say', text }));
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
    const ack = await this.gw.send({ class: 'intent', type: 'ptt_start' });
    this.pttBusy = false;
    if (!ok(ack) && reason(ack) !== 'released before the mic started') {
      this.pttToggled = false;
      this.flash(ack);
      if (!this.listening) this.setPhase('idle');
    }
  }

  private async pttStop() {
    this.pttToggled = false;
    const ack = await this.gw.send({ class: 'intent', type: 'ptt_stop' });
    if (!ok(ack)) this.flash(ack);
    if (!this.listening && this.phase === 'engaging') this.setPhase('idle');
  }

  private flashTimer?: number;
  private thinkingTimer?: number;
  private flash(a: Ack) {
    const el = $('ptt-msg');
    if (ok(a)) {
      el.hidden = true;
      return;
    }
    el.textContent = reason(a);
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

  private trackConversation(e: R3xEvent, m: EventMeta) {
    if (e.domain !== 'conversation') return;
    const id = m.conversationId;
    const at = m.wall;
    const known = () => (id && this.turns.get(id)) || this.lastTurn;
    let turn: Turn | null = null;

    switch (e.type) {
      case 'listening_started':
        if (!id) return;
        turn = this.turnFor(id, at);
        turn.you.textContent = 'listening…';
        break;
      case 'transcript':
        turn = known();
        if (turn && e.text && turn.you.classList.contains('pending')) turn.you.textContent = e.text;
        break;
      case 'listening_stopped':
        if (!id) return;
        turn = this.turnFor(id, at);
        turn.stoppedAt = at;
        turn.you.classList.remove('pending');
        turn.you.textContent = e.transcript || '(nothing heard)';
        break;
      case 'intent_detected':
        turn = known();
        if (turn) turn.actions.push(e.tool);
        break;
      case 'reply_delta': {
        turn = known();
        if (!turn) return;
        turn.firstReplyAt ??= at;
        const bubble = this.rexBubble(turn, turn.finalized);
        turn.finalized = false;
        turn.streamed += e.text;
        bubble.textContent = turn.streamed;
        break;
      }
      case 'reply':
        turn = known();
        if (!turn || !e.text) return;
        turn.firstReplyAt ??= at;
        this.rexBubble(turn, turn.finalized).textContent = e.text;
        turn.streamed = '';
        turn.finalized = true;
        break;
      case 'speech_started':
        turn = known();
        if (turn) turn.speechAt ??= at;
        break;
      default:
        return;
    }
    if (turn) this.renderMeta(turn);
    const conv = $('conv');
    conv.scrollTop = conv.scrollHeight;
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
      const el = e.target as HTMLElement;
      const b = el.closest<HTMLButtonElement>('[data-cli],[data-engage],[data-stage-mode],[data-music],[data-dj],[data-play]');
      if (!b) return;
      const d = b.dataset;
      if (d.cli) void this.runCli(d.cli);
      else if (d.engage) void this.cmd({ class: 'stage', type: 'set_engagement', engagement: d.engage as Engagement });
      else if (d.stageMode) void this.cmd({ class: 'stage', type: 'set_mode', mode: d.stageMode as OperatingMode });
      else if (d.music === 'play') void this.cmd({ class: 'intent', type: 'music', action: 'play' });
      else if (d.music === 'stop') void this.cmd({ class: 'intent', type: 'music', action: 'stop' });
      else if (d.music === 'next') void this.cmd({ class: 'intent', type: 'music', action: 'next' });
      else if (d.play) void this.cmd({ class: 'intent', type: 'music', action: 'play', query: d.play });
      else if (d.dj) void this.cmd({ class: 'intent', type: 'dj', active: d.dj === 'start' });
    });
    $<HTMLFormElement>('music-form').onsubmit = (e) => {
      e.preventDefault();
      const q = $<HTMLInputElement>('music-q').value.trim();
      void this.cmd({ class: 'intent', type: 'music', action: 'play', ...(q ? { query: q } : {}) });
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

  /** The legacy console (CantinaOS commands with no typed equivalent). */
  private async runCli(line: string) {
    this.addCliOut(`> ${line}`, false, true);
    const ack = await this.gw.send({ class: 'intent', type: 'console', line });
    if (!ok(ack)) this.addCliOut(reason(ack), true);
  }

  private toastTimer?: number;
  private toast(msg: string) {
    const el = $('toast');
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => (el.hidden = true), 5000);
  }

  // ------------------------------------------------------------------ drive

  private bindDrive() {
    $('drive').addEventListener('click', (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button');
      const s = this.gw.state?.stage;
      if (!b || !s) return;
      const d = b.dataset;
      if (d.toggle === 'brain') void this.cmd({ class: 'stage', type: 'set_brain', enabled: !s.brain });
      else if (d.toggle === 'autonomy') void this.cmd({ class: 'stage', type: 'set_autonomy', enabled: !s.autonomy });
      else if (d.toggle === 'freeze') void this.cmd({ class: 'stage', type: 'freeze', on: !s.frozen });
      else if (d.layer) void this.cmd({ class: 'stage', type: 'set_layer', layer: d.layer, enabled: !s.layers[d.layer] });
      else if (d.output) void this.cmd({ class: 'stage', type: 'set_output', output: d.output, enabled: !s.outputs[d.output] });
      else if (d.emote) void this.cmd({ class: 'perf', type: 'emote', slot: Number(d.emote) });
      else if (d.outputs) {
        const enabled = d.outputs === 'on';
        for (const output of Object.keys(s.outputs)) {
          if (s.outputs[output] !== enabled) void this.cmd({ class: 'stage', type: 'set_output', output, enabled });
        }
      }
    });
  }

  private renderDrive(s: RetainedState) {
    const st = s.stage;
    const chip = (attr: string, value: string, label: string, on: boolean, title = '') =>
      `<button class="chip${on ? ' on' : ''}" data-${attr}="${esc(value)}" aria-pressed="${on}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</button>`;
    $('drive-toggles').innerHTML = [
      chip('toggle', 'brain', 'Brain (voice + LLM)', st.brain, 'Accept spoken and typed turns'),
      chip('toggle', 'autonomy', 'Autonomy (idle + DJ)', st.autonomy, 'Idle policy and DJ autonomy'),
      chip('toggle', 'freeze', st.frozen ? 'Frozen - release' : 'Freeze motion', st.frozen, 'Stop every run and refuse new ones'),
    ].join('');
    $('drive-layers').innerHTML = Object.keys(st.layers).map((l) => chip('layer', l, l.replace(/_/g, ' '), st.layers[l])).join('')
      || '<span class="hint">No alive layers in the profile.</span>';
    $('drive-emotes').innerHTML = this.emotes.map((cue, i) => `<button class="chip" data-emote="${i}" title="slot ${i + 1}">${esc(cue.replace(/_/g, ' '))}</button>`).join('')
      || '<span class="hint">No emotes in the profile.</span>';
    const outs = Object.keys(st.outputs);
    $('drive-outputs').innerHTML = outs.map((o) => chip('output', o, o, st.outputs[o])).join('');
    $('drive-out-summary').textContent = `${outs.filter((o) => st.outputs[o]).length} of ${outs.length} on`;
    for (const b of Array.from($('drive-emotes').querySelectorAll('button'))) (b as HTMLButtonElement).disabled = st.frozen;
  }

  private renderEyes() {
    $('eye-patterns').innerHTML = EYE_PATTERNS.map((p) => `<button class="chip" data-cli="eye pattern ${p}">${p}</button>`).join('');
  }

  private renderLibrary() {
    const q = $<HTMLInputElement>('music-q').value.trim().toLowerCase();
    const hits = q ? this.library.filter((t) => t.toLowerCase().includes(q)) : this.library;
    $('library').innerHTML = hits.slice(0, 200).map((t) =>
      `<li><button class="track" data-play="${esc(t)}" title="Play ${esc(t)}">${esc(t)}</button></li>`).join('');
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

  private renderServices(s: RetainedState) {
    const svc = s.services.services;
    const names = Object.keys(svc).sort();
    const bad = names.filter((n) => svc[n].status === 'error' || svc[n].status === 'degraded');
    $('svc-summary').textContent = names.length ? `${names.length} reporting${bad.length ? `, ${bad.length} unhealthy` : ''}` : '';
    $('services').innerHTML = names.map((n) => {
      const h = svc[n];
      const cls = h.status === 'error' ? 'bad' : h.status === 'degraded' || h.status === 'stopped' ? 'meh' : h.status === 'running' ? 'ok' : '';
      return `<li title="${esc(h.detail ?? '')}"><span class="dot ${cls}"></span>${esc(n.replace(/^cantina\//, ''))}<small>${esc(h.status)}</small></li>`;
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
      const ack = await this.gw.send({ class: 'telemetry', type: 'set_log_level', level: TRACING_LEVEL[level] ?? level });
      this.addLog({ t: Date.now() / 1000, level: ok(ack) ? 'INFO' : 'WARNING', name: 'panel', msg: ok(ack) ? `r3x logs at ${level} and above` : reason(ack) });
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

  private addEventRow(e: R3xEvent, m: EventMeta) {
    const li = document.createElement('li');
    li.className = 'k-event';
    const topic = topicOf(e);
    const { domain: _d, type: _t, ...rest } = e as Record<string, unknown>;
    const summary = JSON.stringify(rest);
    li.innerHTML = `<time>${clockTime(m.wall)}</time><b>EVT</b><i>${esc(topic)}</i><span>${esc(summary.length > 400 ? summary.slice(0, 400) + '…' : summary)}</span>`;
    li.dataset.text = `${topic} ${summary}`.toLowerCase();
    this.appendLogRow(li, false);
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
