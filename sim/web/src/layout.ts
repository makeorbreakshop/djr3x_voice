/**
 * The two side panels collapse to rails: Scene (left, `[`) and R3X (right, `]`); `\` is the
 * focus view (both collapsed, then back to what they were). Each panel's state is this
 * viewer's own (localStorage, try/catch). A viewer who has never chosen gets the Scene panel
 * collapsed below 1200 px. Every change calls `relayout` so the 3D view re-fits the space
 * between the panels.
 */

const KEY = 'r3x.panels';
/** The Scene panel's collapse used to live in its own key; read it once as the choice. */
const OLD_SCENE_KEY = 'r3x.scenePanel';
const NARROW = 1200;

export type Side = 'scene' | 'r3x';
type Collapsed = Record<Side, boolean>;
/** `undefined` = never chosen. `focusPrev` = the states the focus view will restore. */
interface Stored { scene?: boolean; r3x?: boolean; focusPrev?: Collapsed }

function load(): Stored {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Stored;
    if (s.scene === undefined) {
      const old = JSON.parse(localStorage.getItem(OLD_SCENE_KEY) ?? '{}') as { collapsed?: boolean };
      if (typeof old.collapsed === 'boolean') s.scene = old.collapsed;
    }
    return s;
  } catch {
    return {};
  }
}

function save(s: Stored) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked: lasts this page load */
  }
}

const SIDES: Record<Side, { panel: string; button: string; body: string; cls: string; name: string; key: string }> = {
  scene: { panel: 'scene-panel', button: 'scene-collapse', body: 'scene-body', cls: 'scene-collapsed', name: 'Scene', key: '[' },
  r3x: { panel: 'panel', button: 'r3x-collapse', body: 'r3x-body', cls: 'r3x-collapsed', name: 'R3X', key: ']' },
};

/** Typing in a field owns its keys. */
const typing = (e: KeyboardEvent) => {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
};

export interface Panels {
  set(side: Side, collapsed: boolean, remember?: boolean): void;
}

export function mountPanels(relayout: () => void): Panels {
  const $ = (id: string) => document.getElementById(id)!;
  const st = load();
  const now: Collapsed = { scene: false, r3x: false };

  const apply = (side: Side, on: boolean) => {
    const d = SIDES[side];
    now[side] = on;
    document.body.classList.toggle(d.cls, on);
    const b = $(d.button);
    b.setAttribute('aria-expanded', String(!on));
    b.title = `${on ? 'Expand' : 'Collapse'} the ${d.name} panel ( ${d.key} )`;
    b.setAttribute('aria-label', `${on ? 'Expand' : 'Collapse'} the ${d.name} panel`);
    b.setAttribute('aria-keyshortcuts', d.key);
  };

  const set = (side: Side, on: boolean, remember = true) => {
    // Focus inside a panel that is closing moves to its expand button.
    const d = SIDES[side];
    if (on && $(d.body).contains(document.activeElement)) $(d.button).focus();
    apply(side, on);
    if (remember) {
      st[side] = on;
      delete st.focusPrev;
      save(st);
    }
    relayout();
  };

  const focusView = () => {
    if (now.scene && now.r3x) {
      const prev = st.focusPrev ?? { scene: false, r3x: false };
      set('scene', prev.scene);
      set('r3x', prev.r3x);
    } else {
      const prev = { ...now };
      set('scene', true);
      set('r3x', true);
      st.focusPrev = prev;
      save(st);
    }
  };

  // A narrow window starts with the Scene panel collapsed until the viewer chooses.
  const defaults = () => {
    if (st.scene === undefined) apply('scene', window.innerWidth < NARROW);
    if (st.r3x === undefined) apply('r3x', false);
  };
  apply('scene', st.scene ?? window.innerWidth < NARROW);
  apply('r3x', st.r3x ?? false);
  addEventListener('resize', () => {
    const was = { ...now };
    defaults();
    if (was.scene !== now.scene) relayout();
  });

  for (const side of ['scene', 'r3x'] as Side[]) {
    const d = SIDES[side];
    $(d.button).onclick = () => set(side, !now[side]);
    // Clicking a collapsed rail anywhere but its own controls opens it.
    $(d.panel).addEventListener('click', (e) => {
      if (now[side] && !(e.target as HTMLElement).closest('button, a, input, select')) set(side, false);
    });
  }

  addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey || typing(e)) return;
    if (e.key === '[') set('scene', !now.scene);
    else if (e.key === ']') set('r3x', !now.r3x);
    else if (e.key === '\\') focusView();
    else return;
    e.preventDefault();
  });

  return { set };
}
