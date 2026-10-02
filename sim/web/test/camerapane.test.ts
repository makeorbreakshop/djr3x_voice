import { describe, expect, it } from 'vitest';
import { CAM_PANE_HTML, canStartDrag, clampPane } from '../src/camerapane';

/** Enough of an Element for `canStartDrag`: `closest` over a chain of tag + classes. */
function el(chain: { tag: string; cls?: string }[]) {
  const matches = (n: { tag: string; cls?: string }, sel: string) =>
    sel.split(',').some((s) => {
      s = s.trim();
      if (s.startsWith('.')) return (n.cls ?? '').split(' ').includes(s.slice(1));
      return n.tag === s;
    });
  return { closest: (sel: string) => (chain.some((n) => matches(n, sel)) ? {} : null) } as unknown as Element;
}

describe('Camera pane: where a press starts a drag', () => {
  it('drags from the title bar and from the picture itself', () => {
    expect(canStartDrag(el([{ tag: 'span', cls: 'cam-title' }, { tag: 'div', cls: 'cam-head' }]))).toBe(true);
    expect(canStartDrag(el([{ tag: 'div', cls: 'cam-head' }]))).toBe(true);
    expect(canStartDrag(el([{ tag: 'canvas' }, { tag: 'div', cls: 'cam-body' }]))).toBe(true);
    expect(canStartDrag(el([{ tag: 'video' }, { tag: 'div', cls: 'cam-body' }]))).toBe(true);
  });
  it('leaves the controls and the resize grip alone', () => {
    expect(canStartDrag(el([{ tag: 'button', cls: 'cam-chip' }, { tag: 'div', cls: 'cam-head' }]))).toBe(false);
    expect(canStartDrag(el([{ tag: 'svg' }, { tag: 'button', cls: 'cam-close' }]))).toBe(false);
    expect(canStartDrag(el([{ tag: 'select', cls: 'cam-dev' }]))).toBe(false);
    expect(canStartDrag(el([{ tag: 'div', cls: 'cam-grip' }]))).toBe(false);
  });
});

describe('Camera pane: the title bar keeps somewhere to grab', () => {
  const head = CAM_PANE_HTML.slice(CAM_PANE_HTML.indexOf('cam-head'), CAM_PANE_HTML.indexOf('cam-body'));
  it("uses compact chips, not the panels' full-width switch rows (button.toggle fills the bar)", () => {
    expect(head).not.toMatch(/class="[^"]*\btoggle\b/);
    for (const c of ['cam-track', 'cam-ov', 'cam-mirror']) expect(head).toMatch(new RegExp(`class="[^"]*\\b${c}\\b[^"]*\\bcam-chip\\b`));
  });
  it('has a title to grab', () => {
    expect(head).toContain('cam-title');
  });
});

describe('Camera pane: placement', () => {
  const vp = { w: 1600, h: 900 };
  it('follows a drag and keeps the whole pane on screen', () => {
    expect(clampPane({ x: 100, y: 120, w: 400 }, 16 / 9, vp)).toEqual({ x: 100, y: 120, w: 400 });
    const h = 400 / (16 / 9) + 34;
    expect(clampPane({ x: 1500, y: 880, w: 400 }, 16 / 9, vp)).toEqual({ x: 1200, y: 900 - h, w: 400 });
    expect(clampPane({ x: -50, y: -20, w: 400 }, 16 / 9, vp)).toEqual({ x: 0, y: 0, w: 400 });
  });
  it('resizes between a minimum and the window width', () => {
    // Narrow enough to tuck away, wide enough for the title bar: a grab area, three chips, close.
    expect(clampPane({ x: 0, y: 0, w: 100 }, 16 / 9, vp).w).toBe(300);
    expect(clampPane({ x: 0, y: 0, w: 5000 }, 16 / 9, vp).w).toBe(1600 - 16);
  });
});
