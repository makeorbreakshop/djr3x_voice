/**
 * Studio (plan Phase 9): a keyframe timeline over the performer.
 *
 * - Lanes: time ruler (+ beat grid, loop region), audio waveform, voice line, then one curve
 *   track per profile channel (animation range drawn, lint violations marked).
 * - Preview is the performer itself: scrub/play send the unsaved clip to the embedded WASM
 *   performer (`preview`), the same compositor + actuation as a performed clip. "Robot"
 *   sends it to the runtime's performer instead (typed `perf.preview`, Bench-safe there).
 * - Save goes through the gateway (`perf.save_show`): the runtime validates, lints, writes
 *   `show/clips/<id>.json` and its folder reload picks it up. No browser file writes.
 * - Record: the performer's targets at 50 Hz while you move sliders -> RDP keys (reduce.ts).
 *
 * Keyboard (Studio open, focus not in a field): Space play/pause, Home start, K key,
 * Del delete, 1/2/3 ease, A additive/override, arrows nudge/step, Alt+arrows value,
 * [ ] prev/next key, I/O loop in/out, L loop, S snap, = - F zoom, R record, Cmd+Z/Shift+Z,
 * Cmd+S save, Cmd+A all keys on the row, Esc stop.
 */
import './studio.css';
import { lintShow } from '../wasm/r3x_performer';
import { torqueMarks } from '../mechrig/lint';
import { ACTIVE_RIG, RIG_LABEL, type Rig } from '../rigchoice';
import { PROFILE_JSON, type Performer, type PerfFrames } from '../performer';
import { SHOW_FILES } from '../show/loader';
import type { Ack } from '../generated/Ack';
import type { Command } from '../generated/Command';
import {
  blankClip, type ClipDoc, type Ease, evalKeys, fromDoc, type Key, lintMarks, type LintMark, normalizeTrack,
  type StudioClip, toDoc, type Track,
} from './model';
import { reduce, type Sample } from './reduce';
import { AudioPlayer, beatsIn, charTimings, fetchLibrary, type LibraryTrack, loadAudio, type LoadedAudio, PEAK_HZ } from './audio';

export interface StudioHost {
  performer(): Performer | null;
  connected(): boolean;
  send(c: Command): Promise<Ack>;
  /** Studio opened/closed, and whether the 3D view should follow the embedded performer. */
  studioView(open: boolean, local: boolean, inset: number): void;
}

interface JointInfo { name: string; unit: string; lo: number; hi: number; softLo: number; softHi: number; vMax: number; extended: boolean }

const PROFILE = JSON.parse(PROFILE_JSON) as {
  joints: { name: string; unit: string; soft: { min: number; max: number }; animation: { min: number; max: number }; v_max: number; extended: boolean }[];
  actuators: { joints: Record<string, number> }[];
};
const BODY_ORDER = ['head_pan', 'head_tilt', 'head_roll', 'head_lift', 'visor', 'hero_shoulder', 'hero_wrist', 'torso_top', 'torso_lower'];
/** Channels a clip can drive: each actuator's primary joint, body order first. */
const JOINTS: JointInfo[] = PROFILE.actuators
  .map((a) => Object.keys(a.joints)[0])
  .map((n) => PROFILE.joints.find((j) => j.name === n)!)
  .map((j) => ({ name: j.name, unit: j.unit, lo: j.animation.min, hi: j.animation.max, softLo: j.soft.min, softHi: j.soft.max, vMax: j.v_max, extended: j.extended }))
  .sort((a, b) => (BODY_ORDER.indexOf(a.name) + 1 || 99) - (BODY_ORDER.indexOf(b.name) + 1 || 99) || Number(a.extended) - Number(b.extended));

const TOP_H = 22, AUDIO_H = 46, VOICE_H = 18, TOPS = TOP_H + AUDIO_H + VOICE_H;
const ROW_H = 26, ROW_H_SEL = 108, HEAD_W = 188;
const C = {
  bg: '#0e1016', row: '#12151c', rowSel: '#161a23', grid: '#1e222c', beat: '#2a2f3b', bar: '#3a4150',
  text: '#8a909c', curve: '#4aa3ff', curveAdd: '#8fd18b', key: '#d9dce3', keySel: '#e8762a', limit: 'rgba(255,90,80,.10)',
  limitLine: 'rgba(255,90,80,.55)', anim: 'rgba(74,163,255,.05)', err: '#ff5a50', play: '#e8762a', loop: 'rgba(232,118,42,.10)',
  wave: '#56607a', waveHi: '#8b95b0', out: 'rgba(0,0,0,.35)', rec: '#ff4d4d',
};
const SESSION = 'r3x.studio';
const FRAME = 0.02;
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, '0')}`;

export class Studio {
  active = false;
  private clip: StudioClip = blankClip();
  private doc: ClipDoc = toDoc(this.clip);
  private t = 0;
  private playing = false;
  private playFrom = 0;
  private playWall = 0;
  private loop = { on: false, a: 0, b: 2 };
  private pps = 160; // pixels per second
  private x0 = 0; // seconds at the left edge
  private snap = true;
  private showExt = false;
  private robot = false;
  private selRow = 0;
  private selKeys = new Set<number>();
  private undo: string[] = [];
  private redo: string[] = [];
  private marks: LintMark[] = [];
  private audio: LoadedAudio | null = null;
  private player = new AudioPlayer();
  private library: LibraryTrack[] = [];
  private voice = { text: '', at: 0 };
  private rec: { t0: number; wall: number; samples: Map<string, Sample[]>; next: number } | null = null;
  private frame: PerfFrames | null = null;
  private lastRemote = 0;
  private dirty = true;
  private lintTimer = 0;

  private root = el('div', 'studio');
  private heads = el('div', 'st-heads');
  private top = el('canvas', 'st-top');
  private cv = el('canvas', 'st-tracks');
  private scroller = el('div', 'st-body');
  private status = el('div', 'st-status');
  private ui: Record<string, HTMLInputElement | HTMLSelectElement | HTMLButtonElement> = {};
  private rowEls: { row: HTMLDivElement; val: HTMLOutputElement; slider: HTMLInputElement; mode: HTMLButtonElement }[] = [];

  constructor(private host: StudioHost) {
    this.build();
    this.restore();
    addEventListener('keydown', (e) => this.onKey(e), true);
    addEventListener('resize', () => this.layout());
  }

  // ------------------------------------------------------------------ public

  setActive(on: boolean) {
    if (on === this.active) return;
    this.active = on;
    this.root.hidden = !on;
    if (!on) {
      this.stop();
      this.stopRecord(false);
      this.host.performer()?.previewStop();
      if (this.robot && this.host.connected()) void this.host.send({ class: 'perf', type: 'preview_stop' });
    }
    this.host.studioView(on, !this.robot, on ? this.root.offsetHeight : 0);
    if (on) {
      this.layout();
      this.relint();
      this.preview(true);
    }
    this.save();
  }

  /** Re-send the preview (the embedded performer just loaded). */
  refresh() {
    if (this.active) { this.layout(); this.relint(); this.preview(!this.playing); }
  }

  /** Every animation frame: the embedded performer's frame (null while it stands down). */
  tick(f: PerfFrames | null) {
    if (!this.active) return;
    if (f) this.frame = f;
    if (this.playing) {
      const at = this.player.time() ?? this.playFrom + (performance.now() - this.playWall) / 1000;
      const end = this.loop.on ? this.loop.b : Math.max(this.clip.duration, this.audio?.buffer.duration ?? 0);
      if (at >= end) {
        if (this.loop.on && !this.rec) this.play(this.loop.a);
        else this.stop();
      } else this.t = at;
      this.dirty = true;
    }
    if (this.rec && f) this.sample(f);
    if (this.dirty) this.draw();
    this.readouts();
  }

  // ------------------------------------------------------------------ build

  private build() {
    this.root.hidden = true;
    const bar = el('div', 'st-bar');
    const btn = (id: string, label: string, title: string, fn: () => void) => {
      const b = el('button', undefined, label);
      b.title = title;
      b.onclick = () => { fn(); b.blur(); };
      this.ui[id] = b;
      return b;
    };
    const input = (id: string, title: string, attrs: Record<string, string | number | boolean>, on: (i: HTMLInputElement) => void) => {
      const i = el('input');
      Object.assign(i, attrs);
      i.title = title;
      i.onchange = () => on(i);
      i.onkeydown = (e) => { if (e.key === 'Enter' || e.key === 'Escape') i.blur(); };
      this.ui[id] = i;
      return i;
    };
    const select = (id: string, title: string, on: (s: HTMLSelectElement) => void) => {
      const s = el('select');
      s.title = title;
      s.onchange = () => { on(s); s.blur(); };
      this.ui[id] = s;
      return s;
    };
    const group = (...xs: HTMLElement[]) => { const g = el('div', 'st-group'); g.append(...xs); return g; };

    const load = select('load', 'Open a clip from show/clips', (s) => this.open(s.value));
    const tier = select('tier', 'Tier: who may trigger it (SPEC)', (s) => this.edit(() => (this.clip.tier = s.value as StudioClip['tier'])));
    tier.append(...['free', 'cheap', 'show'].map((x) => Object.assign(el('option', undefined, x), { value: x })));
    const lib = select('lib', 'Audio: a library track (beat grid from the r3x-beats cache)', (s) => void this.loadLibrary(s.value));
    const file = Object.assign(el('input'), { type: 'file', accept: 'audio/*', hidden: true });
    file.onchange = () => { const f = file.files?.[0]; if (f) void this.loadFile(f); file.value = ''; };

    bar.append(
      group(
        btn('play', '▶', 'Play / pause (Space)', () => this.toggle()),
        btn('rec', '●', 'Record the puppet sliders into a take (R)', () => (this.rec ? this.stopRecord(true) : this.startRecord())),
        btn('loop', 'Loop', 'Loop region (L; I/O set in/out; Shift-drag the ruler)', () => this.setLoop(!this.loop.on)),
        btn('snap', 'Snap', 'Snap keys to beats / 50 ms (S)', () => { this.snap = !this.snap; this.dirty = true; this.syncBar(); }),
      ),
      el('span', 'st-time'),
      group(btn('new', '+ New', 'Start a new, empty clip (Cmd+Z brings the old one back)', () => this.open('')),
        load, input('id', 'Clip id (file name)', { value: '', size: 12, spellcheck: false }, (i) => this.edit(() => (this.clip.id = i.value.trim()))),
        input('dur', 'Duration (s)', { type: 'number', step: '0.1', min: '0.1' }, (i) => this.edit(() => (this.clip.duration = Math.max(0.1, Number(i.value) || 1)))),
        tier,
        input('desc', 'Description: one line, the catalogue Claude and Jev see', { size: 18, placeholder: 'description' }, (i) => this.edit(() => (this.clip.description = i.value)))),
      group(lib, btn('file', 'File…', 'Load a local audio file as the reference track', () => file.click()),
        input('bpm', 'BPM (grid when the track has no cached beats)', { type: 'number', step: '0.1', min: '30', placeholder: 'bpm', size: 4 }, (i) => {
          if (this.audio) { this.audio.bpm = Number(i.value) || null; this.audio.beats = []; this.dirty = true; }
        }),
        input('voice', 'Voice line: characters spread over the audio, else 13 chars/s from the playhead', { placeholder: 'voice line', size: 14 }, (i) => {
          this.voice = { text: i.value, at: i.value ? this.t : 0 };
          this.dirty = true;
          this.save();
        }), file),
      group(
        btn('ext', 'Ext', 'Show the extended joints (the clip then requires "extended")', () => { this.showExt = !this.showExt; this.rows(); }),
        btn('robot', 'Robot', 'Send to robot: preview on the runtime performer (Studio/Bench, lint-clean clips only)', () => this.setRobot(!this.robot)),
        btn('save', 'Save', 'Save to show/clips via the runtime (Cmd+S)', () => void this.saveClip()),
        btn('cue', '+Cue', 'Also save a cue <id>_cue that plays this clip', () => void this.saveCue()),
      ),
    );
    bar.append(file);

    const topHead = el('div', 'st-head-top');
    topHead.append(el('div', 'st-lane', 'time'), el('div', 'st-lane st-audio', 'audio'), el('div', 'st-lane', 'voice'));
    this.heads.append(topHead);
    const lanes = el('div', 'st-lanes');
    lanes.append(this.top, this.cv);
    this.scroller.append(this.heads, lanes);
    this.root.append(bar, this.scroller, this.status);
    document.body.append(this.root);

    this.top.onpointerdown = (e) => this.onTopDown(e);
    this.cv.onpointerdown = (e) => this.onTrackDown(e);
    this.cv.ondblclick = (e) => this.onTrackDbl(e);
    for (const c of [this.top, this.cv]) c.onwheel = (e) => this.onWheel(e);

    void fetchLibrary().then((l) => {
      this.library = l;
      lib.replaceChildren(Object.assign(el('option', undefined, l.length ? 'audio…' : 'no library'), { value: '' }),
        ...l.map((x) => Object.assign(el('option', undefined, `${x.name.replace(/\.\w+$/, '')}${x.bpm ? ` · ${x.bpm.toFixed(0)}` : ''}`), { value: x.name })));
    });
    this.listClips();
    this.rows();
  }

  private listClips() {
    const ids = Object.keys(SHOW_FILES).filter((p) => p.startsWith('show/clips/')).map((p) => p.slice(11, -5));
    (this.ui.load as HTMLSelectElement).replaceChildren(Object.assign(el('option', undefined, '+ new clip'), { value: '' }),
      ...ids.map((id) => Object.assign(el('option', undefined, id), { value: id })));
  }

  /** Visible channels: body joints, extended ones on demand or when the clip uses them. */
  private joints(): JointInfo[] {
    const used = new Set(this.clip.tracks.map((t) => t.joint));
    return JOINTS.filter((j) => !j.extended || this.showExt || used.has(j.name));
  }

  private rows() {
    const rowsEl = this.heads.querySelector('.st-rows') ?? this.heads.appendChild(el('div', 'st-rows'));
    rowsEl.replaceChildren();
    this.rowEls = this.joints().map((j, i) => {
      const row = el('div', 'st-row');
      row.onpointerdown = (e) => { if (!(e.target instanceof HTMLInputElement)) this.selectRow(i); };
      const name = el('span', 'st-name', j.name);
      name.title = `${j.name} (${j.unit}) animation ${j.lo}..${j.hi}, vMax ${j.vMax}/s${j.extended ? ', extended' : ''}`;
      const mode = el('button', 'st-mode');
      mode.title = 'Additive (offset over lower layers) / override (absolute) (A)';
      mode.onclick = () => { this.selectRow(i); this.toggleMode(); mode.blur(); };
      const val = el('output', 'st-val');
      const slider = Object.assign(el('input'), { type: 'range', min: String(j.lo), max: String(j.hi), step: '0.1', value: '0' });
      slider.title = 'Puppet this channel (jogs the embedded performer; recorded while ● is on)';
      slider.oninput = () => this.host.performer()?.command({ cmd: 'jog', joint: j.name, value: Number(slider.value) });
      const release = () => this.host.performer()?.command({ cmd: 'jog', joint: j.name, value: null });
      slider.onpointerup = release;
      slider.onchange = release;
      row.append(name, mode, val, slider);
      rowsEl.append(row);
      return { row, val, slider, mode };
    });
    this.selRow = Math.min(this.selRow, this.rowEls.length - 1);
    this.layout();
  }

  // ------------------------------------------------------------------ geometry

  private rowTop(i: number) { let y = 0; for (let k = 0; k < i; k++) y += k === this.selRow ? ROW_H_SEL : ROW_H; return y; }
  private rowH(i: number) { return i === this.selRow ? ROW_H_SEL : ROW_H; }
  private rowAt(y: number) { const js = this.joints(); for (let i = 0, top = 0; i < js.length; i++) { const h = this.rowH(i); if (y < top + h) return i; top += h; } return -1; }
  private tx(t: number) { return (t - this.x0) * this.pps; }
  private xt(x: number) { return this.x0 + x / this.pps; }
  private range(j: JointInfo): [number, number] { const pad = (j.hi - j.lo) * 0.12; return [j.lo - pad, j.hi + pad]; }
  private vy(j: JointInfo, v: number, top: number, h: number) { const [a, b] = this.range(j); return top + h - 3 - ((v - a) / (b - a)) * (h - 6); }
  private yv(j: JointInfo, y: number, top: number, h: number) { const [a, b] = this.range(j); return a + ((top + h - 3 - y) / (h - 6)) * (b - a); }
  private track(j: string): Track | undefined { return this.clip.tracks.find((t) => t.joint === j); }
  private width() { return Math.max(100, this.scroller.clientWidth - (this.heads.offsetWidth || HEAD_W)); }

  private layout() {
    if (!this.active) return;
    const w = this.width();
    const dpr = devicePixelRatio || 1;
    const h = this.rowTop(this.rowEls.length);
    for (const [c, ch] of [[this.top, TOPS], [this.cv, h]] as const) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(ch * dpr);
      c.style.width = `${w}px`;
      c.style.height = `${ch}px`;
    }
    this.rowEls.forEach((r, i) => { r.row.style.height = `${this.rowH(i)}px`; r.row.classList.toggle('sel', i === this.selRow); });
    this.dirty = true;
    this.draw();
  }

  // ------------------------------------------------------------------ drawing

  private gridTimes(a: number, b: number): { t: number; strong: boolean }[] {
    if (this.audio && (this.audio.bpm || this.audio.beats.length)) {
      const beats = beatsIn(this.audio, a, b);
      const all = beatsIn(this.audio, 0, b);
      const first = all.length - beats.length;
      return beats.map((t, i) => ({ t, strong: (first + i) % 4 === 0 }));
    }
    const step = this.pps > 120 ? 0.25 : this.pps > 40 ? 1 : 5;
    const out = [];
    for (let t = Math.ceil(a / step) * step; t <= b; t += step) out.push({ t, strong: Math.abs(t - Math.round(t)) < 1e-6 });
    return out;
  }

  private draw() {
    if (!this.active) return;
    this.dirty = false;
    const dpr = devicePixelRatio || 1;
    const w = this.width();
    const t1 = this.xt(w);
    const grid = this.gridTimes(Math.max(0, this.x0), t1);
    const dur = this.clip.duration;

    // ---- top lanes
    let g = this.top.getContext('2d')!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = C.bg;
    g.fillRect(0, 0, w, TOPS);
    if (this.loop.on || this.loop.b > this.loop.a) {
      g.fillStyle = this.loop.on ? 'rgba(232,118,42,.35)' : 'rgba(232,118,42,.12)';
      g.fillRect(this.tx(this.loop.a), 0, (this.loop.b - this.loop.a) * this.pps, 5);
    }
    g.font = '10px ui-monospace, Menlo, monospace';
    g.fillStyle = C.text;
    const lab = this.pps > 120 ? 0.5 : this.pps > 40 ? 1 : 5;
    for (let t = Math.ceil(Math.max(0, this.x0) / lab) * lab; t <= t1; t += lab) {
      const x = this.tx(t);
      g.fillRect(x, TOP_H - 6, 1, 6);
      g.fillText(t.toFixed(lab < 1 ? 1 : 0), x + 3, TOP_H - 8);
    }
    for (const b of grid) { g.fillStyle = b.strong ? C.bar : C.beat; g.fillRect(this.tx(b.t), TOP_H, 1, AUDIO_H); }
    if (this.audio) {
      const p = this.audio.peaks;
      const mid = TOP_H + AUDIO_H / 2;
      g.fillStyle = C.wave;
      for (let x = 0; x < w; x++) {
        const a = Math.floor(this.xt(x) * PEAK_HZ), b = Math.max(a + 1, Math.floor(this.xt(x + 1) * PEAK_HZ));
        if (a < 0 || a * 2 >= p.length) continue;
        let lo = 0, hi = 0;
        for (let k = a; k < b && k * 2 < p.length; k++) { lo = Math.min(lo, p[k * 2]); hi = Math.max(hi, p[k * 2 + 1]); }
        g.fillRect(x, mid - hi * (AUDIO_H / 2 - 2), 1, Math.max(1, (hi - lo) * (AUDIO_H / 2 - 2)));
      }
      g.fillStyle = C.waveHi;
      g.fillText(`${this.audio.name}${this.audio.bpm ? `  ${this.audio.bpm.toFixed(1)} bpm` : ''}`, 4, TOP_H + 11);
    } else {
      g.fillStyle = C.grid;
      g.fillText('no audio: pick a library track or a file', 4, TOP_H + 26);
    }
    if (this.voice.text) {
      const times = charTimings(this.voice.text, this.audio ? { dur: this.audio.buffer.duration } : { at: this.voice.at });
      g.fillStyle = '#c9a4ff';
      g.font = '11px ui-monospace, Menlo, monospace';
      let lastX = -99;
      times.forEach((t, i) => { const x = this.tx(t); if (x - lastX >= 7 && x >= 0 && x < w) { g.fillText(this.voice.text[i], x, TOP_H + AUDIO_H + 13); lastX = x; } });
    }
    g.fillStyle = C.out;
    g.fillRect(this.tx(dur), 0, w, TOPS);
    this.playhead(g, TOPS, true);

    // ---- tracks
    const h = this.rowTop(this.rowEls.length);
    g = this.cv.getContext('2d')!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = C.bg;
    g.fillRect(0, 0, w, h);
    this.joints().forEach((j, i) => {
      const top = this.rowTop(i), rh = this.rowH(i), sel = i === this.selRow;
      g.fillStyle = sel ? C.rowSel : C.row;
      g.fillRect(0, top, w, rh - 1);
      for (const b of grid) { g.fillStyle = b.strong ? C.beat : C.grid; g.fillRect(this.tx(b.t), top, 1, rh - 1); }
      // Limits: the animation range is the band; beyond the soft range is red.
      const yHi = this.vy(j, j.hi, top, rh), yLo = this.vy(j, j.lo, top, rh);
      g.fillStyle = C.anim;
      g.fillRect(0, yHi, w, yLo - yHi);
      g.fillStyle = C.limit;
      g.fillRect(0, top, w, this.vy(j, j.softHi, top, rh) - top);
      const ySoftLo = this.vy(j, j.softLo, top, rh);
      g.fillRect(0, ySoftLo, w, top + rh - 1 - ySoftLo);
      if (sel) {
        g.fillStyle = C.limitLine;
        g.fillRect(0, Math.round(yHi), w, 1);
        g.fillRect(0, Math.round(yLo), w, 1);
        g.fillStyle = C.text;
        g.font = '10px ui-monospace, Menlo, monospace';
        g.fillText(String(j.hi), 3, yHi + 11);
        g.fillText(String(j.lo), 3, yLo - 3);
      }
      const tr = this.track(j.name);
      if (tr?.mode === 'additive' || !tr) { g.fillStyle = C.grid; g.fillRect(0, Math.round(this.vy(j, 0, top, rh)), w, 1); }
      if (tr?.keys.length) {
        g.save();
        g.beginPath();
        g.rect(0, top, w, rh - 1);
        g.clip();
        g.strokeStyle = tr.mode === 'additive' ? C.curveAdd : C.curve;
        g.lineWidth = sel ? 1.5 : 1;
        g.beginPath();
        for (let x = Math.max(0, this.tx(0)); x <= Math.min(w, this.tx(dur) + 1); x += 2) {
          const y = this.vy(j, evalKeys(tr.keys, this.xt(x)), top, rh);
          if (x <= Math.max(0, this.tx(0))) g.moveTo(x, y); else g.lineTo(x, y);
        }
        g.stroke();
        tr.keys.forEach((k, ki) => {
          const x = this.tx(k.t), y = this.vy(j, k.v, top, rh);
          const on = sel && this.selKeys.has(ki);
          const r = sel ? 4.5 : 3;
          g.fillStyle = on ? C.keySel : C.key;
          g.beginPath();
          if (k.ease === 'step') g.rect(x - r + 1, y - r + 1, 2 * r - 2, 2 * r - 2);
          else if (k.ease === 'linear') { g.moveTo(x, y - r); g.lineTo(x + r, y + r - 1); g.lineTo(x - r, y + r - 1); }
          else { g.moveTo(x, y - r); g.lineTo(x + r, y); g.lineTo(x, y + r); g.lineTo(x - r, y); }
          g.fill();
        });
        g.restore();
      }
      // Lint marks: a red tick at the offending time (or the whole row edge).
      for (const m of this.marks.filter((m) => m.joint === j.name)) {
        g.fillStyle = C.err;
        if (m.t !== undefined) g.fillRect(this.tx(m.t) - 1, top, 3, 5);
        g.fillRect(0, top, 3, rh - 1);
      }
    });
    g.fillStyle = C.out;
    g.fillRect(this.tx(dur), 0, w, h);
    if (this.loop.on) { g.fillStyle = C.loop; g.fillRect(this.tx(this.loop.a), 0, (this.loop.b - this.loop.a) * this.pps, h); }
    this.playhead(g, h, false);
    (this.root.querySelector('.st-time') as HTMLElement).textContent = `${fmt(this.t)} / ${fmt(dur)}`;
  }

  private playhead(g: CanvasRenderingContext2D, h: number, handle: boolean) {
    const x = Math.round(this.tx(this.t));
    g.fillStyle = this.rec ? C.rec : C.play;
    g.fillRect(x, 0, 1, h);
    if (handle) { g.beginPath(); g.moveTo(x - 5, 0); g.lineTo(x + 6, 0); g.lineTo(x + 0.5, 7); g.fill(); }
  }

  private readouts() {
    const f = this.frame;
    this.joints().forEach((j, i) => {
      const r = this.rowEls[i];
      if (!r) return;
      const tr = this.track(j.name);
      r.mode.textContent = tr ? (tr.mode === 'additive' ? 'ADD' : 'OVR') : '·';
      r.mode.classList.toggle('add', tr?.mode === 'additive');
      const v = f?.joints[j.name];
      r.val.textContent = v === undefined ? '' : v.toFixed(1);
      if (document.activeElement !== r.slider && v !== undefined) r.slider.value = String(v);
    });
  }

  private syncBar() {
    const u = this.ui;
    u.play.textContent = this.playing ? '❚❚' : '▶';
    u.rec.classList.toggle('rec', !!this.rec);
    u.loop.classList.toggle('on', this.loop.on);
    u.snap.classList.toggle('on', this.snap);
    u.ext.classList.toggle('on', this.showExt);
    u.robot.classList.toggle('on', this.robot);
    (u.robot as HTMLButtonElement).disabled = !this.host.connected();
    (u.id as HTMLInputElement).value = this.clip.id;
    (u.dur as HTMLInputElement).value = String(+this.clip.duration.toFixed(3));
    (u.tier as HTMLSelectElement).value = this.clip.tier;
    (u.desc as HTMLInputElement).value = this.clip.description;
    (u.bpm as HTMLInputElement).value = this.audio?.bpm ? this.audio.bpm.toFixed(1) : '';
  }

  private say(msg: string, err = false) {
    this.status.textContent = msg;
    this.status.classList.toggle('err', err);
  }

  // ------------------------------------------------------------------ edits

  private snapshot() { return JSON.stringify(this.clip); }

  /** Apply an edit with undo, then re-preview, re-lint and persist. */
  private edit(fn: () => void) {
    this.undo.push(this.snapshot());
    if (this.undo.length > 200) this.undo.shift();
    this.redo = [];
    fn();
    this.changed();
  }

  private changed() {
    for (const tr of this.clip.tracks) normalizeTrack(tr, this.clip.duration);
    this.clip.tracks = this.clip.tracks.filter((t) => t.keys.length);
    this.clip.requires = this.clip.tracks.some((t) => JOINTS.find((j) => j.name === t.joint)?.extended) ? 'extended' : undefined;
    this.doc = toDoc(this.clip);
    this.syncBar();
    this.dirty = true;
    this.preview(!this.playing);
    clearTimeout(this.lintTimer);
    this.lintTimer = window.setTimeout(() => this.relint(), 120);
    this.save();
  }

  private restoreFrom(stack: string[], other: string[]) {
    const s = stack.pop();
    if (!s) return;
    other.push(this.snapshot());
    this.clip = JSON.parse(s) as StudioClip;
    this.selKeys.clear();
    this.rows();
    this.changed();
  }

  private open(id: string) {
    const text = id ? SHOW_FILES[`show/clips/${id}.json`] : null;
    this.edit(() => {
      this.clip = text ? fromDoc(JSON.parse(text) as ClipDoc) : blankClip(this.freeId());
      this.selKeys.clear();
      this.t = 0;
      if (this.clip.requires) this.showExt = true;
    });
    this.rows();
    this.fit();
    const on = this.clip.extra?.rig as Rig | undefined;
    const rigNote = ` Rig: authored on ${on ? RIG_LABEL[on] : 'Original (no rig recorded)'}; linting against ${RIG_LABEL[ACTIVE_RIG]}.`;
    this.say(text ? `Opened show/clips/${id}.json.${rigNote}` : 'New clip: double-click a track to key it, or ● to record the sliders');
  }

  private freeId() {
    for (let n = 1; ; n++) if (!SHOW_FILES[`show/clips/studio_${n}.json`]) return `studio_${n}`;
  }

  private relint() {
    try {
      const files = { ...SHOW_FILES, [`show/clips/${this.doc.id}.json`]: JSON.stringify(this.doc) };
      const r = JSON.parse(lintShow(PROFILE_JSON, JSON.stringify(files))) as { errors: string[] };
      this.marks = lintMarks(r.errors, this.doc.id);
      this.marks.push(...torqueMarks(this.doc)); // servo load vs the 70 % rule (mechrig/lint.ts)
    } catch (e) {
      this.marks = [{ message: String(e) }];
    }
    // An empty new clip is not an error yet: keep the "New clip: ..." hint.
    if (!this.clip.tracks.length) this.marks = [];
    if (this.marks.length) this.say(`${this.marks.length} lint error${this.marks.length > 1 ? 's' : ''}: ${this.marks[0].message}`, true);
    else if (this.status.classList.contains('err')) this.say('Lint clean.');
    this.ui.save.classList.toggle('bad', this.marks.length > 0 || !this.clip.tracks.length);
    this.dirty = true;
  }

  private selectRow(i: number) {
    if (i === this.selRow || i < 0) return;
    this.selRow = i;
    this.selKeys.clear();
    this.layout();
  }

  private selTrack(create = false): Track | undefined {
    const j = this.joints()[this.selRow];
    if (!j) return;
    let tr = this.track(j.name);
    if (!tr && create) {
      tr = { joint: j.name, mode: 'override', keys: [] };
      this.clip.tracks.push(tr);
    }
    return tr;
  }

  private addKey(t: number, v?: number) {
    this.edit(() => {
      const tr = this.selTrack(true)!;
      const j = this.joints()[this.selRow];
      const value = v ?? (tr.keys.length ? evalKeys(tr.keys, t) : tr.mode === 'additive' ? 0 : (this.frame?.targets[j.name] ?? 0));
      const ease: Ease = tr.keys[tr.keys.length - 1]?.ease ?? 'minjerk';
      tr.keys = tr.keys.filter((k) => Math.abs(k.t - t) > 1e-4);
      tr.keys.push({ t, v: +value.toFixed(2), ease });
      normalizeTrack(tr, this.clip.duration);
      this.selKeys = new Set([tr.keys.findIndex((k) => Math.abs(k.t - t) < 1e-3)]);
    });
  }

  private toggleMode() {
    const tr = this.selTrack();
    if (tr) this.edit(() => (tr.mode = tr.mode === 'additive' ? 'override' : 'additive'));
  }

  private snapT(t: number, exact = false): number {
    if (!this.snap || exact) return Math.max(0, +t.toFixed(3));
    const tol = 8 / this.pps;
    if (this.audio) {
      const beats = beatsIn(this.audio, t - tol, t + tol);
      // Half beats too, so off-beat accents land.
      const halves = beats.length ? beats : beatsIn(this.audio, t - 1, t + 1).flatMap((b, i, a) => (a[i + 1] ? [(b + a[i + 1]) / 2] : []));
      const near = [...beats, ...halves].sort((a, b) => Math.abs(a - t) - Math.abs(b - t))[0];
      if (near !== undefined && Math.abs(near - t) <= tol) return Math.max(0, +near.toFixed(3));
    }
    return Math.max(0, Math.round(t / 0.05) * 0.05);
  }

  // ------------------------------------------------------------------ transport + preview

  /** Send the working clip to the preview target at the playhead. */
  private preview(hold: boolean) {
    if (!this.active) return;
    const hasTracks = Object.keys(this.doc.tracks).length > 0;
    if (this.robot && this.host.connected()) {
      const now = performance.now();
      if (hold && now - this.lastRemote < 40) return;
      this.lastRemote = now;
      const c: Command = hasTracks ? { class: 'perf', type: 'preview', clip: this.doc as never, at: this.t, hold } : { class: 'perf', type: 'preview_stop' };
      void this.host.send(c).then((a) => { if (a.status === 'rejected') this.say(`Robot: ${a.reason}`, true); });
      return;
    }
    const p = this.host.performer();
    if (!p) return;
    if (!hasTracks) return p.previewStop();
    try {
      p.preview(this.doc, this.t, hold);
    } catch (e) {
      this.say(`Preview: ${e instanceof Error ? e.message : String(e)}`, true);
    }
  }

  private play(from = this.t) {
    this.t = from;
    this.playing = true;
    this.playFrom = from;
    this.playWall = performance.now();
    if (this.audio && from < this.audio.buffer.duration) this.player.play(this.audio.buffer, from);
    else this.player.stop();
    this.preview(false);
    this.syncBar();
  }

  private stop() {
    if (!this.playing) return;
    this.playing = false;
    this.player.stop();
    if (this.rec) this.stopRecord(true);
    this.preview(true);
    this.syncBar();
    this.dirty = true;
  }

  private toggle() {
    if (this.playing) this.stop();
    else this.play(this.loop.on && (this.t < this.loop.a || this.t >= this.loop.b) ? this.loop.a : this.t >= this.clip.duration && !this.audio ? 0 : this.t);
  }

  private seek(t: number) {
    this.t = Math.max(0, t);
    if (this.playing) this.play(this.t);
    else this.preview(true);
    this.dirty = true;
  }

  private setLoop(on: boolean) {
    if (on && this.loop.b <= this.loop.a) this.loop = { on, a: 0, b: this.clip.duration };
    this.loop.on = on;
    this.syncBar();
    this.dirty = true;
    this.save();
  }

  private setRobot(on: boolean) {
    if (on && !this.host.connected()) return this.say('Send to robot needs the runtime (r3x-runtime) connected.', true);
    if (!on && this.host.connected()) void this.host.send({ class: 'perf', type: 'preview_stop' });
    if (on) this.host.performer()?.previewStop();
    this.robot = on;
    this.host.studioView(true, !on, this.root.offsetHeight);
    this.syncBar();
    this.preview(!this.playing);
    this.say(on ? 'Robot: previews run on the runtime performer (Studio/Bench only, lint-clean clips only).' : 'Preview: embedded performer (virtual).');
  }

  private fit() {
    // The clip is the unit of work; a long track scrolls (- zooms out).
    const len = Math.max(this.clip.duration * 1.1, 1);
    this.pps = Math.max(10, Math.min(800, this.width() / len));
    this.x0 = 0;
    this.dirty = true;
  }

  private zoom(f: number, atX = this.tx(this.t)) {
    const t = this.xt(atX);
    this.pps = Math.max(10, Math.min(2000, this.pps * f));
    this.x0 = Math.max(0, t - atX / this.pps);
    this.dirty = true;
  }

  // ------------------------------------------------------------------ audio

  private async loadLibrary(name: string) {
    const meta = this.library.find((x) => x.name === name);
    if (!meta) return;
    this.say(`Loading ${name}…`);
    try {
      const data = await (await fetch(meta.url)).arrayBuffer();
      this.setAudio(await loadAudio(name, data, meta));
      (this.ui.lib as HTMLSelectElement).value = name;
    } catch (e) {
      this.say(`Audio: ${String(e)}`, true);
    }
  }

  private async loadFile(f: File) {
    try {
      this.setAudio(await loadAudio(f.name, await f.arrayBuffer()));
      if (!this.audio?.bpm) this.say('No cached beat grid for this file: type its BPM for a grid.');
    } catch (e) {
      this.say(`Audio: ${String(e)}`, true);
    }
  }

  private setAudio(a: LoadedAudio) {
    this.stop();
    this.audio = a;
    this.fit();
    this.syncBar();
    this.save();
    this.say(`${a.name}: ${a.buffer.duration.toFixed(1)} s${a.bpm ? `, ${a.bpm.toFixed(1)} bpm (${a.beats.length ? 'cached beats' : 'grid'})` : ''}. Snap keys to beats with S.`);
  }

  // ------------------------------------------------------------------ record

  private startRecord() {
    if (this.robot) return this.say('Record uses the embedded performer: turn Robot off first.', true);
    if (!this.host.performer()) return;
    this.host.performer()!.previewStop();
    this.rec = { t0: this.t, wall: performance.now(), samples: new Map(), next: 0 };
    this.play(this.t);
    this.host.performer()!.previewStop(); // record over the live pose, not the old curve
    this.say('Recording: move the sliders. R or Space stops.');
    this.syncBar();
  }

  private sample(f: PerfFrames) {
    const r = this.rec!;
    const t = this.t;
    if (t < r.next) return;
    r.next = t + FRAME;
    for (const j of this.joints()) {
      const s = r.samples.get(j.name) ?? r.samples.set(j.name, []).get(j.name)!;
      s.push({ t, v: f.targets[j.name] ?? 0 });
    }
  }

  private stopRecord(keep: boolean) {
    const r = this.rec;
    if (!r) return;
    this.rec = null;
    if (this.playing) { this.playing = false; this.player.stop(); }
    const moved = [...r.samples].filter(([, s]) => s.length > 2 && Math.max(...s.map((x) => x.v)) - Math.min(...s.map((x) => x.v)) > 0.5);
    if (!keep || !moved.length) {
      this.say(keep ? 'Take: nothing moved.' : 'Take discarded.');
      this.syncBar();
      return this.preview(true);
    }
    const end = r.samples.values().next().value!.at(-1)!.t;
    let nKeys = 0, nSamples = 0;
    this.edit(() => {
      this.clip.duration = Math.max(this.clip.duration, end);
      for (const [joint, s] of moved) {
        const j = JOINTS.find((x) => x.name === joint)!;
        const keys = reduce(s, { eps: Math.max(0.2, (j.hi - j.lo) * 0.006), vMax: j.vMax }).map((k): Key => ({ ...k, t: +(k.t + r.t0).toFixed(3) }));
        nKeys += keys.length;
        nSamples += s.length;
        let tr = this.track(joint);
        if (!tr) this.clip.tracks.push((tr = { joint, mode: 'override', keys: [] }));
        tr.mode = 'override';
        tr.keys = [...tr.keys.filter((k) => k.t < r.t0 - 1e-6 || k.t > end + 1e-6), ...keys];
      }
    });
    this.say(`Take: ${moved.length} channel${moved.length > 1 ? 's' : ''}, ${nSamples} samples → ${nKeys} keys (RDP, vMax-limited).`);
  }

  // ------------------------------------------------------------------ save

  private async saveClip() {
    const doc = { ...this.doc, rig: ACTIVE_RIG }; // which rig it was authored on (rigchoice.ts)
    if (!this.host.connected()) {
      await navigator.clipboard?.writeText(JSON.stringify(doc, null, 2)).catch(() => {});
      return this.say('Saving needs the runtime (r3x-runtime); the clip JSON is on the clipboard.', true);
    }
    const send = (overwrite: boolean) => this.host.send({ class: 'perf', type: 'save_show', doc: doc as never, overwrite });
    let a = await send(false);
    if (a.status === 'rejected' && /exists/.test(a.reason) && confirm(`Overwrite show/clips/${doc.id}.json?`)) a = await send(true);
    if (a.status === 'rejected') this.say(`Save: ${a.reason}`, true);
    else this.say(`Saved show/clips/${doc.id}.json; the runtime reloads it.`);
  }

  private async saveCue() {
    if (!this.host.connected()) return this.say('Saving needs the runtime (r3x-runtime).', true);
    const id = `${this.clip.id}_cue`;
    const doc = { id, kind: 'cue', title: this.clip.title || id, description: this.clip.description, tags: this.clip.tags, tier: this.clip.tier, actions: [{ at: 0, do: 'clip', id: this.clip.id }] };
    const a = await this.host.send({ class: 'perf', type: 'save_show', doc: doc as never, overwrite: true });
    this.say(a.status === 'rejected' ? `Cue: ${a.reason} (save the clip first)` : `Saved show/cues/${id}.json.`, a.status === 'rejected');
  }

  // ------------------------------------------------------------------ session (a show-file save reloads the dev page)

  private save() {
    try {
      sessionStorage.setItem(SESSION, JSON.stringify({ active: this.active, clip: this.clip, t: this.t, loop: this.loop, pps: this.pps, x0: this.x0, voice: this.voice, audio: this.audio?.name, ext: this.showExt }));
    } catch { /* storage is a convenience */ }
  }

  private restore() {
    let s: { active?: boolean; clip?: StudioClip; t?: number; loop?: Studio['loop']; pps?: number; x0?: number; voice?: Studio['voice']; audio?: string; ext?: boolean } | null = null;
    try { s = JSON.parse(sessionStorage.getItem(SESSION) ?? 'null'); } catch { /* none */ }
    if (!s) return;
    if (s.clip) {
      this.clip = s.clip;
      this.doc = toDoc(this.clip);
      this.say(`Restored your last session (${this.clip.id || 'unnamed clip'}). + New starts an empty clip.`);
    }
    Object.assign(this, { t: s.t ?? 0, pps: s.pps ?? this.pps, x0: s.x0 ?? 0, showExt: !!s.ext });
    if (s.loop) this.loop = s.loop;
    if (s.voice) { this.voice = s.voice; (this.ui.voice as HTMLInputElement).value = s.voice.text; }
    this.rows();
    this.syncBar();
    const name = s.audio;
    if (name) void fetchLibrary().then((l) => { this.library = l; if (l.some((x) => x.name === name)) void this.loadLibrary(name); });
  }

  /** Studio was open before a reload (a show-file save reloads the dev page). */
  wantsOpen() {
    try { return !!JSON.parse(sessionStorage.getItem(SESSION) ?? 'null')?.active; } catch { return false; }
  }

  // ------------------------------------------------------------------ input

  private drag: null | { kind: 'scrub' | 'loop' | 'keys' | 'box'; x0: number; y0: number; t0: number; row: number; orig?: Key[]; moved?: boolean } = null;

  private onTopDown(e: PointerEvent) {
    const x = e.offsetX;
    const t = this.xt(x);
    this.top.setPointerCapture(e.pointerId);
    this.drag = { kind: e.shiftKey ? 'loop' : 'scrub', x0: x, y0: 0, t0: t, row: -1 };
    if (!e.shiftKey) this.seek(this.snapT(t, e.altKey));
    const move = (ev: PointerEvent) => {
      const tt = this.xt(ev.offsetX);
      if (this.drag?.kind === 'scrub') this.seek(this.snapT(tt, ev.altKey));
      else if (this.drag?.kind === 'loop') {
        const a = this.snapT(Math.min(this.drag.t0, tt)), b = this.snapT(Math.max(this.drag.t0, tt));
        if (b - a > 0.05) { this.loop = { on: true, a, b }; this.syncBar(); this.dirty = true; }
      }
    };
    const up = () => { this.top.removeEventListener('pointermove', move); this.drag = null; this.save(); };
    this.top.addEventListener('pointermove', move);
    this.top.addEventListener('pointerup', up, { once: true });
  }

  private keyAt(x: number, y: number): number {
    const j = this.joints()[this.selRow];
    const tr = j && this.track(j.name);
    if (!tr) return -1;
    const top = this.rowTop(this.selRow), h = this.rowH(this.selRow);
    let best = -1, bd = 7;
    tr.keys.forEach((k, i) => { const d = Math.hypot(this.tx(k.t) - x, this.vy(j, k.v, top, h) - y); if (d < bd) { bd = d; best = i; } });
    return best;
  }

  private onTrackDown(e: PointerEvent) {
    const row = this.rowAt(e.offsetY);
    if (row < 0) return;
    if (row !== this.selRow) { this.selectRow(row); return; }
    const k = this.keyAt(e.offsetX, e.offsetY);
    const tr = this.selTrack();
    if (k >= 0 && tr) {
      if (e.shiftKey) { if (this.selKeys.has(k)) this.selKeys.delete(k); else this.selKeys.add(k); }
      else if (!this.selKeys.has(k)) this.selKeys = new Set([k]);
      this.drag = { kind: 'keys', x0: e.offsetX, y0: e.offsetY, t0: tr.keys[k].t, row, orig: tr.keys.map((x) => ({ ...x })) };
    } else {
      if (!e.shiftKey) this.selKeys.clear();
      this.drag = { kind: 'box', x0: e.offsetX, y0: e.offsetY, t0: this.xt(e.offsetX), row };
    }
    this.dirty = true;
    this.cv.setPointerCapture(e.pointerId);
    const j = this.joints()[row];
    const top = this.rowTop(row), h = this.rowH(row);
    const move = (ev: PointerEvent) => {
      const d = this.drag;
      if (!d) return;
      if (d.kind === 'keys' && tr && d.orig) {
        if (!d.moved) { this.undo.push(JSON.stringify({ ...this.clip, tracks: this.clip.tracks.map((t) => (t === tr ? { ...t, keys: d.orig } : t)) })); this.redo = []; d.moved = true; }
        const dt = this.snapT(d.t0 + (ev.offsetX - d.x0) / this.pps, ev.altKey) - d.t0;
        const [a, b] = this.range(j);
        const dv = ev.shiftKey && Math.abs(ev.offsetX - d.x0) > Math.abs(ev.offsetY - d.y0) ? 0 : this.yv(j, ev.offsetY, top, h) - this.yv(j, d.y0, top, h);
        const dtt = ev.shiftKey && dv !== 0 ? 0 : dt;
        tr.keys = d.orig.map((o, i) => this.selKeys.has(i)
          ? { ...o, t: o.t === 0 ? 0 : Math.max(0.001, o.t + dtt), v: +Math.max(a, Math.min(b, o.v + dv)).toFixed(2) }
          : { ...o });
        this.doc = toDoc(this.clip);
        this.dirty = true;
        this.preview(true);
      } else if (d.kind === 'box') {
        if (Math.abs(ev.offsetX - d.x0) + Math.abs(ev.offsetY - d.y0) > 3) d.moved = true;
        const ta = Math.min(d.t0, this.xt(ev.offsetX)), tb = Math.max(d.t0, this.xt(ev.offsetX));
        const va = this.yv(j, Math.max(d.y0, ev.offsetY), top, h), vb = this.yv(j, Math.min(d.y0, ev.offsetY), top, h);
        if (tr) this.selKeys = new Set(tr.keys.flatMap((k, i) => (k.t >= ta && k.t <= tb && k.v >= va && k.v <= vb ? [i] : [])));
        this.dirty = true;
      }
    };
    const up = () => {
      this.cv.removeEventListener('pointermove', move);
      const d = this.drag;
      this.drag = null;
      if (d?.kind === 'keys' && d.moved && tr) {
        // Keep the selection on the same keys through the re-sort.
        const sel = [...this.selKeys].map((i) => tr.keys[i]);
        this.changed();
        this.selKeys = new Set(sel.map((k) => tr.keys.indexOf(k)).filter((i) => i >= 0));
      } else if (d?.kind === 'box' && !d.moved) {
        this.seek(this.snapT(d.t0)); // a plain click on the row moves the playhead
      }
    };
    this.cv.addEventListener('pointermove', move);
    this.cv.addEventListener('pointerup', up, { once: true });
  }

  private onTrackDbl(e: MouseEvent) {
    const row = this.rowAt(e.offsetY);
    if (row < 0) return;
    this.selectRow(row);
    const j = this.joints()[row];
    const t = this.snapT(this.xt(e.offsetX), e.altKey);
    if (t > this.clip.duration) this.clip.duration = t;
    this.addKey(t, this.yv(j, e.offsetY, this.rowTop(row), this.rowH(row)));
  }

  private onWheel(e: WheelEvent) {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      this.zoom(Math.exp(-e.deltaY * 0.01), e.offsetX);
    } else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      e.preventDefault();
      this.x0 = Math.max(0, this.x0 + (e.deltaX || e.deltaY) / this.pps);
      this.dirty = true;
    }
  }

  private onKey(e: KeyboardEvent) {
    if (!this.active) return;
    const tgt = e.target instanceof HTMLElement ? e.target : null;
    if (tgt?.closest('input:not([type=range]), textarea, select') || tgt?.isContentEditable) return;
    // Keys aimed at a side panel (a focused mode button, a tab) are that panel's.
    if (tgt?.closest('#panel, #scene-panel')) return;
    const mod = e.metaKey || e.ctrlKey;
    const tr = this.selTrack();
    const k = e.key;
    const handled = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    const nudge = (dt: number, dv: number) => {
      if (!tr || !this.selKeys.size) return false;
      this.edit(() => tr.keys.forEach((x, i) => { if (this.selKeys.has(i)) { x.t = x.t === 0 ? 0 : Math.max(0.001, +(x.t + dt).toFixed(3)); x.v = +(x.v + dv).toFixed(2); } }));
      return true;
    };
    if (mod && k.toLowerCase() === 'z') { handled(); return e.shiftKey ? this.restoreFrom(this.redo, this.undo) : this.restoreFrom(this.undo, this.redo); }
    if (mod && k.toLowerCase() === 's') { handled(); return void this.saveClip(); }
    if (mod && k.toLowerCase() === 'a') { handled(); if (tr) this.selKeys = new Set(tr.keys.map((_, i) => i)); this.dirty = true; return; }
    if (mod) return;
    switch (k) {
      case ' ': handled(); if (!e.repeat) (this.rec ? this.stopRecord(true) : this.toggle()); return;
      case 'Home': case 'Enter': handled(); return this.seek(this.loop.on ? this.loop.a : 0);
      case 'End': handled(); return this.seek(this.clip.duration);
      case 'Escape': handled(); if (this.rec) this.stopRecord(false); else if (this.playing) this.stop(); else { this.selKeys.clear(); this.dirty = true; } return;
      case 'k': case 'K': handled(); return this.addKey(this.snapT(this.t, true));
      case 'Delete': case 'Backspace':
        handled();
        if (tr && this.selKeys.size) this.edit(() => { tr.keys = tr.keys.filter((x, i) => !this.selKeys.has(i) || (x.t === 0 && tr.keys.length > 1 && !tr.keys.every((_, n) => this.selKeys.has(n)))); this.selKeys.clear(); });
        return;
      case '1': case '2': case '3':
        handled();
        if (tr && this.selKeys.size) this.edit(() => tr.keys.forEach((x, i) => { if (this.selKeys.has(i)) x.ease = (['minjerk', 'linear', 'step'] as Ease[])[Number(k) - 1]; }));
        return;
      case 'a': case 'A': handled(); return this.toggleMode();
      case 'ArrowLeft': case 'ArrowRight': {
        handled();
        const dt = (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 0.1 : FRAME);
        if (!nudge(dt, 0)) this.seek(this.t + dt);
        return;
      }
      case 'ArrowUp': case 'ArrowDown': {
        handled();
        const up = k === 'ArrowUp';
        if (e.altKey) nudge(0, (up ? 1 : -1) * (e.shiftKey ? 5 : 0.5));
        else this.selectRow(Math.max(0, Math.min(this.rowEls.length - 1, this.selRow + (up ? -1 : 1))));
        return;
      }
      case '[': case ']': {
        handled();
        if (!tr) return;
        const ks = tr.keys.map((x) => x.t);
        const t = k === '[' ? ks.filter((x) => x < this.t - 1e-4).at(-1) : ks.find((x) => x > this.t + 1e-4);
        if (t !== undefined) { this.selKeys = new Set([ks.indexOf(t)]); this.seek(t); }
        return;
      }
      case 'i': case 'I': handled(); this.loop.a = this.t; if (this.loop.b <= this.loop.a) this.loop.b = this.clip.duration; return this.setLoop(true);
      case 'o': case 'O': handled(); this.loop.b = this.t; if (this.loop.a >= this.loop.b) this.loop.a = 0; return this.setLoop(true);
      case 'l': case 'L': handled(); return this.setLoop(!this.loop.on);
      case 's': case 'S': handled(); this.snap = !this.snap; this.syncBar(); return;
      case '=': case '+': handled(); return this.zoom(1.4);
      case '-': case '_': handled(); return this.zoom(1 / 1.4);
      case 'f': case 'F': handled(); return this.fit();
      case 'r': case 'R': handled(); return this.rec ? this.stopRecord(true) : this.startRecord();
    }
  }
}
