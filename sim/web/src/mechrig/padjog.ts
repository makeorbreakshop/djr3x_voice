/**
 * Build mode's pad layer: the DS3 jogs the mechanism on the workbench (a separate binding
 * layer, active only while Build is open and a pad is present; the puppeteer mapping is
 * untouched everywhere else).
 *
 * | Input | Head group | Body group | One joint selected |
 * |---|---|---|---|
 * | Left stick x / y | pan / tilt | lower ring / hero shoulder | - / jog it |
 * | Right stick x / y | roll / visor | top ring / hero wrist | - |
 * | R2 / L2 | lift up / down | - | - |
 * | D-pad left / right | cycle the selection (Head, Body, then each joint) | | |
 * | D-pad up / down | - | - | jog it |
 * | Triangle | Home (every joint to 0) | | |
 * | Cross (hold) | fine: quarter speed | | |
 *
 * Rate control: full stick = 60 % of the joint's generated v_max; values stay inside the
 * joint's mech range. Input: the browser's Gamepad API (standard mapping), else the pad the
 * runtime reads (frames.pad) - the same two sources the puppeteer has. Sim only, like Build.
 */

import './mechrig.css';
import type { PadFrame } from '../generated/PadFrame';
import type { AsmNode, Workbench } from '../workbench/workbench';
import { GENERATED } from './model';

type Axis = 'lx' | 'ly' | 'rx' | 'ry' | 'trig';
interface Group { name: string; map: Partial<Record<Axis, string>> }

const GROUPS: Group[] = [
  { name: 'Head', map: { lx: 'head_pan', ly: 'head_tilt', rx: 'head_roll', ry: 'visor', trig: 'head_lift' } },
  { name: 'Body', map: { lx: 'torso_lower', ly: 'hero_shoulder', rx: 'torso_top', ry: 'hero_wrist' } },
];
const DEAD = 0.12;
const B = { cross: 0, triangle: 3, l2: 6, r2: 7, up: 12, down: 13, left: 14, right: 15 };

export interface PadInput { axes: number[]; buttons: number[] }

const vmax = new Map(GENERATED.joints.map((j) => [j.name, j.v_max]));
const expo = (x: number) => {
  const a = Math.abs(x) < DEAD ? 0 : (Math.abs(x) - DEAD) / (1 - DEAD);
  return Math.sign(x) * (0.35 * a + 0.65 * a * a * a);
};

export class PadJog {
  readonly el: HTMLDivElement;
  /** Selection index: groups first, then single joints. */
  sel = 0;
  private remote: PadInput | null = null;
  private prev: number[] = [];
  private last = -1;
  private joints: { node: AsmNode; id: string; profile: string; min: number; max: number }[] = [];
  private loadedTop: AsmNode | null = null;
  /** True while this layer consumes the pad (Build open, a pad present): the puppeteer must not. */
  owns = false;

  constructor(private readonly wb: Workbench, parent?: HTMLElement) {
    const dom = typeof document !== 'undefined';
    this.el = dom ? document.createElement('div') : ({ hidden: true, innerHTML: '' } as unknown as HTMLDivElement);
    this.el.className = 'pad-jog';
    this.el.hidden = true;
    if (dom) (parent ?? document.body).appendChild(this.el);
  }

  /** The runtime's pad (frames.pad), when the browser has none of its own. */
  feed(p: PadFrame | null | undefined) {
    this.remote = p ? { axes: p.axes, buttons: p.buttons } : null;
  }

  private read(): { pad: PadInput; source: string } | null {
    const gp = [...((typeof navigator !== 'undefined' && navigator.getGamepads?.()) || [])].find((g) => g && g.mapping === 'standard');
    if (gp) return { pad: { axes: [...gp.axes], buttons: gp.buttons.map((b) => (b.pressed ? Math.max(b.value, 1e-3) : 0)) }, source: gp.id.slice(0, 40) };
    if (this.remote) return { pad: this.remote, source: 'runtime pad' };
    return null;
  }

  private index() {
    if (this.loadedTop === this.wb.top) return;
    this.loadedTop = this.wb.top;
    this.joints = [];
    // A child that is a non-default option (the other head, the other frame) is not fitted.
    const off = (n: AsmNode | null): boolean => {
      for (let c = n; c; c = c.parent) {
        const v = (c.asm.mount as { variant?: { group: string; id: string; default?: boolean } } | undefined)?.variant;
        if (v && (this.wb.variants[v.group] ? this.wb.variants[v.group] !== v.id : !v.default)) return true;
      }
      return false;
    };
    this.wb.forEachNode((n) => {
      if (off(n)) return;
      for (const j of n.asm.joints) {
        if (j.type === 'fixed' || !(j.limits.max > j.limits.min)) continue;
        this.joints.push({ node: n, id: j.id, profile: j.profile_joint ?? j.id, min: j.limits.min, max: j.limits.max });
      }
    });
    this.sel = 0;
  }

  private get options(): string[] {
    const have = new Set(this.joints.map((j) => j.profile));
    const groups = GROUPS.filter((g) => Object.values(g.map).some((p) => have.has(p!))).map((g) => g.name);
    return [...groups, ...this.joints.map((j) => j.profile)];
  }

  /** Per frame. Returns the profile-named pose while jogging (for the load meter), else null. */
  tick(now = performance.now()): Record<string, number> | null {
    const dt = this.last < 0 ? 0 : Math.min(0.1, Math.max(0, now - this.last) / 1000);
    this.last = now;
    const got = this.wb.active && this.wb.top ? this.read() : null;
    this.owns = !!got;
    this.el.hidden = !got;
    if (!got) {
      this.prev = [];
      return null;
    }
    this.index();
    const { pad, source } = got;
    const btn = (i: number) => (pad.buttons[i] ?? 0) > 0.05;
    const edge = (i: number) => btn(i) && !((this.prev[i] ?? 0) > 0.05);
    const opts = this.options;
    if (edge(B.right)) this.sel = (this.sel + 1) % opts.length;
    if (edge(B.left)) this.sel = (this.sel - 1 + opts.length) % opts.length;
    if (edge(B.triangle)) this.wb.home();
    const fine = btn(B.cross) ? 0.25 : 1;
    const pick = opts[this.sel];
    const group = GROUPS.find((g) => g.name === pick);
    const ax = (i: number) => expo(pad.axes[i] ?? 0);
    const input: [string, number][] = [];
    if (group) {
      const m = group.map;
      if (m.lx) input.push([m.lx, ax(0)]);
      if (m.ly) input.push([m.ly, ax(1)]);
      if (m.rx) input.push([m.rx, ax(2)]);
      if (m.ry) input.push([m.ry, ax(3)]);
      if (m.trig) input.push([m.trig, (pad.buttons[B.r2] ?? 0) - (pad.buttons[B.l2] ?? 0)]);
    } else if (pick) {
      input.push([pick, -ax(1) + (btn(B.up) ? 1 : 0) - (btn(B.down) ? 1 : 0)]);
    }
    let moved = false;
    for (const [pj, u] of input) {
      if (!u) continue;
      const j = this.joints.find((x) => x.profile === pj);
      if (!j) continue;
      const rate = 0.6 * (vmax.get(pj) ?? 60) * fine;
      const cur = j.node.pose[j.id] ?? 0;
      const next = Math.min(j.max, Math.max(j.min, cur + u * rate * dt));
      if (next !== cur) {
        j.node.pose[j.id] = next;
        moved = true;
      }
    }
    // one pose/emit per frame, whatever moved (Workbench.setJoint re-poses per call)
    if (moved && input.length) {
      const j0 = this.joints.find((x) => x.profile === input.find(([, u]) => u)?.[0]);
      if (j0) this.wb.setJoint(j0.node, j0.id, j0.node.pose[j0.id]);
    }
    this.prev = pad.buttons.slice();
    const pose: Record<string, number> = {};
    for (const j of this.joints) pose[j.profile] = j.node.pose[j.id] ?? 0;
    this.render(opts, pose, source, group, fine < 1);
    return pose;
  }

  private render(opts: string[], pose: Record<string, number>, source: string, group: Group | undefined, fine: boolean) {
    const hot = new Set(group ? Object.values(group.map) : [opts[this.sel]]);
    const show = (group ? Object.values(group.map) : [opts[this.sel]]).filter((p) => p && p in pose) as string[];
    const unit = (p: string) => (p === 'head_lift' ? 'mm' : '°');
    this.el.innerHTML =
      `<header><b>Pad jog</b><span>${escape(source)}${fine ? ' · fine' : ''}</span></header>` +
      `<div class="sel">${opts.map((o, i) => `<em class="${i === this.sel ? 'on' : ''}">${o}</em>`).join('')}</div>` +
      `<div class="vals">${show.map((p) => `<div class="${hot.has(p) ? 'hot' : ''}"><small>${p}</small>${pose[p].toFixed(1)}${unit(p)}</div>`).join('')}</div>` +
      `<div class="keys">${group ? 'L stick pan/tilt · R stick roll/visor · R2/L2 lift' : 'L stick ↕ or D-pad ↕ jogs it'} · D-pad ◀▶ select · △ Home · ✕ fine</div>`;
  }
}

function escape(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}
