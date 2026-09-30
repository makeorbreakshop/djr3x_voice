// The controller overlay: a DualShock 3 over the 3D view that lights what the operator is
// pressing and says what each control does to R3X, with the puppeteer's resulting intents.
// Fed from frames (`pad`, live or from the embedded performer); `G` or its close button
// toggles it, and it opens by itself the first time a pad appears (then remembers).

import type { PadFrame } from './generated/PadFrame';

// Standard gamepad button indices (the puppeteer's mapping, show/puppeteer.rs).
const B = { cross: 0, circle: 1, square: 2, triangle: 3, l1: 4, r1: 5, l2: 6, r2: 7, select: 8, start: 9,
  l3: 10, r3: 11, up: 12, down: 13, left: 14, right: 15, ps: 16 } as const;

const SVG_NS = 'http://www.w3.org/2000/svg';
const STORE = 'r3x.pad-overlay';

type Row = { id: string; control: string; does: string; buttons?: number[]; axes?: number[]; intents?: string[] };

const ROWS: Row[] = [
  { id: 'gaze', control: 'Right stick', does: 'Gaze: head pan / tilt', axes: [2, 3], intents: ['gaze_yaw', 'gaze_pitch'] },
  { id: 'body', control: 'Left stick', does: 'Body turn / lean', axes: [0, 1], intents: ['body_yaw', 'lean'] },
  { id: 'lift', control: 'D-pad ↑ ↓', does: 'Head lift', buttons: [B.up, B.down], intents: ['lift'] },
  { id: 'visor', control: 'D-pad ← →', does: 'Visor open / close', buttons: [B.left, B.right], intents: ['visor'] },
  { id: 'arm', control: 'R2 − L2', does: 'Arm raise / lower', buttons: [B.l2, B.r2], intents: ['arm_raise'] },
  { id: 'energy', control: 'L1 / R1', does: 'Energy − / +', buttons: [B.l1, B.r1], intents: ['energy'] },
  { id: 'mode', control: 'R3 (click)', does: 'Cycle mode', buttons: [B.r3] },
  { id: 'freeze', control: 'Start', does: 'Freeze / unfreeze', buttons: [B.start] },
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

export class PadOverlay {
  readonly root = el('div', 'padov');
  private shown = false;
  private autoOpened = false;
  private lastPad = false;
  /** Pressure-fillable parts, by standard button index. */
  private parts = new Map<number, SVGElement>();
  private sticks: { ring: SVGElement; dot: SVGElement; cx: number; cy: number }[] = [];
  private rows = new Map<string, { row: HTMLElement; bars: HTMLElement[] }>();
  private emotes: HTMLElement[] = [];
  private modeEl = el('span', 'padov-chip');
  private status = el('span', 'padov-status', 'No controller');

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
    head.append(title, this.status, this.modeEl, close);

    const body = el('div', 'padov-body');
    body.append(this.drawPad(), this.buildLegend());
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

  private buildLegend(): HTMLElement {
    const list = el('div', 'padov-legend');
    for (const r of ROWS) {
      const row = el('div', 'padov-row');
      const bars: HTMLElement[] = [];
      const meters = el('span', 'padov-meters');
      for (const _ of r.intents ?? []) {
        const m = el('span', 'padov-meter');
        const b = el('i');
        m.append(b);
        meters.append(m);
        bars.push(b);
      }
      row.append(el('span', 'padov-ctl', r.control), el('span', 'padov-does', r.does), meters);
      list.append(row);
      this.rows.set(r.id, { row, bars });
    }
    const em = el('div', 'padov-row padov-emotes');
    em.append(el('span', 'padov-ctl', '✕ ○ □ △'));
    const names = el('span', 'padov-does');
    for (let i = 0; i < 4; i++) {
      const n = el('span', 'padov-emote');
      this.emotes.push(n);
      names.append(n);
    }
    em.append(names, el('span', 'padov-meters padov-hint', 'SELECT: 5–8'));
    list.append(em);
    return list;
  }

  private render(p: PadFrame | null) {
    this.root.classList.toggle('nopad', !p);
    this.status.textContent = p ? 'live' : 'No controller: plug in the DualShock 3 and press PS';
    this.modeEl.textContent = p ? p.mode : '';
    this.modeEl.hidden = !p;
    const v = (i: number) => p?.buttons[i] ?? 0;

    for (const [i, part] of this.parts) {
      const x = v(i);
      part.classList.toggle('on', x > 0);
      if (i === B.l1 || i === B.l2 || i === B.r1 || i === B.r2) part.setAttribute('width', String(64 * x));
      else (part as SVGElement).style.setProperty('--p', String(x));
    }
    this.sticks.forEach((st, k) => {
      const ax = p?.axes[k * 2] ?? 0;
      const ay = p?.axes[k * 2 + 1] ?? 0;
      st.dot.setAttribute('cx', String(st.cx + ax * 14));
      st.dot.setAttribute('cy', String(st.cy + ay * 14));
      st.dot.classList.toggle('on', Math.hypot(ax, ay) > 0.12);
    });

    for (const r of ROWS) {
      const { row, bars } = this.rows.get(r.id)!;
      const active =
        !!p &&
        ((r.buttons ?? []).some((i) => v(i) > 0) || (r.axes ?? []).some((i) => Math.abs(p.axes[i] ?? 0) > 0.12));
      row.classList.toggle('on', active);
      (r.intents ?? []).forEach((k, j) => {
        const x = Math.max(-1, Math.min(1, p?.intents[k] ?? 0));
        const b = bars[j];
        b.style.left = `${50 + Math.min(0, x) * 50}%`;
        b.style.width = `${Math.abs(x) * 50}%`;
      });
    }

    const bank = v(B.select) > 0 ? 4 : 0;
    const faces = [B.cross, B.circle, B.square, B.triangle];
    this.emotes.forEach((n, k) => {
      n.textContent = this.slots[bank + k] ?? '—';
      n.classList.toggle('on', v(faces[k]) > 0);
    });
  }
}
