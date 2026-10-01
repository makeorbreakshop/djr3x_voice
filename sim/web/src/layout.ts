/**
 * The two side panels collapse to rails: Scene (left, `[`) and R3X (right, `]`); `\` is the
 * focus view (both collapsed, then back to what they were). Each panel's state is this
 * viewer's own (localStorage, try/catch). The Scene panel remembers two states: in Build it is
 * the navigator (open until the viewer closes it); everywhere else it holds view settings and
 * starts as its rail. Every change calls `relayout` so the 3D view re-fits the space between
 * the panels.
 */

/** v2: the Scene panel's defaults changed with the panel redesign (a rail outside Build). */
const KEY = 'r3x.panels.v2';

export type Side = 'scene' | 'r3x';
type Collapsed = Record<Side, boolean>;
/** `undefined` = never chosen. `focusPrev` = the states the focus view will restore. */
interface Stored { scene?: boolean; sceneBuild?: boolean; r3x?: boolean; r3xStudio?: boolean; focusPrev?: Collapsed }

function load(): Stored {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Stored;
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

/**
 * Where a panel starts in a mode until the viewer picks (true = collapsed to its rail): the
 * Scene panel is Build's navigator (open) and a rail elsewhere (view settings), and a rail on
 * phones; the R3X panel is open except in Studio, whose timeline dock owns the stage.
 */
export function panelDefault(side: Side, mode: string, width: number): boolean {
  if (side === 'scene') return mode !== 'build' || width <= 720;
  return mode === 'studio';
}

export interface Panels {
  set(side: Side, collapsed: boolean, remember?: boolean): void;
}

export function mountPanels(relayout: () => void): Panels {
  const $ = (id: string) => document.getElementById(id)!;
  const st = load();
  const now: Collapsed = { scene: false, r3x: false };
  /** Build has its own Scene panel state (the navigator). */
  let build = document.body.dataset.mode === 'build';
  const sceneKey = () => (build ? 'sceneBuild' : 'scene') as 'scene' | 'sceneBuild';
  const sceneDefault = () => st[sceneKey()] ?? panelDefault('scene', build ? 'build' : 'other', window.innerWidth);
  /** Studio's timeline dock owns the stage: the R3X panel starts as its rail there. */
  let studio = document.body.dataset.mode === 'studio';
  const r3xKey = () => (studio ? 'r3xStudio' : 'r3x') as 'r3x' | 'r3xStudio';
  const r3xDefault = () => st[r3xKey()] ?? panelDefault('r3x', studio ? 'studio' : 'other', window.innerWidth);

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
      st[side === 'scene' ? sceneKey() : r3xKey()] = on;
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

  apply('scene', sceneDefault());
  apply('r3x', r3xDefault());
  // Entering or leaving Build (Scene panel) or Studio (R3X panel) swaps to that context's state.
  addEventListener('r3x:mode', (e) => {
    const m = String((e as CustomEvent).detail);
    build = m === 'build';
    studio = m === 'studio';
    let changed = false;
    if (sceneDefault() !== now.scene) {
      apply('scene', sceneDefault());
      changed = true;
    }
    if (r3xDefault() !== now.r3x) {
      apply('r3x', r3xDefault());
      changed = true;
    }
    if (changed) relayout();
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
