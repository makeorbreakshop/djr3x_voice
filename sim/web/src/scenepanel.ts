/**
 * The Scene panel (left): how this viewer sees R3X. Nothing here changes robot state; every
 * choice is this browser's own (localStorage, try/catch). The camera presets, backdrop, work
 * light, Centres and quality are wired where they live (main.ts, post.ts); this module owns
 * the rail's section icons plus the Captions and Frame stats overlays (collapse: layout.ts).
 */

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

export interface FrameSource {
  readonly frames: number;
  readonly pixelRatio: number;
  readonly currentQuality: string;
}

/** Mount the panel; `open` expands it (layout.ts). */
export function mountScenePanel(post: FrameSource, open: () => void) {
  const $ = (id: string) => document.getElementById(id)!;
  const st = load();
  const panel = $('scene-panel');

  const press = (id: string, on: boolean) => $(id).setAttribute('aria-pressed', String(on));

  // A rail icon opens the panel at its section.
  panel.querySelectorAll<HTMLButtonElement>('[data-scene-open]').forEach((b) => {
    b.onclick = () => {
      open();
      const sec = $(b.dataset.sceneOpen!);
      sec.scrollIntoView({ block: 'nearest' });
      sec.querySelector<HTMLElement>('button, select')?.focus();
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

  const stats = $('frame-stats');
  let timer = 0;
  let prev = { frames: post.frames, at: performance.now() };
  const tick = () => {
    const now = performance.now();
    const fps = ((post.frames - prev.frames) * 1000) / Math.max(1, now - prev.at);
    prev = { frames: post.frames, at: now };
    stats.textContent = `${fps.toFixed(0)} fps drawn · ${post.pixelRatio.toFixed(2)}x · ${post.currentQuality}`;
  };
  const setStats = (on: boolean) => {
    st.stats = on;
    stats.hidden = !on;
    press('scene-stats', on);
    clearInterval(timer);
    if (on) {
      prev = { frames: post.frames, at: performance.now() };
      stats.textContent = '…';
      timer = window.setInterval(tick, 1000);
    }
  };
  $('scene-stats').onclick = (e) => {
    setStats(!st.stats);
    save(st);
    (e.currentTarget as HTMLElement).blur();
  };

  setCaptions(st.captions);
  setStats(st.stats);
}
