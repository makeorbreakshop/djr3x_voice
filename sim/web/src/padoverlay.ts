// The controller overlay: a DualShock 3 over the 3D view that lights what the operator is
// pressing and says what each control does to R3X right now. With the runtime's operator
// layer (`pad.controls` in frames) it follows it: the base layer, the L1/R1 bank being held
// (labels from profiles/r3x/pad.json), the Select menu, and what the last button did.
// Without it (the sim's own pad) it shows the embedded puppeteer's mapping. `G` or its close
// button toggles it; it opens by itself the first time a pad appears (then remembers).

import type { PadFrame } from './generated/PadFrame';
import type { PadControls } from './generated/PadControls';
import type { PadMapping } from './generated/PadMapping';
import type { PadBank } from './generated/PadBank';
import padJson from '../../../profiles/r3x/pad.json?raw';

const MAPPING = JSON.parse(padJson) as PadMapping;

// Standard gamepad button indices.
const B = { cross: 0, circle: 1, square: 2, triangle: 3, l1: 4, r1: 5, l2: 6, r2: 7, select: 8, start: 9,
  l3: 10, r3: 11, up: 12, down: 13, left: 14, right: 15, ps: 16 } as const;
const FACES = [B.cross, B.circle, B.square, B.triangle];
const FACE_SYM = ['✕', '○', '□', '△'];
const DPAD = [B.up, B.right, B.down, B.left];
const DPAD_SYM = ['↑', '→', '↓', '←'];

const SVG_NS = 'http://www.w3.org/2000/svg';
const STORE = 'r3x.pad-overlay';

/** A legend line: what lights it (buttons held / sticks off centre) and optional meters. */
type Row = { control: string; does: string; buttons?: number[]; axes?: number[]; intents?: string[]; cls?: string };

const STICKS: Row[] = [
  { control: 'Right stick', does: 'Gaze: head pan / tilt', axes: [2, 3], intents: ['gaze_yaw', 'gaze_pitch'] },
  { control: 'Left stick', does: 'Body turn / lean', axes: [0, 1], intents: ['body_yaw', 'lean'] },
];

/** The runtime's base layer (r3x-pad controls.rs). */
const BASE: Row[] = [
  ...STICKS,
  { control: 'D-pad', does: '↑↓ head lift · ←→ visor', buttons: DPAD, intents: ['lift', 'visor'] },
  { control: 'L2 hold', does: 'Talk (push-to-talk)', buttons: [B.l2] },
  { control: 'R2', does: 'Arm raise', buttons: [B.r2], intents: ['arm_raise'] },
  { control: '✕○□△', does: 'Emotes: tap 1-4 · hold 5-8', buttons: FACES },
  { control: 'L1 / R1 hold', does: `${MAPPING.banks.l1.name} / ${MAPPING.banks.r1.name} bank`, buttons: [B.l1, B.r1] },
  { control: 'L3 · R3', does: 'Idle motion on/off · cancel', buttons: [B.l3, B.r3] },
  { control: 'Start · PS hold', does: 'Freeze · arm/disarm', buttons: [B.start, B.ps] },
  { control: 'Select', does: 'Menu', buttons: [B.select] },
];

/** The embedded performer's own mapping (show/puppeteer.rs), when no runtime is driving. */
const LEGACY: Row[] = [
  ...STICKS,
  { control: 'D-pad ↑ ↓', does: 'Head lift', buttons: [B.up, B.down], intents: ['lift'] },
  { control: 'D-pad ← →', does: 'Visor open / close', buttons: [B.left, B.right], intents: ['visor'] },
  { control: 'R2 − L2', does: 'Arm raise / lower', buttons: [B.l2, B.r2], intents: ['arm_raise'] },
  { control: 'L1 / R1', does: 'Energy − / +', buttons: [B.l1, B.r1], intents: ['energy'] },
  { control: '✕○□△', does: 'Emotes 1-4 (Select + for 5-8)', buttons: FACES },
  { control: 'R3 · Start', does: 'Cycle mode · freeze', buttons: [B.r3, B.start] },
];

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function svg(tag: string, attrs: Record<string, string | number>, text?: string) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  if (text != null) e.textContent = text;
  return e;
}

type Live = { row: HTMLElement; test: (p: PadFrame) => boolean; bars: [HTMLElement, string][] };

export class PadOverlay {
  readonly root = el('div', 'padov');
  private shown = false;
  private autoOpened = false;
  private lastPad = false;
  private parts = new Map<number, SVGElement>();
  private sticks: { ring: SVGElement; dot: SVGElement; cx: number; cy: number }[] = [];
  private legend = el('div', 'padov-legend');
  private layoutKey = '';
  private live: Live[] = [];
  private status = el('span', 'padov-status', 'No controller');
  private chips = el('span', 'padov-chips');
  private toast = el('div', 'padov-toast');

  constructor(private slots: string[]) {
    try {
      const v = localStorage.getItem(STORE);
      if (v != null) {
        this.autoOpened = true; // the operator already chose; don't second-guess them
        this.shown = v === '1';
      }
    } catch {
      /* storage blocked: default behaviour */
    }
    this.build();
    this.render(null);
    this.apply();
    addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.key === 'g' || e.key === 'G') {
        this.toggle();
        e.preventDefault();
      }
    });
  }

  toggle(on = !this.shown) {
    this.shown = on;
    try {
      localStorage.setItem(STORE, on ? '1' : '0');
    } catch {
      /* not remembered */
    }
    this.apply();
  }

  private apply() {
    this.root.hidden = !this.shown;
  }

  /** One frame's pad (or null when none is attached). */
  update(p: PadFrame | null | undefined) {
    const has = !!p;
    if (has && !this.autoOpened) {
      this.autoOpened = true;
      this.shown = true;
      this.apply();
    }
    if (!this.shown && has === this.lastPad) return;
    this.lastPad = has;
    this.render(p ?? null);
  }

  private build() {
    const head = el('div', 'padov-head');
    const title = el('strong', undefined, 'Controller');
    const close = el('button', 'padov-close', '×');
    close.title = 'Hide (G)';
    close.setAttribute('aria-label', 'Hide the controller overlay');
    close.onclick = () => this.toggle(false);
    head.append(title, this.status, this.chips, close);

    const body = el('div', 'padov-body');
    const left = el('div', 'padov-left');
    left.append(this.drawPad(), this.toast);
    body.append(left, this.legend);
    this.root.append(head, body);
    this.root.setAttribute('role', 'region');
    this.root.setAttribute('aria-label', 'Controller overlay');
  }

  private drawPad(): SVGSVGElement {
    const s = svg('svg', { viewBox: '0 0 340 190', class: 'padov-svg', 'aria-hidden': 'true' }) as SVGSVGElement;
    // Shoulders: L2/R2 behind, L1/R1 in front.
    const shoulder = (i: number, x: number, y: number, label: string) => {
      const g = svg('g', {});
      g.append(svg('rect', { x, y, width: 64, height: 14, rx: 5, class: 'padov-btn' }));
      const fill = svg('rect', { x, y, width: 0, height: 14, rx: 5, class: 'padov-fill' });
      g.append(fill, svg('text', { x: x + 32, y: y + 10.5, class: 'padov-lbl' }, label));
      this.parts.set(i, fill);
      s.append(g);
    };
    shoulder(B.l2, 38, 4, 'L2');
    shoulder(B.l1, 38, 22, 'L1');
    shoulder(B.r2, 238, 4, 'R2');
    shoulder(B.r1, 238, 22, 'R1');
    // Body with grips.
    s.append(svg('path', {
      class: 'padov-body-shape',
      d: 'M40 44 Q20 44 16 70 L6 150 Q2 182 30 184 Q52 186 70 160 L96 128 L244 128 L270 160 Q288 186 310 184 Q338 182 334 150 L324 70 Q320 44 300 44 Z',
    }));
    // D-pad.
    const dpad = (i: number, x: number, y: number, w: number, h: number) => {
      const r = svg('rect', { x, y, width: w, height: h, rx: 3, class: 'padov-btn' });
      s.append(r);
      this.parts.set(i, r);
    };
    dpad(B.up, 62, 60, 16, 20);
    dpad(B.down, 62, 100, 16, 20);
    dpad(B.left, 40, 82, 20, 16);
    dpad(B.right, 80, 82, 20, 16);
    // Face buttons.
    const face = (i: number, cx: number, cy: number, sym: string, cls: string) => {
      const c = svg('circle', { cx, cy, r: 11, class: `padov-btn padov-face ${cls}` });
      s.append(c, svg('text', { x: cx, y: cy + 4, class: `padov-sym ${cls}` }, sym));
      this.parts.set(i, c);
    };
    face(B.triangle, 278, 64, '△', 'tri');
    face(B.circle, 302, 90, '○', 'cir');
    face(B.cross, 278, 116, '✕', 'crs');
    face(B.square, 254, 90, '□', 'sqr');
    // Select, Start, PS.
    const small = (i: number, x: number, y: number, w: number, label: string) => {
      const r = svg('rect', { x, y, width: w, height: 9, rx: 4.5, class: 'padov-btn' });
      s.append(r, svg('text', { x: x + w / 2, y: y + 20, class: 'padov-lbl sm' }, label));
      this.parts.set(i, r);
    };
    small(B.select, 128, 84, 22, 'SELECT');
    small(B.start, 190, 84, 22, 'START');
    const ps = svg('circle', { cx: 170, cy: 110, r: 9, class: 'padov-btn' });
    s.append(ps, svg('text', { x: 170, y: 113.5, class: 'padov-lbl sm' }, 'PS'));
    this.parts.set(B.ps, ps);
    // Sticks: the ring lights on click (L3/R3), the dot is the position.
    for (const [cx, cy, click] of [[118, 146, B.l3], [222, 146, B.r3]] as const) {
      const ring = svg('circle', { cx, cy, r: 22, class: 'padov-btn padov-stick' });
      const dot = svg('circle', { cx, cy, r: 8, class: 'padov-dot' });
      s.append(ring, svg('circle', { cx, cy, r: 1.5, class: 'padov-centre' }), dot);
      this.parts.set(click, ring);
      this.sticks.push({ ring, dot, cx, cy });
    }
    return s;
  }

  // ---------------------------------------------------------------- legend layouts
  private rowsLayout(rows: Row[]) {
    for (const r of rows) {
      const row = el('div', `padov-row ${r.cls ?? ''}`);
      const meters = el('span', 'padov-meters');
      const bars: [HTMLElement, string][] = [];
      for (const k of r.intents ?? []) {
        const m = el('span', 'padov-meter');
        const b = el('i');
        m.append(b);
        meters.append(m);
        bars.push([b, k]);
      }
      row.append(el('span', 'padov-ctl', r.control), el('span', 'padov-does', r.does), meters);
      this.legend.append(row);
      this.live.push({
        row,
        bars,
        test: (p) => (r.buttons ?? []).some((i) => (p.buttons[i] ?? 0) > 0) || (r.axes ?? []).some((i) => Math.abs(p.axes[i] ?? 0) > 0.12),
      });
    }
  }

  private bankLayout(bank: PadBank) {
    this.legend.append(el('div', 'padov-title', `${bank.name} bank: tap · hold`));
    FACES.forEach((i, k) => {
      const row = el('div', 'padov-row padov-bank');
      row.append(el('span', 'padov-ctl', FACE_SYM[k]), el('span', 'padov-does', bank.face_tap[k].label), el('span', 'padov-does padov-hold', bank.face_hold[k].label));
      this.legend.append(row);
      this.live.push({ row, bars: [], test: (p) => (p.buttons[i] ?? 0) > 0 });
    });
    DPAD.forEach((i, k) => {
      const row = el('div', 'padov-row padov-bank');
      row.append(el('span', 'padov-ctl', `D-pad ${DPAD_SYM[k]}`), el('span', 'padov-does', bank.dpad[k].label), el('span'));
      this.legend.append(row);
      this.live.push({ row, bars: [], test: (p) => (p.buttons[i] ?? 0) > 0 });
    });
  }

  private menuLayout(c: PadControls) {
    const m = c.menu!;
    this.legend.append(el('div', 'padov-title', m.path.join(' › ')));
    const list = el('div', 'padov-menu');
    m.items.forEach((label, i) => list.append(el('div', `padov-item ${i === m.cursor ? 'on' : ''}`, label)));
    this.legend.append(list, el('div', 'padov-hint', '↑↓ move · ✕ or → pick · ○ or ← back · SELECT close'));
  }

  private relayout(p: PadFrame | null) {
    const c = p?.controls;
    const bank = c?.bank === 'l1' ? MAPPING.banks.l1 : c?.bank === 'r1' ? MAPPING.banks.r1 : null;
    const key = !c ? 'legacy' : c.menu ? `menu:${c.menu.path.join('/')}:${c.menu.items.join('|')}:${c.menu.cursor}` : bank ? `bank:${c.bank}` : 'base';
    if (key === this.layoutKey) return;
    this.layoutKey = key;
    this.legend.replaceChildren();
    this.live = [];
    if (c?.menu) this.menuLayout(c);
    else if (bank) this.bankLayout(bank);
    else this.rowsLayout(c ? BASE : LEGACY);
  }

  private render(p: PadFrame | null) {
    this.root.classList.toggle('nopad', !p);
    this.status.textContent = p ? 'live' : 'No controller: plug in the DualShock 3 and press PS';
    const c = p?.controls;
    const chips: [string, string][] = [];
    if (p) chips.push([p.mode, '']);
    if (c?.talking) chips.push(['talking', 'talk']);
    if (c && !c.armed) chips.push(['disarmed', 'warn']);
    this.chips.replaceChildren(...chips.map(([t, k]) => el('span', `padov-chip ${k}`, t)));
    this.toast.textContent = c?.last ?? '';
    this.toast.classList.toggle('on', !!c?.last);

    const v = (i: number) => p?.buttons[i] ?? 0;
    for (const [i, part] of this.parts) {
      const x = v(i);
      part.classList.toggle('on', x > 0);
      if (i === B.l1 || i === B.l2 || i === B.r1 || i === B.r2) part.setAttribute('width', String(64 * x));
      else part.style.setProperty('--p', String(x));
    }
    this.sticks.forEach((st, k) => {
      const ax = p?.axes[k * 2] ?? 0;
      const ay = p?.axes[k * 2 + 1] ?? 0;
      st.dot.setAttribute('cx', String(st.cx + ax * 14));
      st.dot.setAttribute('cy', String(st.cy + ay * 14));
      st.dot.classList.toggle('on', Math.hypot(ax, ay) > 0.12);
    });

    this.relayout(p);
    for (const l of this.live) {
      l.row.classList.toggle('on', !!p && l.test(p));
      for (const [b, k] of l.bars) {
        const x = Math.max(-1, Math.min(1, p?.intents[k] ?? 0));
        b.style.left = `${50 + Math.min(0, x) * 50}%`;
        b.style.width = `${Math.abs(x) * 50}%`;
      }
    }
    // Emote names on the base layer's face row.
    if (this.layoutKey === 'base' || this.layoutKey === 'legacy') {
      const faceRow = this.live.find((l) => l.row.textContent?.startsWith('✕○□△'));
      const does = faceRow?.row.querySelector('.padov-does');
      if (does) {
        const held = FACES.findIndex((i) => v(i) > 0);
        does.textContent = held >= 0 ? `Emote: ${this.slots[held] ?? '—'} (hold: ${this.slots[held + 4] ?? '—'})` : (this.layoutKey === 'base' ? 'Emotes: tap 1-4 · hold 5-8' : 'Emotes 1-4 (Select + for 5-8)');
      }
    }
  }
}
