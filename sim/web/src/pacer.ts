/**
 * Frame pacing: draw only as often as the picture needs.
 *
 * The render loop asks `next()` for its frames: requestAnimationFrame (which the browser stops
 * for a hidden tab) while drawing at full rate, a timer and then one animation frame when paced.
 * Each wake checks the drawn state and draws only when due:
 *
 *   interacting   60 fps  a pointer, wheel or key in the page, the orbit camera moving or
 *                         settling, a resize - and 1.5 s after the last of them
 *   active        cap     something drawn moves fast: a joint faster than 12 deg/s (a glance,
 *                         a gesture), a face LED or a stage light changing quickly (speech,
 *                         a cue) - and 0.25 s after
 *   quiet         floor   nothing moves, or only slowly (breathing, idle drift: well under a
 *                         pixel per frame at the floor rate); also catches anything this does
 *                         not see (a texture finishing, a bake)
 *
 * `cap` and `floor` come from the Quality setting (post.ts).
 */

export interface PaceRates {
  /** fps while something moves fast but nobody is interacting. */
  active: number;
  /** fps while nothing moves fast. */
  quiet: number;
}

const INTERACT_HOLD_MS = 1500;
const ACTIVE_HOLD_MS = 250;

/**
 * Per-second rates above which a change counts as fast, by top-level key of the drawn state:
 * joints in deg (or mm), stage light flux (~0-2), anything else LED levels (0-255). The chest
 * panel's random twinkle and the servo targets (the joints again) never count.
 */
const FAST: Record<string, number> = { joints: 12, stage: 1.5, chest: Infinity, servo: Infinity };
const FAST_DEFAULT = 150;

/** The drawn state's numbers, flattened in a stable order, with each one's fast threshold. */
function flatten(v: unknown, into: number[], limits: number[] | null, limit = FAST_DEFAULT) {
  if (typeof v === 'number') {
    into.push(v);
    limits?.push(limit);
  } else if (Array.isArray(v) || ArrayBuffer.isView(v)) {
    const a = v as ArrayLike<unknown>;
    for (let i = 0; i < a.length; i++) flatten(a[i], into, limits, limit);
  } else if (v && typeof v === 'object') {
    for (const k in v) flatten((v as Record<string, unknown>)[k], into, limits, limit === FAST_DEFAULT ? FAST[k] ?? limit : limit);
  }
}

export class FramePacer {
  private interactUntil = -Infinity;
  private activeUntil = -Infinity;
  private lastDraw = -Infinity;
  private prev: number[] = [];
  private limits: number[] = [];
  private prevAt = -Infinity;
  private scratch: number[] = [];
  /** Frames drawn since load (for measurement). */
  frames = 0;
  /** Draw every animation frame until then (a profiling run, framediag.ts). */
  private continuousUntil = -Infinity;

  constructor(public rates: PaceRates, private readonly enabled = true) {}

  /** A user interaction (or anything that should run at full rate for a moment). */
  interact(now = performance.now()) {
    this.interactUntil = now + INTERACT_HOLD_MS;
    this.wakeNow();
  }

  private timer: ReturnType<typeof setTimeout> | null = null;
  private sleeper: FrameRequestCallback | null = null;

  /**
   * Request the next animation frame: straight away while drawing at full rate, otherwise
   * sleep on a timer until just before the next draw is due. An idle page then wakes 10-30
   * times a second instead of 60 (the browser's frame loop stops between), and still checks
   * the state for fast motion every time it wakes.
   */
  next(cb: FrameRequestCallback) {
    const now = performance.now();
    const fps = this.fps(now);
    const wait = fps === Infinity ? 0 : this.lastDraw + 1000 / fps - now - 6;
    if (wait <= 4) {
      requestAnimationFrame(cb);
      return;
    }
    this.sleeper = cb;
    this.timer = setTimeout(() => this.wakeNow(), wait);
  }

  private wakeNow() {
    if (!this.sleeper) return;
    const cb = this.sleeper;
    this.sleeper = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    requestAnimationFrame(cb);
  }

  /** Something drawn changed that the state does not show (a scene or quality switch). */
  touch(now = performance.now()) {
    this.activeUntil = now + ACTIVE_HOLD_MS;
    this.lastDraw = -Infinity;
  }

  get interacting() {
    return performance.now() < this.interactUntil;
  }

  /** Draw every animation frame for `ms` (0 ends it early). */
  continuous(ms: number, now = performance.now()) {
    this.continuousUntil = ms > 0 ? now + ms : -Infinity;
    this.wakeNow();
  }

  /**
   * Why frames come as often as they do, for the frame diagnostics: a low rate while `quiet`
   * is the pacing working, not the renderer struggling.
   */
  state(now = performance.now()): 'continuous' | 'interacting' | 'active' | 'quiet' {
    if (!this.enabled || now < this.continuousUntil) return 'continuous';
    if (now < this.interactUntil) return 'interacting';
    return now < this.activeUntil ? 'active' : 'quiet';
  }

  /** Target fps now. */
  fps(now: number): number {
    if (!this.enabled || now < this.interactUntil || now < this.continuousUntil) return Infinity;
    return now < this.activeUntil ? this.rates.active : this.rates.quiet;
  }

  /** Did the state move fast since the last draw? (Structure changes count as fast.) */
  private moving(now: number, state: unknown): boolean {
    const cur = this.scratch;
    cur.length = 0;
    flatten(state, cur, null);
    if (cur.length !== this.prev.length) {
      this.limits = [];
      flatten(state, [], this.limits);
      return this.prev.length > 0 || cur.length > 0;
    }
    const dt = Math.max(1e-3, (now - this.prevAt) / 1000);
    for (let i = 0; i < cur.length; i++) {
      if (Math.abs(cur[i] - this.prev[i]) > this.limits[i] * dt) return true;
    }
    return false;
  }

  /**
   * Call once per animation frame with the drawn state (joints, LEDs, stage lights);
   * true = draw now. The 2 ms slack keeps a 30 fps cap on every other 60 Hz vsync.
   */
  due(now: number, state: unknown): boolean {
    if (this.enabled && this.moving(now, state)) this.activeUntil = now + ACTIVE_HOLD_MS;
    const fps = this.fps(now);
    if (now - this.lastDraw < 1000 / fps - 2) return false;
    this.lastDraw = now;
    this.frames++;
    // Rates are measured from the state as last drawn.
    [this.prev, this.scratch] = [this.scratch, this.prev];
    this.prevAt = now;
    return true;
  }

  /** Wake on input anywhere in the page, and on resize. */
  listen(target: Window = window) {
    const wake = () => this.interact();
    for (const ev of ['pointerdown', 'pointermove', 'wheel', 'keydown', 'input', 'resize', 'focus']) {
      target.addEventListener(ev, wake, { passive: true, capture: true });
    }
  }
}
