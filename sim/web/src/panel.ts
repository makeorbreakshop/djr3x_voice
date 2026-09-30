/**
 * The R3X panel (right): what he does. The Show / Bench / Studio switch picks the tabs -
 * Show: Talk (push-to-talk, typed turns, the conversation), Perform (emotes and shows, placed
 * by main.ts; DJ; music), Behaviour (brain, autonomy, alive layers, gaze, engagement);
 * Bench: Rig (Home, outputs by body region, joints, calibrate), Test (the same emotes and
 * shows); every mode: System (services, logs + events, debug views). The Scene panel (left,
 * scenepanel.ts) is how the viewer sees him and never changes his state.
 *
 * Every action is a typed command to the r3x gateway and waits for its ack; state comes from
 * the gateway's retained state, and the runtime's log lines come from the gateway too. The
 * SimBridge LiveLink (legacy CantinaOS only) is read here for CantinaOS's own log lines.
 */

import { GatewayClient, gatewayUrl } from './gateway';
import type { Ack, Command, Event as R3xEvent, EventMeta, Hello, RetainedState } from './gateway';
import type { Engagement } from './generated/Engagement';
import type { OperatingMode } from './generated/OperatingMode';
import { accessToken, LiveLink, LogRecord } from './link';
import { mountCalibrate } from './calibrate';
import * as Ptt from './ptt';
import { BodyRegions } from './regions';
import type { RobotProfile } from './generated/RobotProfile';

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
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
/** The first tab of each operating mode, until the operator picks another. */
const DEFAULT_TAB: Record<OperatingMode, string> = { show: 'talk', bench: 'rig', studio: 'system' };
const TABS_KEY = 'r3x.tabs';

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
  /** Show / Bench / Studio: the runtime's, or the local pick while offline. */
  private stageMode: OperatingMode = 'show';
  private regions: BodyRegions | null = null;
  private tabFor: Record<string, string> = { ...DEFAULT_TAB };
  private phase: Phase = 'offline';
  private ptt: Ptt.PttState = Ptt.initial();
  /** Where `music.position_s` was, by the page clock (for the DJ countdown). */
  private musicAnchor = { pos: 0, at: 0, t: -1 };
  /** Element that shows the next console reply (a DJ test button's outcome). */
  private consoleTo: string | null = null;

  private turns = new Map<string, Turn>();
  private lastTurn: Turn | null = null;

  private library: string[] = [];
  private logPaused = false;
  private logKinds = new Set(['log', 'event']);
  private errCount = 0;
  private activeTab = '';
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
    mountCalibrate(this.gw, $('drive'), (m) => this.toast(m));
    this.bindLogs();
    this.renderEyes();
    this.applyMode('show', true);
    this.setPhase('offline');
    this.pttInput({ kind: 'link', connected: false });
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
    pill.textContent = on ? 'Connected' : 'Offline';
    pill.classList.toggle('on', on);
    this.pttInput({ kind: 'link', connected: on });
    if (!on) {
      this.setPhase('offline');
      this.addLog({ t: Date.now() / 1000, level: 'WARNING', name: 'panel', msg: 'Lost connection to the r3x gateway - retrying every 2 s' });
    }
  }

  private onHello(h: Hello) {
    this.regions = h.profile ? new BodyRegions(h.profile as RobotProfile) : null;
    this.render(h.state);
  }

  /** Everything the panel shows about state comes from here. */
  private render(s: RetainedState) {
    this.setMode(s.engagement.engagement);
    this.setStage(s);
    this.pttInput({ kind: 'server', phase: s.conversation.phase, owner: s.conversation.ptt_owner ?? null });
    this.setPhase(this.ptt.pending === 'start' && s.conversation.phase !== 'listening' ? 'engaging' : s.conversation.phase);
    if (s.music.library.join('\n') !== this.library.join('\n')) {
      this.library = s.music.library;
      this.renderLibrary();
    }
    this.setNowPlaying(s.music.playing ? s.music.track?.title || 'Unknown track' : null);
    this.setDj(s);
    this.renderServices(s);
    this.renderDrive(s);
  }

  private onEvent(e: R3xEvent, m: EventMeta) {
    if (e.domain === 'conversation' && e.type === 'reply') {
      // A turn that ends without speech (TTS off or failed) must not stay on "thinking".
      clearTimeout(this.thinkingTimer);
      this.thinkingTimer = window.setTimeout(() => {
        if (this.phase !== 'thinking') return;
        this.setPhase('idle');
        this.pttInput({ kind: 'server', phase: 'idle', owner: this.ptt.owner });
      }, 6000);
    }
    if (e.domain === 'ops' && e.type === 'console') {
      this.addCliOut(e.message, e.is_error);
      if (this.consoleTo) {
        const el = $(this.consoleTo);
        el.textContent = e.message;
        el.classList.toggle('err', e.is_error);
        this.consoleTo = null;
      }
    }
    this.trackConversation(e, m);
    if (!NOISY_TOPICS.has(topicOf(e))) this.addEventRow(e, m);
  }

  // ------------------------------------------------------------------ state

  /** Engagement (STARTUP/IDLE/AMBIENT/INTERACTIVE). */
  private setMode(m: Engagement) {
    this.mode = m.toUpperCase();
    $('eng-now').textContent = title(m);
    document.querySelectorAll<HTMLButtonElement>('[data-engage]').forEach((b) =>
      b.classList.toggle('on', b.dataset.engage === m));
  }

  /** Operating mode (Show/Bench/Studio). */
  private setStage(s: RetainedState) {
    if (s.stage.mode !== this.stageMode) this.applyMode(s.stage.mode);
    const brainOff = !s.stage.brain;
    for (const id of ['ptt', 'say-in', 'say-send']) ($(id) as HTMLButtonElement).disabled = !this.connected || brainOff;
    if (this.ptt.enabled === brainOff) this.pttInput({ kind: 'enabled', enabled: !brainOff });
  }

  private setPhase(p: Phase) {
    if (!this.connected) p = 'offline';
    this.phase = p;
    this.renderChip();
  }

  /** The viewport's one state chip: mode, and the conversation phase when it says something. */
  private renderChip() {
    const p = this.phase;
    const labels: Record<Phase, string> = {
      offline: 'Offline', idle: title(this.mode), engaging: 'Engaging…', listening: 'Listening',
      thinking: 'Thinking', speaking: 'Speaking',
    };
    const badge = $('state-badge');
    const mode = title(this.stageMode);
    if (!this.connected) {
      badge.dataset.state = 'offline';
      badge.textContent = this.stageMode === 'studio' ? 'Studio · Offline' : 'Offline';
    } else if (this.stageMode === 'show') {
      badge.dataset.state = p;
      badge.textContent = `${mode} · ${labels[p]}`;
    } else {
      badge.dataset.state = p === 'idle' ? this.stageMode : p;
      badge.textContent = p === 'idle' ? mode : `${mode} · ${labels[p]}`;
    }
  }

  /** Offline there is no StageManager: the page's own mode pick (main.ts). */
  setLocalMode(mode: OperatingMode) {
    if (!this.connected) this.applyMode(mode);
  }

  /**
   * Show / Bench / Studio: the switch, the tabs that mode has (each mode remembers its last
   * tab), and where the one set of emote/show controls sits (Perform in Show, Test in Bench).
   */
  private applyMode(mode: OperatingMode, boot = false) {
    this.stageMode = mode;
    document.body.dataset.mode = mode;
    document.querySelectorAll<HTMLButtonElement>('[data-stage-mode]').forEach((b) => {
      const on = b.dataset.stageMode === mode;
      b.classList.toggle('on', on);
      b.setAttribute('aria-checked', String(on));
    });
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) =>
      (b.hidden = !(b.dataset.modes ?? '').split(' ').includes(mode)));
    const slot = $(mode === 'bench' ? 'test-slot' : 'perform-slot');
    const shared = $('shared-perf');
    if (shared.parentElement !== slot) slot.appendChild(shared);
    if (boot) {
      try {
        Object.assign(this.tabFor, JSON.parse(localStorage.getItem(TABS_KEY) ?? '{}'));
      } catch {
        /* storage blocked */
      }
    }
    const want = this.tabFor[mode];
    const ok = (t: string) => !!document.querySelector<HTMLButtonElement>(`[data-tab="${t}"]:not([hidden])`);
    this.showTab(ok(want) ? want : DEFAULT_TAB[mode]);
    this.renderChip();
    window.dispatchEvent(new CustomEvent('r3x:mode', { detail: mode }));
  }

  /** Feed the push-to-talk machine; send what it asks for. True when a key event was ours. */
  private pttInput(i: Ptt.PttInput): boolean {
    const r = Ptt.step(this.ptt, i);
    this.ptt = r.state;
    if (r.send) void this.pttSend(r.send);
    const v = Ptt.view(this.ptt);
    const b = $('ptt');
    b.dataset.state = v.look;
    b.querySelector('.ptt-label')!.textContent = v.label;
    b.querySelector('.ptt-hint')!.textContent = v.hint;
    return !!r.handled;
  }

  private async pttSend(type: Ptt.PttSend) {
    if (type === 'ptt_start') this.setPhase('engaging');
    const ack = await this.gw.send({ class: 'intent', type });
    // A stop's ack can land after "(nothing heard)": only a start clears the message.
    if (!ok(ack) || type === 'ptt_start') this.flash(ack);
    this.pttInput({ kind: 'ack', of: type === 'ptt_start' ? 'start' : 'stop', ok: ok(ack) });
    if (this.phase === 'engaging' && this.gw.state) this.setPhase(this.gw.state.conversation.phase);
  }

  private setNowPlaying(track: string | null) {
    $('now-track').textContent = track || 'Nothing playing';
    $('now-state').classList.toggle('on', Boolean(track));
  }

  private setDj(s: RetainedState) {
    const { dj, music } = s;
    const el = $('dj-state');
    el.textContent = dj.active ? 'on' : 'off';
    el.classList.toggle('on', dj.active);
    // A new anchor only when the engine published one (other domains re-render with the old).
    if (music.position_t !== this.musicAnchor.t) this.musicAnchor = { pos: music.position_s, at: performance.now(), t: music.position_t };
    $('dj-info').hidden = !dj.active;
    $('dj-now').textContent = dj.current?.title ?? '-';
    $('dj-next').textContent = dj.next?.title ?? 'not picked yet';
    const lines: Record<string, string> = { none: 'none', writing: 'Claude is writing it…', synthesizing: 'synthesising…', ready: 'cached, ready' };
    $('dj-line').textContent = lines[dj.commentary] ?? dj.commentary;
    $('dj-line').classList.toggle('on', dj.commentary === 'ready');
    this.renderDjCountdown();
  }

  /** Time until the ending-soon mark (the transition), or the running plan step. */
  private renderDjCountdown() {
    const s = this.gw.state;
    if (!s?.dj.active) return;
    const el = $('dj-when');
    if (s.dj.step) {
      el.textContent = `running: ${s.dj.step.replace(/_/g, ' ')}`;
      return;
    }
    const m = s.music;
    if (!m.playing || m.ending_at_s == null) {
      el.textContent = m.playing ? 'track too short for a transition mark' : 'nothing playing';
      return;
    }
    const pos = this.musicAnchor.pos + (m.paused ? 0 : (performance.now() - this.musicAnchor.at) / 1000);
    const left = Math.round(m.ending_at_s - pos);
    el.textContent = left > 0 ? `in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : 'now';
  }

  // ------------------------------------------------------------------ tabs

  private bindTabs() {
    const tabs = () => Array.from(document.querySelectorAll<HTMLButtonElement>('[data-tab]:not([hidden])'));
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => {
      const body = document.querySelector<HTMLElement>(`[data-body="${b.dataset.tab}"]`)!;
      body.id ||= `body-${b.dataset.tab}`;
      body.setAttribute('aria-labelledby', (b.id = `tab-${b.dataset.tab}`));
      b.setAttribute('aria-controls', body.id);
      b.onclick = () => this.pickTab(b.dataset.tab!);
      // Arrow keys move between the visible tabs (one tab stop for the whole strip).
      b.onkeydown = (e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const list = tabs();
        const next = list[(list.indexOf(b) + (e.key === 'ArrowRight' ? 1 : list.length - 1)) % list.length];
        e.preventDefault();
        next.focus();
        this.pickTab(next.dataset.tab!);
      };
    });
  }

  /** The operator picked a tab: remembered for the current mode. */
  private pickTab(name: string) {
    this.tabFor[this.stageMode] = name;
    try {
      localStorage.setItem(TABS_KEY, JSON.stringify(this.tabFor));
    } catch {
      /* storage blocked */
    }
    this.showTab(name);
  }

  private showTab(name: string) {
    if (name === this.activeTab) return;
    this.activeTab = name;
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll<HTMLElement>('[data-body]').forEach((el) => (el.hidden = el.dataset.body !== name));
    if (name === 'system') {
      this.errCount = 0;
      this.renderErrCount();
      this.scrollLog();
    }
  }

  // ------------------------------------------------------------------ talk

  private bindTalk() {
    const ptt = $<HTMLButtonElement>('ptt');
    // On pointerdown, not click: the same instant as the OS mouse press, so the runtime's
    // click-anywhere source (./r3x --click-anywhere) sees the panel's request and stands down.
    ptt.addEventListener('pointerdown', (e) => {
      if (e.button === 0) this.pttInput({ kind: 'click' });
    });
    // Keyboard activation (Enter on the focused button); pointer clicks were handled above.
    ptt.addEventListener('click', (e) => {
      if (e.detail === 0) this.pttInput({ kind: 'click' });
    });
    ptt.addEventListener('contextmenu', (e) => e.preventDefault());

    // Space anywhere (except while typing) is hold-to-talk.
    addEventListener('keydown', (e) => {
      if (e.code !== 'Space') return;
      if (this.pttInput({ kind: 'space_down', repeat: e.repeat, typing: this.typing(e) })) e.preventDefault();
    });
    addEventListener('keyup', (e) => {
      if (e.code !== 'Space' || this.typing(e)) return;
      e.preventDefault(); // a focused button must not also "click" on Space
      this.pttInput({ kind: 'space_up' });
    });
    window.setInterval(() => this.renderDjCountdown(), 1000);

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

  private flashTimer?: number;
  private thinkingTimer?: number;
  private flash(a: Ack) {
    if (ok(a)) {
      $('ptt-msg').hidden = true;
      return;
    }
    this.note(reason(a));
  }

  private note(msg: string) {
    const el = $('ptt-msg');
    el.textContent = msg;
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
        if (!e.transcript) this.note('(nothing heard) - speak once the button turns red, then click to send');
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
      const b = el.closest<HTMLButtonElement>('[data-cli],[data-eye],[data-engage],[data-stage-mode],[data-music],[data-dj],[data-play]');
      if (!b) return;
      const d = b.dataset;
      if (d.cli) {
        if (d.cliOut) this.consoleTo = d.cliOut;
        void this.runCli(d.cli);
      }
      else if (d.eye) void this.cmd({ class: 'perf', type: 'eyes', pattern: d.eye });
      else if (d.engage) void this.cmd({ class: 'stage', type: 'set_engagement', engagement: d.engage as Engagement });
      else if (d.stageMode) {
        // Focus left on a mode button must not re-send it on a later Space/Enter.
        b.blur();
        if (this.gw.state?.stage.mode !== d.stageMode) void this.cmd({ class: 'stage', type: 'set_mode', mode: d.stageMode as OperatingMode });
      }
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

  /** The runtime's command console: the same parser as r3x-cli; the reply is an `ops.console` event. */
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
    $('panel').addEventListener('click', (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button');
      const s = this.gw.state?.stage;
      if (!b || !s) return;
      const d = b.dataset;
      if (d.toggle === 'brain') void this.cmd({ class: 'stage', type: 'set_brain', enabled: !s.brain });
      else if (d.toggle === 'autonomy') void this.cmd({ class: 'stage', type: 'set_autonomy', enabled: !s.autonomy });
      else if (d.layer) void this.cmd({ class: 'stage', type: 'set_layer', layer: d.layer, enabled: !s.layers[d.layer] });
      else if (d.output) void this.cmd({ class: 'stage', type: 'set_output', output: d.output, enabled: !s.outputs[d.output] });
      else if (d.group) {
        // All on, unless they already are: then all off.
        const names = this.outputGroups(s).find(([r]) => r === d.group)?.[1] ?? [];
        const enabled = !names.every((o) => s.outputs[o]);
        for (const output of names) {
          if (s.outputs[output] !== enabled) void this.cmd({ class: 'stage', type: 'set_output', output, enabled });
        }
      }
    });
  }

  private renderDrive(s: RetainedState) {
    // The chips are re-rendered on every state update: keep keyboard focus on the same control.
    const f = document.activeElement as HTMLElement | null;
    const key = f?.closest('#drive-toggles, #drive-layers, #drive-outputs')
      ? ['toggle', 'layer', 'output', 'group'].map((k) => (f.dataset[k] ? `[data-${k}="${CSS.escape(f.dataset[k]!)}"]` : '')).join('')
      : '';
    this.drawDrive(s);
    if (key) document.querySelector<HTMLElement>(key)?.focus();
  }

  private drawDrive(s: RetainedState) {
    const st = s.stage;
    const chip = (attr: string, value: string, label: string, on: boolean, title = '') =>
      `<button class="chip${on ? ' on' : ''}" data-${attr}="${esc(value)}" aria-pressed="${on}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</button>`;
    $('drive-toggles').innerHTML = [
      chip('toggle', 'brain', 'Brain', st.brain, 'Voice + LLM: accept spoken and typed turns'),
      chip('toggle', 'autonomy', 'Autonomy', st.autonomy, 'Idle policy and DJ autonomy'),
    ].join('');
    $('drive-layers').innerHTML = Object.keys(st.layers).map((l) => chip('layer', l, l.replace(/_/g, ' '), st.layers[l], 'Procedural motion')).join('')
      || '<span class="hint">No alive layers in the profile.</span>';
    const outs = Object.keys(st.outputs);
    $('drive-outputs').innerHTML = this.outputGroups(st).map(([region, names]) => {
      const n = names.filter((o) => st.outputs[o]).length;
      const all = n === names.length;
      return `<div class="out-group"><div class="og-head"><span>${esc(region)}</span><small>${n} / ${names.length}</small>
        <button class="link" data-group="${esc(region)}" aria-label="${all ? 'All off' : 'All on'}: ${esc(region)}">${all ? 'all off' : 'all on'}</button></div>
        <div class="chips">${names.map((o) => chip('output', o, o, st.outputs[o])).join('')}</div></div>`;
    }).join('');
    $('drive-out-summary').textContent = outs.length ? `${outs.filter((o) => st.outputs[o]).length} of ${outs.length} on` : '';
  }

  /** The outputs by body region (regions.ts), in the profile's order. */
  private outputGroups(st: { outputs: Record<string, boolean> }) {
    const outs = Object.keys(st.outputs);
    return this.regions ? this.regions.group(outs, (o) => this.regions!.ofOutput(o)) : [['Outputs', outs] as [string, string[]]];
  }

  private renderEyes() {
    $('eye-patterns').innerHTML = EYE_PATTERNS.map((p) => `<button class="chip" data-eye="${p}" title="Face pattern until the interaction state changes">${p}</button>`).join('');
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
        b.setAttribute('aria-pressed', String(this.logKinds.has(k)));
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
    if (!replay && (r.level === 'ERROR' || r.level === 'CRITICAL') && this.activeTab !== 'system') {
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
