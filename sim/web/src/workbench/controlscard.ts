/**
 * The controls card (? or the ? by the view cube): Build's camera and part controls at a glance, mouse and
 * trackpad side by side, with this platform's key (⌘ or Ctrl), and the scroll device setting for when the
 * automatic mouse/trackpad guess is wrong (navigate.ts).
 */

import { PART_KEY_NAME, type Navigator, type ScrollDevice } from './navigate';

const K = (s: string) => `<kbd>${s}</kbd>`;

/** The card's rows: [what, mouse, trackpad]. */
export function controlRows(mod: string): [string, string, string][] {
  return [
    ['Orbit', 'Left drag', 'Click-drag · ⇧ two-finger scroll'],
    ['Pan', 'Right / middle drag · ⇧ left drag', 'Two-finger scroll'],
    ['Zoom', 'Wheel', 'Pinch'],
    ['Frame part', 'Double-click', 'Double-click'],
    ['Move part', `${mod} drag`, `${mod} drag`],
    ['All its joints', `${mod} ⇧ drag`, `${mod} ⇧ drag`],
  ];
}

export class ControlsCard {
  private el: HTMLDivElement | null = null;
  private back: HTMLElement | null = null;

  constructor(private readonly nav: Navigator) {}

  get open() {
    return !!this.el && !this.el.hidden;
  }

  toggle(on = !this.open) {
    if (on) this.show();
    else this.hide();
  }

  private show() {
    if (!this.el) this.build();
    this.back = document.activeElement as HTMLElement | null;
    this.sync();
    this.el!.hidden = false;
    this.el!.querySelector<HTMLButtonElement>('.bb-keys-close')!.focus();
  }

  hide() {
    if (!this.el || this.el.hidden) return;
    this.el.hidden = true;
    this.back?.focus?.();
  }

  private sync() {
    this.el?.querySelectorAll<HTMLButtonElement>('[data-scroll]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scroll === this.nav.wheel.setting)));
  }

  private build() {
    const el = document.createElement('div');
    el.className = 'bb-keys';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', 'Controls');
    el.hidden = true;
    const rows = controlRows(PART_KEY_NAME).map(([w, m, t]) => `<tr><th scope="row">${w}</th><td>${m}</td><td>${t}</td></tr>`).join('');
    el.innerHTML = `<header><b>Controls</b><button type="button" class="bb-keys-close" aria-label="Close (Esc)">×</button></header>
      <table><thead><tr><th></th><th scope="col">Mouse</th><th scope="col">Trackpad</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="bb-keys-row">${K('M')} Move mode ${K('F')} Frame ${K('H')} Home view ${K('←')}${K('→')}${K('↑')}${K('↓')} Nudge joint ${K('Esc')} Back ${K('?')} This card</p>
      <p class="bb-keys-row dim">Touch: one finger orbits · two pan and pinch · long-press a part, then drag, to move it</p>
      <div class="bb-keys-scroll" role="group" aria-label="Scroll device"><span>Scrolling is</span>
        <button type="button" data-scroll="auto">Auto</button><button type="button" data-scroll="trackpad">Trackpad</button><button type="button" data-scroll="mouse">Mouse</button></div>`;
    el.addEventListener('click', (e) => {
      const t = e.target as HTMLElement;
      const s = t.closest<HTMLButtonElement>('[data-scroll]');
      if (s) {
        this.nav.setScrollDevice(s.dataset.scroll as ScrollDevice);
        this.sync();
      } else if (t.closest('.bb-keys-close')) this.hide();
    });
    document.body.append(el);
    this.el = el;
  }
}
