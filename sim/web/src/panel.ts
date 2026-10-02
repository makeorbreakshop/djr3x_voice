/**
 * The R3X panel (right): what he does. The Build / Show / Bench / Studio switch picks the tabs -
 * Build (sim only, src/workbench/): Inspect, Checks, BOM, and Steps / Electronics under More -
 * the mechanics, never the robot; Show: Talk (push-to-talk, typed turns, the conversation),
 * Perform (emotes and shows, placed by main.ts; DJ; music), Behaviour (brain, autonomy, alive
 * layers, gaze, engagement); Bench: Rig (Home, outputs by body region, joints, calibrate), Test
 * (the same emotes and shows), Electronics; Studio: Outputs (the Rig tab's, moved). Every mode:
 * the header's Diagnostics button (services, logs + events, console, debug views, the rig
 * profile) takes the panel over until Done. The Scene panel (left, scenepanel.ts) is how the
 * viewer sees him and never changes his state.
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
const NOISY_TOPICS = new Set(['conversation.reply_delta', 'conversation.transcript', 'vision.faces']);
const MAX_LOG_ROWS = 1500;
const topicOf = (e: R3xEvent) => `${e.domain}.${e.type}`;
const ok = (a: Ack) => a.status === 'accepted';
const reason = (a: Ack) => (a.status === 'rejected' ? a.reason : '');
/** tracing's level names for the panel's DEBUG/INFO/WARNING/ERROR select. */
const TRACING_LEVEL: Record<string, string> = { DEBUG: 'debug', INFO: 'info', WARNING: 'warn', ERROR: 'error' };

type Phase = 'offline' | 'idle' | 'engaging' | 'listening' | 'thinking' | 'speaking';
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
/** The runtime's operating modes plus Build, which is this page's own (sim only, no runtime). */
export type PageMode = OperatingMode | 'build';
/** The first tab of each mode, until the operator picks another. */
const DEFAULT_TAB: Record<PageMode, string> = { build: 'inspect', show: 'talk', bench: 'rig', studio: 'outputs' };
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
  /** Build / Show / Bench / Studio: the runtime's, the local pick while offline, or Build. */
  private stageMode: PageMode = 'show';
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
  /** Diagnostics (the old System tab) has the panel: the tabs stand aside until Done. */
  private diagOpen = false;
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
    this.bindDiag();
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
    for (const id of ['ptt', 'rail-ptt', 'say-in', 'say-send']) ($(id) as HTMLButtonElement).disabled = !on;
    const pill = $('st-gw');
    // Offline the page is not dead: it runs its own demo (the embedded performer).
    pill.textContent = on ? 'Live' : 'Demo';
    pill.title = on ? 'Connected to the r3x runtime' : 'Not connected: this page runs its own demo. Start the runtime with ./r3x';
    pill.classList.toggle('on', on);
    const dot = $('rail-gw');
    dot.classList.toggle('on', on);
    dot.title = on ? 'Live' : 'Demo (not connected)';
    dot.setAttribute('aria-label', dot.title);
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

  /** Operating mode (Show/Bench/Studio). Build is local: the runtime's mode waits behind it. */
  private setStage(s: RetainedState) {
    if (this.stageMode !== 'build' && s.stage.mode !== this.stageMode) this.applyMode(s.stage.mode);
    const brainOff = !s.stage.brain;
    for (const id of ['ptt', 'rail-ptt', 'say-in', 'say-send']) ($(id) as HTMLButtonElement).disabled = !this.connected || brainOff;
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
    // Only a live conversation phase earns the viewport: connection lives in the header pill,
    // the mode in the switch.
    badge.hidden = this.stageMode === 'build' || !this.connected || !['engaging', 'listening', 'thinking', 'speaking'].includes(p);
    if (this.stageMode === 'build') {
      badge.dataset.state = 'build';
      badge.textContent = 'Build · sim only';
    } else if (!this.connected) {
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

  /** Offline there is no StageManager: the page's own mode pick (main.ts). Build is always local. */
  setLocalMode(mode: PageMode) {
    if (mode === 'build' || !this.connected) this.applyMode(mode);
  }

  /**
   * Show / Bench / Studio: the switch, the tabs that mode has (each mode remembers its last
   * tab), and where the one set of emote/show controls sits (Perform in Show, Test in Bench).
   */
  private applyMode(mode: PageMode, boot = false) {
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
    // Outputs: Bench's Rig tab, or Studio's own tab (what the timeline sends to the robot).
    const out = $('out-sec');
    if (mode === 'studio') {
      if (out.parentElement !== $('outputs-slot')) $('outputs-slot').appendChild(out);
    } else if (out.parentElement !== $('drive')) $('drive').querySelector('.home-row')!.after(out);
    // Tabs this mode keeps under More (Build: Steps, Electronics).
    document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) =>
      b.classList.toggle('in-more', (b.dataset.more ?? '').split(' ').includes(mode)));
    $('more-menu').hidden = true;
    if (boot) {
      try {
        Object.assign(this.tabFor, JSON.parse(localStorage.getItem(TABS_KEY) ?? '{}'));
      } catch {
        /* storage blocked */
      }
    }
    const want = this.tabFor[mode];
    const ok = (t: string) => !!document.querySelector<HTMLButtonElement>(`[data-tab="${t}"]:not([hidden]):not([data-empty])`);
    if (this.diagOpen) this.activeTab = '';
    if (!this.diagOpen) this.showTab(ok(want) ? want : DEFAULT_TAB[mode]);
    this.renderMore();
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
    // The collapsed panel's rail keeps the same button (layout.ts).
    const rail = $('rail-ptt');
    rail.dataset.state = v.look;
    rail.title = v.hint ? `${v.label} (${v.hint})` : v.label;
    rail.setAttribute('aria-label', v.label);
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
    const tabs = () => Array.from(document.querySelectorAll<HTMLButtonElement>('[data-tab]:not([hidden]):not(.in-more)'));
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

  /** More: the mode's tabs that sit in a menu (Build: Steps, Electronics); empty ones stand aside. */
  private bindMore() {
    const more = $<HTMLButtonElement>('tab-more');
    const menu = $('more-menu');
    const close = () => {
      menu.hidden = true;
      more.setAttribute('aria-expanded', 'false');
    };
    more.onclick = (e) => {
      e.stopPropagation();
      if (!menu.hidden) return close();
      const items = this.moreTabs();
      menu.innerHTML = items.map((b) => `<button role="menuitem" data-pick="${b.dataset.tab}" aria-current="${b.dataset.tab === this.activeTab}">${esc(b.textContent!.trim())}</button>`).join('');
      menu.hidden = false;
      more.setAttribute('aria-expanded', 'true');
      menu.querySelector<HTMLButtonElement>('button')?.focus();
    };
    menu.onclick = (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-pick]');
      if (!b) return;
      close();
      this.pickTab(b.dataset.pick!);
      // The panel's other listeners (Build's Steps / Checks) hear a tab pick as its button's click.
      document.querySelector<HTMLButtonElement>(`[data-tab="${b.dataset.pick}"]`)?.dispatchEvent(new Event('click', { bubbles: true }));
    };
    menu.onkeydown = (e) => {
      if (e.key === 'Escape') {
        close();
        more.focus();
      }
    };
    addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target as Node)) close();
    });
  }

  private moreTabs() {
    return Array.from(document.querySelectorAll<HTMLButtonElement>('[data-tab].in-more:not([hidden]):not([data-empty])'));
  }

  /** More's label says which of its tabs is open ("Steps"), else "More". */
  private renderMore() {
    const more = $('tab-more');
    const items = this.moreTabs();
    more.hidden = !items.length;
    const cur = items.find((b) => b.dataset.tab === this.activeTab);
    more.textContent = cur ? cur.textContent!.trim() : 'More';
    more.setAttribute('aria-selected', String(!!cur));
  }

  // ------------------------------------------------------------------ diagnostics

  private bindDiag() {
    $('diag-open').onclick = () => this.setDiag(!this.diagOpen);
    $('diag-close').onclick = () => this.setDiag(false);
    addEventListener('keydown', (e) => {
      if (e.key !== '`' || e.repeat || e.metaKey || e.ctrlKey || e.altKey || this.typing(e)) return;
      e.preventDefault();
      this.setDiag(!this.diagOpen);
    });
    this.bindMore();
  }

  /** Diagnostics takes the panel (wider, for the logs); Done hands it back to the mode's tab. */
  setDiag(on: boolean) {
    if (on === this.diagOpen) return;
    this.diagOpen = on;
    document.body.classList.toggle('diag-open', on);
    $('diag-open').setAttribute('aria-pressed', String(on));
    this.activeTab = '';
    if (on) {
      this.showTab('system');
      $('diag-close').focus();
    } else {
      const want = this.tabFor[this.stageMode];
      const ok = (t: string) => !!document.querySelector<HTMLButtonElement>(`[data-tab="${t}"]:not([hidden]):not([data-empty])`);
      this.showTab(ok(want) ? want : DEFAULT_TAB[this.stageMode]);
      $('diag-open').focus();
    }
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
    this.renderMore();
    if (name === 'system') {
      this.errCount = 0;
      this.renderErrCount();
      this.scrollLog();
    }
  }

  // ------------------------------------------------------------------ talk

  private bindTalk() {
    for (const ptt of [$<HTMLButtonElement>('ptt'), $<HTMLButtonElement>('rail-ptt')]) {
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
    }

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
        // Build never reaches the runtime; leaving it for the mode the runtime is already in is local too.
        if (d.stageMode === 'build') this.applyMode('build');
        else {
          if (this.stageMode === 'build') this.applyMode(d.stageMode as OperatingMode);
          if (this.gw.state?.stage.mode !== d.stageMode) void this.cmd({ class: 'stage', type: 'set_mode', mode: d.stageMode as OperatingMode });
        }
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
    // On/off settings are switches (one boolean grammar); outputs stay chips (many, grouped).
    const sw = (attr: string, value: string, label: string, on: boolean, title = '') =>
      `<button class="toggle" data-${attr}="${esc(value)}" aria-pressed="${on}"${title ? ` title="${esc(title)}"` : ''}>${esc(label)}</button>`;
    $('drive-toggles').innerHTML = [
      sw('toggle', 'brain', 'Brain', st.brain, 'Voice + LLM: accept spoken and typed turns'),
      sw('toggle', 'autonomy', 'Autonomy', st.autonomy, 'Idle policy and DJ autonomy'),
    ].join('');
    $('drive-layers').innerHTML = Object.keys(st.layers).map((l) => sw('layer', l, l.charAt(0).toUpperCase() + l.slice(1).replace(/_/g, ' '), st.layers[l], 'Procedural motion')).join('')
      || '<span class="hint">None in the profile.</span>';
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
      ? q ? `${hits.length} of ${this.library.length}` : `${this.library.length} tracks`
      : '';
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

  /** System > Vision: the runtime's camera switch (console `vision on|off`), shown from the
   * `vision` service's status: running = on, stopped = switched off, absent = not started. */
  private renderVision(s: RetainedState) {
    const h = s.services.services['vision'];
    const b = $<HTMLButtonElement>('vision-toggle');
    const on = h?.status === 'running';
    const off = h?.status === 'stopped';
    b.disabled = !on && !off;
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on || off ? 'Camera' : 'Camera unavailable'; // a switch: its knob says on or off
    b.title = on
      ? 'Face tracking runs locally (free); a photo goes to Claude only when someone arrives or he is asked to look.'
      : off ? 'Camera closed: no tracking, no photos, no cost.' : 'Camera, face recognition and scene photos';
    $('vision-note').textContent = on
      ? ''
      : off
        ? ''
        : h
          ? `Vision ${h.status}`
          : 'Vision not started';
    b.onclick = async () => {
      b.disabled = true;
      const ack = await this.gw.send({ class: 'intent', type: 'console', line: on ? 'vision off' : 'vision on' });
      if (!ok(ack)) this.toast(reason(ack));
    };
  }

  private renderServices(s: RetainedState) {
    this.renderVision(s);
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
    if (!replay && (r.level === 'ERROR' || r.level === 'CRITICAL') && !this.diagOpen) {
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
