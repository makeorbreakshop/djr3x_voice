/**
 * Build's frame decisions (post.ts PostPipeline, the CAD-viewer rendering of Build), pure so they can be
 * tested without a GL context: which kind of frame to draw, at what scale, which AO it draws (aoStep), and the
 * camera's share of the scene signature that decides whether anything changed at all.
 */

export type BuildFrame = 'moving' | 'changing' | 'still' | 'skip';

/**
 * The camera matrices' share of the scene signature, each element to 1e-4 (0.1 mm, 0.1 mrad: about a
 * seventh of a pixel on the droid at 1440x900). OrbitControls' damping keeps shrinking the last drag's
 * delta by (1 - dampingFactor) a frame long after it stops firing 'change' (its own 1e-6 threshold), so
 * the raw matrix crept by 1e-6..1e-8 every frame for many seconds and never matched the drawn one:
 * Build drew still and changing frames by turns, the visible resolution pulse.
 */
export function cameraHash(e: ArrayLike<number>, k: number): number {
  let h = 0;
  for (let i = 0; i < e.length; i++) h += Math.round(e[i] * 1e4) * 1e-4 * (k + i * 0.618);
  return h;
}

/**
 * Build's frame kind: moving while input is recent, then one frame for a change ('changing' while the
 * scene keeps changing after a still frame, 'still' for the first after input), and once nothing changed,
 * nothing ('skip') but a refresh every `refreshMs`.
 */
export function buildFrameKind(s: { settled: boolean; sig: number; drawnSig: number; stillAt: number; now: number; refreshMs: number }): BuildFrame {
  if (!s.settled) return 'moving';
  if (s.sig !== s.drawnSig) return s.drawnSig !== s.drawnSig || s.stillAt < 0 ? 'still' : 'changing';
  if (s.stillAt < 0 || s.now - s.stillAt >= s.refreshMs) return 'still';
  return 'skip';
}

/** A dynamic resolution's state: its current scale and its cap. */
export interface Scale { scale: number; max: number }

/**
 * The pixel ratio for a frame. Build uses its own scale (`build`: native, lowered only on a machine that
 * cannot hold frame rate there) for every kind, so moving, changing and still frames share one buffer:
 * a scale change reallocates the canvas and every pass's targets (25-80 ms at 2880x1800), and it used to
 * happen at the start and end of every gesture (moving at the quality's cap, still at native). The other
 * modes use the quality's dynamic scale (`other`). `kind` null: not a Build frame.
 */
export function frameScale(kind: BuildFrame | null, interacting: boolean, inBuild: boolean, build: Scale, other: Scale): number {
  const dyn = inBuild ? build : other;
  return kind === 'still' || kind === 'changing' ? dyn.max
    : kind === 'moving' || interacting ? dyn.scale : dyn.max;
}

/** Build's still-frame AO accumulates over this many frames (aoStep). */
export const SETTLE_FRAMES = 4;

/**
 * Which AO a Build frame draws, and the still frame's accumulation (Speckle's progressive model):
 *
 *   moving, changing   the cheap AO pass ('move': half resolution, 8 samples, no transparency pre-pass,
 *                      ~2-3 ms over the plain frame at 3140x2474), and the accumulation starts over
 *   still              the full AO pass ('still': full resolution, a quarter of its samples a frame),
 *                      accumulating: the first still frame resets it (`reset`), then SETTLE_FRAMES - 1
 *                      more frames add to it ('skip' frames become draws until `refined` reaches
 *                      `frames`), each under a frame's budget; then nothing is drawn
 *   a refresh still    (after the accumulation finished) one more frame on top of it, no reset
 *
 * `refined`: still frames accumulated so far (0 after a moving or changing frame). Outside Build
 * (`inBuild` false or `kind` null) the composer's own pass, unchanged.
 */
export interface AoStep { draw: boolean; pass: 'move' | 'still' | null; reset: boolean; refined: number }
export function aoStep(inBuild: boolean, kind: BuildFrame | null, refined: number, frames = SETTLE_FRAMES): AoStep {
  if (!inBuild || kind === null) return { draw: kind !== 'skip', pass: null, reset: false, refined };
  if (kind === 'moving' || kind === 'changing') return { draw: true, pass: 'move', reset: false, refined: 0 };
  if (kind === 'still') return refined === 0
    ? { draw: true, pass: 'still', reset: true, refined: 1 }
    : { draw: true, pass: 'still', reset: false, refined: Math.min(frames, refined + 1) };
  return refined > 0 && refined < frames
    ? { draw: true, pass: 'still', reset: false, refined: refined + 1 }
    : { draw: false, pass: null, reset: false, refined };
}
