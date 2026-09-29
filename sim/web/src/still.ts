/**
 * Deterministic "still" mode for screenshot regression (`?still`, see
 * scripts/render-harness.mjs).
 *
 * The sim is driven by wall-clock time and Math.random in several places (firmware blink
 * timers, the performer's glances, servo latency, chest sparkles, the renderer's noise).
 * Rather than thread a clock and an RNG through every module, still mode replaces the two
 * globals before anything else reads them:
 *
 * - `performance.now()` returns a virtual clock that starts at 0 and only moves when the
 *   harness asks: `__r3xStill.advance(n)` steps it 1/60 s per rendered frame for n frames,
 *   `__r3xStill.hold(n)` renders n frames with time frozen. So a shot's simulated time
 *   never depends on how fast the machine renders or how long the model took to load.
 * - `Math.random()` is a seeded mulberry32 (`?seed=`, default 1).
 *
 * post.ts reads STILL to freeze film grain and to pin the render resolution, and main.ts
 * drops the control-panel offset from the camera. Nothing here runs unless the URL has
 * `?still`.
 */

const params = new URLSearchParams(location.search);

export const STILL = params.has('still');

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface StillControl {
  /** Virtual milliseconds since page start. */
  readonly now: number;
  /** Frames rendered since still mode started. */
  readonly frames: number;
  /** Render n frames, advancing the virtual clock 1/60 s per frame. */
  advance(n: number): Promise<void>;
  /** Render n frames with the clock frozen (camera moves, resize settles). */
  hold(n: number): Promise<void>;
}

if (STILL) {
  const FRAME_MS = 1000 / 60;
  let virtualMs = 0;
  let frames = 0;
  let stepping = 0;
  const waiters: { at: number; resolve: () => void }[] = [];

  Math.random = mulberry32(Number(params.get('seed') ?? 1) || 1);
  performance.now = () => virtualMs;

  // Registered at import time, so it runs before main.ts's frame loop in every frame
  // (rAF callbacks fire in registration order and both re-register each frame).
  const tick = () => {
    requestAnimationFrame(tick);
    frames++;
    if (stepping > 0) {
      stepping--;
      virtualMs += FRAME_MS;
    }
    // Resolve on the *next* tick, so the frame the caller waited for has been drawn.
    for (let i = waiters.length - 1; i >= 0; i--) {
      if (frames > waiters[i].at) {
        waiters[i].resolve();
        waiters.splice(i, 1);
      }
    }
  };
  requestAnimationFrame(tick);

  const wait = (n: number) => new Promise<void>((resolve) => waiters.push({ at: frames + Math.max(1, n), resolve }));
  const control: StillControl = {
    get now() { return virtualMs; },
    get frames() { return frames; },
    advance(n) {
      stepping += n;
      return wait(n);
    },
    hold(n) {
      return wait(n);
    },
  };
  Object.assign(window, { __r3xStill: control });
  // A bare frame: no control panel or captions over the render.
  const style = document.createElement('style');
  style.textContent = '#panel, #caption { display: none !important; }';
  document.head.appendChild(style);
}
