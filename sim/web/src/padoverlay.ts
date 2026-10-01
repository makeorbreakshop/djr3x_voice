// The controller overlay: a DualShock 3 over the 3D view that lights what the operator is
// pressing and says what each control does to R3X right now. With the runtime's operator
// layer (`pad.controls` in frames) it follows it: the layer the held or latched L1/R1/R2 pick
// (labels from profiles/r3x/pad.json), what the left stick drives, pinned arms and grips, the
// Select menu, and what the last button did.
// Without it (the sim's own pad) it shows the embedded puppeteer's mapping. It lives in the
// Scene panel's Controller section (main.ts wires `G` and the first-pad auto-open) and only
// redraws while that panel is open.

import type { PadFrame } from './generated/PadFrame';
import type { PadControls } from './generated/PadControls';
import type { PadMapping } from './generated/PadMapping';
import type { PadLayer } from './generated/PadLayer';
import type { PadLayers } from './generated/PadLayers';
import type { StickTarget } from './generated/StickTarget';
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

/** Short labels drawn on the pad itself, per control. */
type Tags = Record<'l2' | 'l1' | 'r2' | 'r1' | 'dpad' | 'face' | 'faceSub' | 'select' | 'start' | 'ps' | 'lstick' | 'lclick' | 'rstick' | 'rclick', string>;

const STICK: Record<StickTarget, string> = { body: 'Body', hero: 'Hero arm', poker: 'Poker arm', arms: 'Both arms' };
/** `r2l1r1` -> "R2+L1+R1". */
const comboName = (key: string) => (key === 'base' ? 'no modifier' : (key.match(/l1|r1|r2/g) ?? []).map((m) => m.toUpperCase()).join('+'));

/** The runtime's operator layer (r3x-pad controls.rs), for the layer `layer`. */
function runtimeTags(layer: PadLayer, stick: StickTarget): Tags {
  const arm = stick !== 'body';
  return {
    l2: 'L2 Talk', l1: `L1 ${MAPPING.layers.l1.name}`, r2: `R2 ${MAPPING.layers.r2.name}`, r1: `R1 ${MAPPING.layers.r1.name}`,
    dpad: layer.name, face: layer.name, faceSub: 'tap · hold', select: 'tap cancel · hold menu', start: 'FREEZE', ps: 'hold: motors',
    lstick: STICK[stick], lclick: arm ? 'click: pin' : 'click: arms home', rstick: 'Head', rclick: 'hold: look · L3+R3 reset',
  };
}

/** The embedded performer's own mapping (show/puppeteer.rs), when no runtime is driving. */
const LEGACY: Tags = {
  l2: 'L2 Arm −', l1: 'L1 Energy −', r2: 'R2 Arm +', r1: 'R1 Energy +',
  dpad: 'Lift · Visor', face: 'Emotes', faceSub: 'SHIFT: 5-8', select: 'SHIFT', start: 'FREEZE', ps: '',
  lstick: 'Body', lclick: '', rstick: 'Gaze', rclick: 'click: mode',
};

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

type Live = { row: HTMLElement; test: (p: PadFrame) => boolean };

export class PadOverlay {
  readonly root = el('div', 'padov');
  private lastPad = false;
  private parts = new Map<number, SVGElement>();
  private tags = new Map<keyof Tags, SVGElement>();
  private sticks: { ring: SVGElement; dot: SVGElement; cx: number; cy: number }[] = [];
  private legend = el('div', 'padov-legend');
  private layoutKey = '';
  private live: Live[] = [];
  private status = el('span', 'padov-status', 'No controller');
  private chips = el('span', 'padov-chips');
  private toast = el('div', 'padov-toast');

  /** `visible`: whether the section is on screen (drawing is skipped while it is not). */
  constructor(host: HTMLElement, private visible: () => boolean) {
    this.build();
    this.render(null);
    host.append(this.root);
  }

  /** One frame's pad (or null when none is attached). */
  update(p: PadFrame | null | undefined) {
    const has = !!p;
    if (!this.visible() && has === this.lastPad) return;
    this.lastPad = has;
    this.render(p ?? null);
  }

  private build() {
    const head = el('div', 'padov-head');
    head.append(this.status, this.chips);

    const body = el('div', 'padov-body');
    const left = el('div', 'padov-left');
    left.append(this.drawPad(), this.toast);
    body.append(left, this.legend);
    this.root.append(head, body);
    this.root.setAttribute('role', 'region');
    this.root.setAttribute('aria-label', 'Controller');
  }

  private drawPad(): SVGSVGElement {
    const s = svg('svg', { viewBox: '0 0 340 212', class: 'padov-svg', 'aria-hidden': 'true' }) as SVGSVGElement;
    const tag = (k: keyof Tags, x: number, y: number, cls = 'padov-tag') => {
      const t = svg('text', { x, y, class: cls });
      s.append(t);
      this.tags.set(k, t);
    };
    // Shoulders (L2/R2 behind, L1/R1 in front), labelled inside; the fill is the pressure.
    const shoulder = (i: number, x: number, y: number, k: keyof Tags) => {
      s.append(svg('rect', { x, y, width: 96, height: 17, rx: 6, class: 'padov-btn' }));
      const fill = svg('rect', { x, y, width: 0, height: 17, rx: 6, class: 'padov-fill' });
      s.append(fill);
      this.parts.set(i, fill);
      tag(k, x + 48, y + 12.5, 'padov-lbl');
    };
    shoulder(B.l2, 18, 2, 'l2');
    shoulder(B.l1, 18, 22, 'l1');
    shoulder(B.r2, 226, 2, 'r2');
    shoulder(B.r1, 226, 22, 'r1');
    s.append(svg('path', {
      class: 'padov-body-shape',
      d: 'M40 44 Q20 44 16 70 L6 150 Q2 182 30 184 Q52 186 70 160 L96 128 L244 128 L270 160 Q288 186 310 184 Q338 182 334 150 L324 70 Q320 44 300 44 Z',
    }));
    const dpad = (i: number, x: number, y: number, w: number, h: number) => {
      const r = svg('rect', { x, y, width: w, height: h, rx: 3, class: 'padov-btn' });
      s.append(r);
      this.parts.set(i, r);
    };
    dpad(B.up, 62, 58, 16, 20);
    dpad(B.down, 62, 98, 16, 20);
    dpad(B.left, 40, 80, 20, 16);
    dpad(B.right, 80, 80, 20, 16);
    tag('dpad', 70, 136);
    const face = (i: number, cx: number, cy: number, sym: string, cls: string) => {
      const c = svg('circle', { cx, cy, r: 11, class: `padov-btn padov-face ${cls}` });
      s.append(c, svg('text', { x: cx, y: cy + 4, class: `padov-sym ${cls}` }, sym));
      this.parts.set(i, c);
    };
    face(B.triangle, 278, 62, '△', 'tri');
    face(B.circle, 302, 88, '○', 'cir');
    face(B.cross, 278, 114, '✕', 'crs');
    face(B.square, 254, 88, '□', 'sqr');
    tag('face', 278, 142);
    tag('faceSub', 278, 154, 'padov-tag sub');
    const small = (i: number, x: number, y: number, w: number, k: keyof Tags) => {
      const r = svg('rect', { x, y, width: w, height: 9, rx: 4.5, class: 'padov-btn' });
      s.append(r);
      this.parts.set(i, r);
      tag(k, x + w / 2, y + 21, 'padov-lbl sm');
    };
    small(B.select, 126, 76, 24, 'select');
    small(B.start, 190, 76, 24, 'start');
    const ps = svg('circle', { cx: 170, cy: 104, r: 9, class: 'padov-btn' });
    s.append(ps, svg('text', { x: 170, y: 107, class: 'padov-lbl sm' }, 'PS'));
    this.parts.set(B.ps, ps);
    tag('ps', 170, 124, 'padov-lbl sm');
    // Sticks: the ring lights on click (L3/R3), the dot is the position.
    for (const [cx, cy, click] of [[118, 150, B.l3], [222, 150, B.r3]] as const) {
      const ring = svg('circle', { cx, cy, r: 22, class: 'padov-btn padov-stick' });
      const dot = svg('circle', { cx, cy, r: 8, class: 'padov-dot' });
      s.append(ring, svg('circle', { cx, cy, r: 1.5, class: 'padov-centre' }), dot);
      this.parts.set(click, ring);
      this.sticks.push({ ring, dot, cx, cy });
    }
    tag('lstick', 118, 190);
    tag('lclick', 118, 204, 'padov-tag sub');
    tag('rstick', 222, 190);
    tag('rclick', 222, 204, 'padov-tag sub');
    return s;
  }

  // ---------------------------------------------------------------- legend layouts
  private layerLayout(key: string, bank: PadLayer) {
    this.legend.append(el('div', 'padov-title', `${bank.name} (${comboName(key)}): tap · hold`));
    FACES.forEach((i, k) => {
      const row = el('div', 'padov-row padov-bank');
      row.append(el('span', 'padov-ctl', FACE_SYM[k]), el('span', 'padov-does', bank.face_tap[k].label), el('span', 'padov-does padov-hold', bank.face_hold[k].label));
      this.legend.append(row);
      this.live.push({ row, test: (p) => (p.buttons[i] ?? 0) > 0 });
    });
    DPAD.forEach((i, k) => {
      const row = el('div', 'padov-row padov-bank');
      row.append(el('span', 'padov-ctl', `D-pad ${DPAD_SYM[k]}`), el('span', 'padov-does', bank.dpad[k].label), el('span'));
      this.legend.append(row);
      this.live.push({ row, test: (p) => (p.buttons[i] ?? 0) > 0 });
    });
  }

  /** Crane mode: both sticks drive the arm(s) at a speed (r3x-pad controls.rs). */
  private craneLayout(crane: string) {
    const rows: [string, string][] = crane === 'both'
      ? [['Left stick', 'hero arm: aim ↔, raise ↕'], ['Right stick', 'poker arm: around ↔, up/down ↕'], ['R2', 'poker grip (press harder)'], ['✕', 'grip both']]
      : crane === 'hero'
        ? [['Left stick', 'aim ↔, raise ↕'], ['Right stick', 'twist ↔'], ['R2 / ✕', 'grip (press harder)'], ['R1 hold', 'creep (slow) · double-tap: both arms']]
        : [['Left stick', 'around ↔, reach in/out ↕'], ['Right stick', 'claw up/down ↕'], ['R2 / ✕', 'grip (press harder)'], ['L1 hold', 'creep (slow) · double-tap: both arms']];
    rows.push(['D-pad ← →', 'tap: glide to spot · hold: save spot'], ['D-pad ↑', 'glide home'], [crane === 'hero' ? 'L1 tap' : crane === 'poker' ? 'R1 tap' : 'L1/R1 tap', 'leave the crane (the arm stays put)']);
    this.legend.append(el('div', 'padov-title', `Crane: ${crane === 'both' ? 'both arms' : `${crane} arm`} · the head watches the claw`));
    for (const [ctl, does] of rows) {
      const row = el('div', 'padov-row padov-bank');
      row.append(el('span', 'padov-ctl', ctl), el('span', 'padov-does', does), el('span'));
      this.legend.append(row);
    }
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
    const layerKey = (c?.layer || 'base') as keyof PadLayers;
    const bank = MAPPING.layers[layerKey] ?? MAPPING.layers.base;
    const stick = c?.stick ?? 'body';
    const key = !c ? 'legacy' : c.menu ? `menu:${c.menu.path.join('/')}:${c.menu.items.join('|')}:${c.menu.cursor}` : c.crane ? `crane:${c.crane}` : `layer:${layerKey}:${stick}`;
    if (key === this.layoutKey) return;
    this.layoutKey = key;
    this.legend.replaceChildren();
    this.live = [];
    const tags = c ? runtimeTags(bank, stick) : LEGACY;
    for (const [k, t] of this.tags) t.textContent = tags[k];
    if (c?.menu) this.menuLayout(c);
    else if (c?.crane) {
      this.craneLayout(c.crane);
      this.tags.get('rstick')!.textContent = 'Arm (crane)';
    } else if (c) this.layerLayout(layerKey, bank);
  }

  private render(p: PadFrame | null) {
    this.root.classList.toggle('nopad', !p);
    this.status.textContent = p ? 'live' : 'No controller: plug in the DualShock 3 and press PS';
    const c = p?.controls;
    const chips: [string, string][] = [];
    if (p) chips.push([p.mode, '']);
    if (c?.talking) chips.push(['talking', 'talk']);
    if (c && c.layer && c.layer !== 'base') chips.push([MAPPING.layers[c.layer as keyof PadLayers]?.name ?? c.layer, 'layer']);
    for (const m of c?.latched ?? []) chips.push([`${m.toUpperCase()} latched`, 'layer']);
    if (c?.crane) chips.push([`crane: ${c.crane}`, 'talk']);
    if (c?.pickup) chips.push(['bring the stick to the arm', 'warn']);
    for (const a of c?.pinned ?? []) chips.push([`${a} pinned`, '']);
    (c?.grip ?? [0, 0]).forEach((g, i) => { if (g > 0.02) chips.push([`${i ? 'poker' : 'hero'} grip ${Math.round(g * 100)}%`, '']); });
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
    for (const l of this.live) l.row.classList.toggle('on', !!p && l.test(p));

  }
}
