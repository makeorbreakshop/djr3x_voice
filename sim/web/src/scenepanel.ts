/**
 * The Scene panel (left): how this viewer sees R3X. Nothing here changes robot state; every
 * choice is this browser's own (localStorage, try/catch). The camera presets, backdrop, work
 * light, Centres and quality are wired where they live (main.ts, post.ts), Lighting and
 * Rendering in rendersettings.ts; this module owns the rail's section icons plus the Captions
 * and Frame stats overlays (the frame diagnostics: framediag.ts; collapse: layout.ts).
 */

import { FrameDiag } from './framediag';
import type { PostPipeline } from './post';

const KEY = 'r3x.scenePanel';

interface Stored {
  captions: boolean;
  stats: boolean;
}

function load(): Stored {
  const d: Stored = { captions: true, stats: false };
  try {
    return { ...d, ...(JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Stored>) };
  } catch {
    return d;
  }
}

function save(s: Stored) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked: lasts this page load */
  }
}

/** Mount the panel; `open` expands it (layout.ts). */
export function mountScenePanel(post: PostPipeline, open: () => void) {
  const $ = (id: string) => document.getElementById(id)!;
  const st = load();
  const panel = $('scene-panel');

  const press = (id: string, on: boolean) => $(id).setAttribute('aria-pressed', String(on));

  // A rail icon opens the panel at its section.
  panel.querySelectorAll<HTMLButtonElement>('[data-scene-open]').forEach((b) => {
    b.onclick = () => {
      open();
      const sec = $(b.dataset.sceneOpen!);
      const det = sec.querySelector('details');
      if (det) det.open = true;
      sec.scrollIntoView({ block: 'nearest' });
      sec.querySelector<HTMLElement>(det ? 'summary' : 'button, select')?.focus();
    };
  });

  const setCaptions = (on: boolean) => {
    st.captions = on;
    document.body.classList.toggle('no-captions', !on);
    press('scene-captions', on);
  };
  $('scene-captions').onclick = (e) => {
    setCaptions(!st.captions);
    save(st);
    (e.currentTarget as HTMLElement).blur();
  };

  const diag = new FrameDiag(post, $('frame-stats'));
  $('scene-stats').title = 'Frame diagnostics: fps and why (pacing), frame and GPU time, draw calls, passes; Profile 10 s';
  const setStats = (on: boolean) => {
    st.stats = on;
    press('scene-stats', on);
    diag.setEnabled(on);
  };
  $('scene-stats').onclick = (e) => {
    setStats(!st.stats);
    save(st);
    (e.currentTarget as HTMLElement).blur();
  };

  setCaptions(st.captions);
  setStats(st.stats);
}
