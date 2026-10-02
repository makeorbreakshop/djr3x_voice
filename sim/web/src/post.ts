import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { SMAAPass } from 'three/addons/postprocessing/SMAAPass.js';
import { LUTPass } from 'three/addons/postprocessing/LUTPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { LUTCubeLoader } from 'three/addons/loaders/LUTCubeLoader.js';
import { N8AOPass } from 'n8ao';
import { STILL } from './still';
import { FramePacer, type PaceRates } from './pacer';
import { aoStep, buildFrameKind, cameraHash, frameScale } from './buildframe';

/**
 * The render pipeline after the scene is lit - everything between "the GPU drew the
 * droid" and "pixels on screen":
 *
 *   N8AO, or RenderPass (Performance)  HDR, half-float, linear
 *   UnrealBloomPass, threshold 1.0    only the LEDs cross 1.0 (look.ts's highlight knee); only
 *                                     the excess blooms, soft-capped (capBloomInput)
 *   OutputPass                        tone mapping + sRGB
 *   SMAA                              edges (no MSAA: every pass is a full-screen quad)
 *   LUTPass                           the grade
 *   film                              vignette, edge-only chromatic aberration, grain
 *
 * Resolution is dynamic: the canvas renders at up to the quality's cap (not the display's
 * 2x) and steps down when frames run long while someone is interacting, back up when there
 * is headroom. SMAA recovers the edges the lower scale costs, and the saving pays for AO.
 * Once the interaction ends the cap is back (a lowered scale used to stick until the next
 * interaction found headroom, so a slow first frame - a model upload - left the view soft).
 * Build goes further (see SETTLE_MS): a still view is drawn once at the native resolution.
 *
 * Frames are paced (pacer.ts): 60 fps only while someone interacts, the quality's cap while
 * the scene changes, a low floor while nothing does.
 *
 *   quality       scale cap  AO                  bloom      changing  quiet
 *   performance   1.0x       off                 1/4 res    30 fps    10 fps
 *   balanced      1.25x      half res, 16 spp    1/4 res    30 fps    15 fps   (default)
 *   high          1.5x       half res, 16 spp +  1/2 res    60 fps    15 fps
 *                            8-tap denoise
 *
 * Exposure, tone mapper, bloom, AO and the environment level are also live controls (Scene ->
 * Rendering, rendersettings.ts), layered over the quality level.
 *
 * URL flags: `?quality=performance|balanced|high` (also the viewport's control; remembered
 * per browser), `?pace=0` (draw every animation frame), `?tonemap=neutral|agx|aces`, `?dpr=<n>`
 * (fixed scale, no adaptation),
 * `?lut=<url.cube>` (grade from a file instead of the built-in one), `?grain=0`,
 * `?post=hot` (HDR check: magenta where a pixel exceeds 1.0 before bloom, i.e. what
 * blooms; yellow for 0.9-1.0), `?post=ao` (the AO term alone, high quality only).
 */

export type Quality = 'performance' | 'balanced' | 'high';

/**
 * Build (setClean) renders like a CAD viewer: frames while someone moves the view, one more
 * once they stop, then nothing until something changes.
 *
 *   moving     input in the last SETTLE_MS   Build's own dynamic scale (`buildDyn`): native
 *                                            (devicePixelRatio, at most 2x) unless this machine
 *                                            cannot hold 60 fps there
 *   changing   settled, the scene moving     native (a demo, a sweep, a hover highlight)
 *   still      settled, nothing changed      once: native; then no frame at all (one every
 *                                            REFRESH_MS)
 *
 * One resolution for all three: a scale change reallocates the canvas and every pass's targets
 * (N8AO's included), 25-80 ms at 2880x1800 measured, and it used to happen at the start and end
 * of every gesture (moving at the quality's cap, still at native) and on hovers (a highlight
 * drew 'changing' at the cap, then 'still' at native): a hitch and a soft/sharp flip each time.
 * Native costs ~3-10 ms a frame on an M-series Mac even with every part showing (X-ray: ~800
 * draws, 590k triangles), so Build only drops below it where frames really run long, and comes
 * back up slowly (DynamicResolution's backoff).
 *
 * AO in Build: full resolution, denoised, a radius for millimetre parts, and transparency-aware
 * so the ghosted shells (which write depth) neither take nor cast it.
 */
const SETTLE_MS = 150;
/** No real input (a hover does not count) this long and the dynamic scale's last drop is forgotten. */
const FORGET_MS = 4000;
const REFRESH_MS = 3000;
const NATIVE_MAX = 2;
const BUILD_AO = { radius: 0.025, falloff: 0.6, intensity: 0.6 };
/**
 * Build's two AO configurations (buildframe.ts aoStep). Still: full resolution and transparency-aware as
 * before, but 4 samples a frame with a light denoise, accumulated over SETTLE_FRAMES frames (N8AO's
 * `accumulate`: a running average with the noise rotated each frame), so the 16 samples of the
 * Medium preset arrive in four ~14-16 ms frames instead of one ~20 ms frame at 3140x2474. Moving: a
 * second pass at half resolution, 8 samples, no transparency pre-pass: ~2-3 ms over the plain frame
 * (~9-10 ms) where full AO was ~10, so a drag keeps its shading and stays inside 16.7 ms.
 */
const BUILD_STILL_AO = { aoSamples: 4, denoiseSamples: 4 };
const BUILD_MOVE_AO = { halfRes: true, aoSamples: 8, denoiseSamples: 4, denoiseRadius: 12 };
export type ToneMap = 'neutral' | 'agx' | 'aces';

const params = new URLSearchParams(location.search);
const QUALITY_KEY = 'r3x.quality';

interface QualityPreset {
  /** Render scale cap (x CSS pixels). */
  scale: number;
  ao: 'Low' | 'Medium' | null;
  /** Bloom resolution as a fraction of the render size (UnrealBloomPass's own is 1/2). */
  bloom: number;
  pace: PaceRates;
}

export const QUALITY: Record<Quality, QualityPreset> = {
  performance: { scale: 1.0, ao: null, bloom: 0.25, pace: { active: 30, quiet: 10 } },
  balanced: { scale: 1.25, ao: 'Low', bloom: 0.25, pace: { active: 30, quiet: 15 } },
  high: { scale: 1.5, ao: 'Medium', bloom: 0.5, pace: { active: 60, quiet: 15 } },
};

/** A quality name, with the old two-level names mapped on. */
export function parseQuality(v: string | null): Quality | null {
  if (v === 'low') return 'performance';
  return v && v in QUALITY ? (v as Quality) : null;
}

function storedQuality(): Quality | null {
  try {
    return parseQuality(localStorage.getItem(QUALITY_KEY));
  } catch {
    return null;
  }
}

/** URL flag, else what this browser picked last, else balanced. */
export function initialQuality(): Quality {
  return parseQuality(params.get('quality')) ?? storedQuality() ?? 'balanced';
}

/**
 * Neutral is the default: it keeps the burnt-orange paint's hue (AgX pulls it toward
 * salmon) and still whitens LED cores once they run a few stops past 1.0. `exposureScale`
 * multiplies whatever exposure the booth set up, so AgX compares at a matched brightness.
 */
const TONE: Record<ToneMap, { mapping: THREE.ToneMapping; exposureScale: number }> = {
  neutral: { mapping: THREE.NeutralToneMapping, exposureScale: 1.0 },
  agx: { mapping: THREE.AgXToneMapping, exposureScale: 1.35 },
  aces: { mapping: THREE.ACESFilmicToneMapping, exposureScale: 0.9 },
};

/** The URL's tone mapper, else neutral. */
export function initialToneMap(): ToneMap {
  const t = params.get('tonemap');
  return t && t in TONE ? (t as ToneMap) : 'neutral';
}

// ------------------------------------------------------------------ bloom input

/**
 * What the bloom blurs. UnrealBloomPass's own high pass keeps a pixel's WHOLE value once its
 * luminance crosses the threshold, so an LED driven to 20x (the eye bulbs: that is what makes
 * the tone mapper whiten their cores) poured 20x into the blur and haloed the whole face.
 * Here only the excess over the threshold blooms, and it is soft-capped at uCap: a core can
 * be as hot as the tone mapper wants, but its halo stays a modest, bounded glow.
 */
function capBloomInput(bloom: UnrealBloomPass, cap: number): { value: number } {
  const u = { value: cap };
  const m = bloom.materialHighPassFilter;
  m.uniforms.uCap = u;
  m.fragmentShader = /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float luminosityThreshold;
    uniform float uCap;
    varying vec2 vUv;
    void main() {
      vec3 c = texture2D(tDiffuse, vUv).rgb;
      float m = max(max(c.r, c.g), c.b);
      float e = max(m - luminosityThreshold, 0.0);
      float s = uCap * (1.0 - exp(-e / max(uCap, 1e-3)));
      gl_FragColor = vec4(c * (s / max(m, 1e-4)), 1.0);
    }`;
  m.needsUpdate = true;
  return u;
}

/** Per-frame measurement hooks (framediag.ts); null = none, no cost. */
export interface RenderProbe {
  frameStart(): void;
  frameEnd(): void;
}

// ------------------------------------------------------------------ the grade

/**
 * The built-in grade as a 3D LUT, in display (sRGB) space, generated here so there is no
 * LUT file to license. Subtle on purpose: a gentle S-curve, cool shadows / warm
 * highlights (the booth's blue fill against its tungsten key), a touch more saturation in
 * the mid-tones, and a slightly lifted, faintly cool film black.
 */
export function makeGradeLut(size = 33): THREE.Data3DTexture {
  const data = new Uint8Array(size * size * size * 4);
  const smooth = (e0: number, e1: number, x: number) => {
    const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
    return t * t * (3 - 2 * t);
  };
  let i = 0;
  for (let b = 0; b < size; b++) {
    for (let g = 0; g < size; g++) {
      for (let r = 0; r < size; r++) {
        let c = [r / (size - 1), g / (size - 1), b / (size - 1)];
        // S-curve: 15% of the way to smoothstep.
        c = c.map((v) => v + 0.15 * (v * v * (3 - 2 * v) - v));
        const l = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
        const sh = 1 - smooth(0.0, 0.45, l);
        const hi = smooth(0.5, 1.0, l);
        const mid = 1 - Math.abs(l - 0.5) * 2;
        const tint = [-0.01 * sh + 0.018 * hi, 0.004 * sh + 0.006 * hi, 0.018 * sh - 0.016 * hi];
        c = c.map((v, k) => {
          const sat = l + (v - l) * (1 + 0.08 * mid);
          return 0.01 + (1 - 0.01) * (sat + tint[k]);
        });
        data[i++] = Math.round(Math.min(1, Math.max(0, c[0])) * 255);
        data[i++] = Math.round(Math.min(1, Math.max(0, c[1])) * 255);
        data[i++] = Math.round(Math.min(1, Math.max(0, c[2] + 0.004 * (1 - l))) * 255);
        data[i++] = 255;
      }
    }
  }
  const tex = new THREE.Data3DTexture(data, size, size, size);
  tex.format = THREE.RGBAFormat;
  tex.type = THREE.UnsignedByteType;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.wrapS = tex.wrapT = tex.wrapR = THREE.ClampToEdgeWrapping;
  tex.unpackAlignment = 1;
  tex.needsUpdate = true;
  return tex;
}

// ------------------------------------------------------------------ film

const FilmShader = {
  name: 'R3XFilm',
  uniforms: {
    tDiffuse: { value: null as THREE.Texture | null },
    uResolution: { value: new THREE.Vector2(1, 1) },
    uTime: { value: 0 },
    uGrain: { value: 0.028 },
    uVignette: { value: 0.32 },
    uAberration: { value: 0.0022 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform vec2 uResolution;
    uniform float uTime;
    uniform float uGrain;
    uniform float uVignette;
    uniform float uAberration;
    varying vec2 vUv;

    // Interleaved gradient noise (Jimenez 2014): cheap, even, no texture.
    float ign(vec2 p) { return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }

    void main() {
      vec2 c = vUv - 0.5;
      float aspect = uResolution.x / uResolution.y;
      // 0 at the centre, 1 in the corners.
      float d = length(c * vec2(aspect, 1.0)) / length(vec2(aspect, 1.0) * 0.5);

      // Lateral chromatic aberration, zero over the middle of the frame.
      vec2 shift = c * uAberration * smoothstep(0.45, 1.0, d);
      vec3 col = vec3(
        texture2D(tDiffuse, vUv - shift).r,
        texture2D(tDiffuse, vUv).g,
        texture2D(tDiffuse, vUv + shift).b);

      col *= 1.0 - uVignette * smoothstep(0.3, 1.15, d);

      // Grain: strongest in the mid-tones, faint in black and white. Also dithers the
      // 8-bit output, which the dark, foggy booth would otherwise band.
      float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      vec2 p = gl_FragCoord.xy + fract(uTime * 7.31) * vec2(97.0, 57.0);
      float n = ign(p) + ign(p + vec2(17.0, 43.0)) - 1.0;
      col += n * uGrain * (0.3 + 2.8 * l * (1.0 - l));

      gl_FragColor = vec4(col, 1.0);
    }`,
};

// ------------------------------------------------------------------ HDR check

const HotShader = {
  name: 'R3XHot',
  uniforms: { tDiffuse: { value: null as THREE.Texture | null } },
  vertexShader: FilmShader.vertexShader,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    varying vec2 vUv;
    void main() {
      vec3 c = texture2D(tDiffuse, vUv).rgb;
      float m = max(max(c.r, c.g), c.b);
      float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
      vec3 grey = vec3(pow(clamp(l, 0.0, 1.0), 0.6) * 0.5);
      gl_FragColor = vec4(m > 1.0 ? vec3(1.0, 0.0, 1.0) : m > 0.9 ? vec3(0.9, 0.8, 0.0) : grey, 1.0);
    }`,
};

// ------------------------------------------------------------------ AO

/**
 * N8AO darkens the whole beauty image, emitters included. An LED diffuser recessed behind
 * a grille is exactly the kind of crevice AO darkens, but emitted light is not occluded,
 * so fade the AO out where the HDR input is already past the knee (only LEDs get there).
 */
function protectEmitters(pass: N8AOPass) {
  const patch = () => {
    const m = pass.effectCompositerQuad.material as THREE.ShaderMaterial;
    const hook = 'finalAo = mix(finalAo, 1.0, fogFactor);';
    if (!m.fragmentShader.includes(hook) || m.fragmentShader.includes('r3xEmit')) return;
    m.fragmentShader = m.fragmentShader.replace(hook, `${hook}
        float r3xEmit = max(max(sceneTexel.r, sceneTexel.g), sceneTexel.b);
        finalAo = mix(finalAo, 1.0, smoothstep(0.92, 1.3, r3xEmit));`);
    m.needsUpdate = true;
  };
  const configure = pass.configureEffectCompositer.bind(pass);
  pass.configureEffectCompositer = (...args: unknown[]) => {
    configure(...args);
    patch();
  };
  patch();
}

// ------------------------------------------------------------------ dynamic resolution

/**
 * Holds ~60 fps by moving the render scale in 0.125 steps between `min` and `max`.
 * Down when a one-second window averages over 17.8 ms/frame; up after three clean windows
 * (at or under the 60 Hz interval with no long frames). A step up that is undone within
 * two seconds doubles the wait before the next try, so it settles instead of hunting.
 */
class DynamicResolution {
  scale: number;
  private sum = 0;
  private n = 0;
  private worst = 0;
  private clean = 0;
  private upWait = 3;
  private lastUpAt = -1e9;
  private clock = 0;

  constructor(public min: number, public max: number, start: number) {
    this.scale = start;
  }

  /** Back to the cap with a fresh window (a new interaction after a quiet spell). */
  reset() {
    this.scale = this.max;
    this.sum = this.n = this.worst = this.clean = 0;
  }

  /** Feed one frame interval; returns the new scale when it should change. */
  sample(ms: number): number | null {
    if (ms <= 0 || ms > 250) return null; // hidden tab, breakpoint, first frame
    this.clock += ms / 1000;
    this.sum += ms;
    this.n++;
    this.worst = Math.max(this.worst, ms);
    if (this.sum < 1000) return null;
    const avg = this.sum / this.n;
    const worst = this.worst;
    this.sum = this.n = this.worst = 0;
    const step = 0.125;
    if (avg > 17.8 && this.scale > this.min) {
      if (this.clock - this.lastUpAt < 2) this.upWait = Math.min(64, this.upWait * 2);
      this.clean = 0;
      return this.set(this.scale - step * (avg > 24 ? 2 : 1));
    }
    if (avg <= 17.2 && worst < 22) {
      if (++this.clean >= this.upWait && this.scale < this.max) {
        this.clean = 0;
        this.lastUpAt = this.clock;
        return this.set(this.scale + step);
      }
    } else {
      this.clean = 0;
    }
    return null;
  }

  private set(s: number) {
    const next = Math.min(this.max, Math.max(this.min, Math.round(s / 0.125) * 0.125));
    if (next === this.scale) return null;
    this.scale = next;
    return next;
  }
}

// ------------------------------------------------------------------ pipeline

export class PostPipeline {
  readonly composer: EffectComposer;
  readonly ao: N8AOPass;
  readonly bloom: UnrealBloomPass;
  readonly lut: LUTPass;
  readonly film: ShaderPass;
  private readonly renderPass: RenderPass;
  private readonly output = new OutputPass();
  private readonly smaa = new SMAAPass();
  private readonly hot: ShaderPass | null;
  private readonly dyn: DynamicResolution | null;
  /** Build frames drawn with AO in the composer and without it (aoSkipped), for scripts/viewer-check.mjs. */
  readonly buildFrames = { still: 0, move: 0, plain: 0 };
  /** Build's moving-frame AO (BUILD_MOVE_AO), made on the first Build frame that needs it. */
  private aoMove: N8AOPass | null = null;
  /** Still frames accumulated since the last moving or changing frame (aoStep). */
  private refined = 0;
  /** Build's scale while moving: native, stepping down only on a machine too slow for it (SETTLE_MS). */
  private readonly buildDyn: DynamicResolution | null;
  private quality: Quality;
  private lastFrame = -1;
  private toneMap: ToneMap;
  private exposure = 1;
  private bloomOn = true;
  private aoOn = true;
  private readonly bloomCap: { value: number };
  /** AO strength as the Rendering panel set it (Build draws a fraction of it). */
  private aoIntensity = 4;
  /** Build: AO settings to restore on leaving, and the still-frame bookkeeping. */
  private aoSaved: { halfRes: boolean; radius: number; falloff: number; aoSamples: number; denoiseSamples: number } | null = null;
  private drawnSig = NaN;
  private stillAt = -Infinity;
  /** Build frames not drawn because nothing changed (measurement). */
  skipped = 0;
  /** Environment multiplier over whatever the set chose (the booth's desk drives it per cue). */
  envScale = 1;
  /** Frame diagnostics (framediag.ts), while its overlay is open. */
  probe: RenderProbe | null = null;
  /** When to draw (pacer.ts); main.ts asks it every animation frame. */
  readonly pacer: FramePacer;

  constructor(
    private readonly renderer: THREE.WebGLRenderer,
    readonly scene: THREE.Scene,
    readonly camera: THREE.PerspectiveCamera,
  ) {
    this.toneMap = initialToneMap();
    this.applyTone();

    this.quality = initialQuality();
    this.pacer = new FramePacer(QUALITY[this.quality].pace, !STILL && params.get('pace') !== '0');
    const dpr = window.devicePixelRatio || 1;
    const fixed = Number(params.get('dpr'));
    const max = Math.min(dpr, QUALITY[this.quality].scale);
    if (fixed > 0) this.dyn = null;
    else if (STILL) this.dyn = null;
    else this.dyn = new DynamicResolution(Math.min(max, 0.75), max, max);
    const native = Math.min(dpr, NATIVE_MAX);
    this.buildDyn = this.dyn ? new DynamicResolution(Math.min(native, Math.max(1, native * 0.625)), native, native) : null;
    renderer.setPixelRatio(fixed > 0 ? fixed : STILL ? Math.min(dpr, 2) : max);

    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    const target = new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType });
    this.composer = new EffectComposer(renderer, target);

    this.renderPass = new RenderPass(scene, camera);
    this.ao = new N8AOPass(scene, camera, size.x, size.y);
    const c = this.ao.configuration;
    c.gammaCorrection = false; // OutputPass does the sRGB conversion
    c.halfRes = true;
    c.depthAwareUpsampling = true;
    // Sized for a ~1 m droid: seams, rings and the gaps between parts, not whole-body dimming.
    c.aoRadius = 0.12;
    c.distanceFalloff = 1.0;
    c.intensity = this.aoIntensity;
    c.color = new THREE.Color(0, 0, 0);
    this.ao.setQualityMode(QUALITY[this.quality].ao ?? 'Medium');
    // The LED glow sprites are additive and depth-less: skip N8AO's transparency pre-pass
    // (it would re-render them into two more full-size targets every frame).
    this.ao.autoDetectTransparency = false;
    c.transparencyAware = false;
    protectEmitters(this.ao);
    if (params.get('post') === 'ao') this.ao.setDisplayMode('AO');

    // Threshold 1.0: only the LEDs (driven past 1.0) bloom; lit surfaces pass through
    // look.ts's soft knee and stay under it.
    this.bloom = new UnrealBloomPass(new THREE.Vector2(size.x, size.y), 0.6, 0.3, 1.0);
    this.bloomCap = capBloomInput(this.bloom, 2.5);
    // The composer sizes every pass to the render size; bloom (a blur) runs at a fraction.
    const bloomSize = this.bloom.setSize.bind(this.bloom);
    this.bloom.setSize = (w: number, h: number) => {
      const k = QUALITY[this.quality].bloom * 2; // UnrealBloomPass halves it again itself
      bloomSize(Math.max(2, Math.round(w * k)), Math.max(2, Math.round(h * k)));
    };
    this.hot = params.get('post') === 'hot' ? new ShaderPass(HotShader) : null;

    this.lut = new LUTPass({ lut: makeGradeLut(), intensity: 1 });
    const lutUrl = params.get('lut');
    if (lutUrl) {
      new LUTCubeLoader().loadAsync(lutUrl).then((r) => { this.lut.lut = r.texture3D; }, (e) => console.warn('LUT', e));
    }
    this.film = new ShaderPass(FilmShader);
    // Still mode (the regression harness) keeps the vignette and aberration but no grain,
    // so a diff shows real changes rather than noise.
    const grain = params.get('grain');
    if (grain === '0' || (STILL && grain !== '1')) this.film.uniforms.uGrain.value = 0;

    this.build();
    this.bindUi();
  }

  /** The Camera section's quality select and render-scale readout, when present. */
  private bindUi() {
    const sel = document.getElementById('quality') as HTMLSelectElement | null;
    this.scaleOut = document.getElementById('render-scale') as HTMLOutputElement | null;
    if (sel) {
      sel.value = this.quality;
      sel.onchange = () => {
        this.setQuality(parseQuality(sel.value) ?? 'balanced');
        sel.blur();
      };
    }
    this.showScale();
  }

  private scaleOut: HTMLOutputElement | null = null;
  private showScale() {
    if (this.scaleOut) this.scaleOut.textContent = `${this.renderer.getPixelRatio().toFixed(2)}x`;
  }

  get aoEnabled() {
    return QUALITY[this.quality].ao !== null && this.aoOn;
  }

  /** Whether the quality level renders AO at all (Performance does not). */
  get aoAvailable() {
    return QUALITY[this.quality].ao !== null;
  }

  get bloomEnabled() {
    return this.bloomOn;
  }

  /** The WebGL renderer (frame diagnostics read its counters and context). */
  get gl() {
    return this.renderer;
  }

  private applyTone() {
    const t = TONE[this.toneMap];
    this.renderer.toneMapping = t.mapping;
    this.renderer.toneMappingExposure = this.exposure * t.exposureScale;
  }

  /** Tone mapper and exposure (Scene -> Rendering). */
  setTone(map: ToneMap, exposure: number) {
    if (map === this.toneMap && exposure === this.exposure) return;
    this.toneMap = map in TONE ? map : 'neutral';
    this.exposure = exposure;
    this.applyTone();
    this.pacer.touch();
  }

  /** Bloom on/off and its shape; `cap` bounds any one pixel's halo (see capBloomInput). */
  setBloom(on: boolean, strength: number, threshold: number, radius: number, cap = this.bloomCap.value) {
    this.bloom.strength = strength;
    this.bloom.threshold = threshold;
    this.bloom.radius = radius;
    this.bloomCap.value = cap;
    if (on !== this.bloomOn) {
      this.bloomOn = on;
      this.build();
    }
    this.pacer.touch();
  }

  /** AO on/off (within what the quality allows) and its strength. */
  setAO(on: boolean, intensity: number) {
    this.aoIntensity = intensity;
    this.ao.configuration.intensity = intensity * (this.aoSaved ? BUILD_AO.intensity : 1);
    if (on !== this.aoOn) {
      this.aoOn = on;
      this.build();
    }
    this.pacer.touch();
  }

  private filmSaved: { grain: number; vignette: number; aberration: number } | null = null;

  /**
   * Inspection (Build): no bloom, grain, lens fringing or vignette, inspection AO, and the
   * still-frame refinement (SETTLE_MS); off restores all of it.
   */
  setClean(on: boolean) {
    const u = this.film.uniforms;
    const c = this.ao.configuration;
    const was = !!this.filmSaved;
    if (on && !this.filmSaved) {
      this.filmSaved = { grain: u.uGrain.value, vignette: u.uVignette.value, aberration: u.uAberration.value };
      u.uGrain.value = 0;
      u.uAberration.value = 0;
      u.uVignette.value = 0;
      // Half-res AO, upsampled and blurred over a 12 px radius, smeared dark blotches over the
      // small parts, and the ghosts (which write depth) took the AO of the solids behind them.
      this.aoSaved = { halfRes: c.halfRes, radius: c.aoRadius, falloff: c.distanceFalloff, aoSamples: c.aoSamples, denoiseSamples: c.denoiseSamples };
      c.halfRes = false;
      c.accumulate = true;
      c.aoRadius = BUILD_AO.radius;
      c.distanceFalloff = BUILD_AO.falloff;
      c.transparencyAware = true;
      c.intensity = this.aoIntensity * BUILD_AO.intensity;
      this.drawnSig = NaN;
    } else if (!on && this.filmSaved) {
      u.uGrain.value = this.filmSaved.grain;
      u.uVignette.value = this.filmSaved.vignette;
      u.uAberration.value = this.filmSaved.aberration;
      this.filmSaved = null;
      if (this.aoSaved) {
        c.halfRes = this.aoSaved.halfRes;
        c.aoRadius = this.aoSaved.radius;
        c.distanceFalloff = this.aoSaved.falloff;
        c.transparencyAware = false;
        c.accumulate = false;
        c.aoSamples = this.aoSaved.aoSamples;
        c.denoiseSamples = this.aoSaved.denoiseSamples;
        c.intensity = this.aoIntensity;
        this.aoSaved = null;
      }
    }
    if (was !== !!this.filmSaved) this.build(); // the bloom pass in or out
    this.pacer.touch();
  }

  /** Frames drawn since load. */
  get frames() {
    return this.pacer.frames;
  }

  get pixelRatio() {
    return this.renderer.getPixelRatio();
  }

  get currentQuality() {
    return this.quality;
  }

  setQuality(q: Quality) {
    if (q === this.quality) return;
    this.quality = q;
    try { localStorage.setItem(QUALITY_KEY, q); } catch { /* private mode: not remembered */ }
    this.build();
    this.pacer.rates = QUALITY[q].pace;
    this.pacer.touch();
    const sel = document.getElementById('quality') as HTMLSelectElement | null;
    if (sel) sel.value = q;
  }

  private build() {
    const passes = this.composer.passes;
    while (passes.length) this.composer.removePass(passes[passes.length - 1]);
    const preset = QUALITY[this.quality];
    const ao = this.aoOn ? preset.ao : null;
    if (ao) this.ao.setQualityMode(ao);
    // Build accumulates its still AO (BUILD_STILL_AO); the preset's samples come back on leaving
    if (ao && this.aoSaved) {
      this.aoSaved.aoSamples = this.ao.configuration.aoSamples;
      this.aoSaved.denoiseSamples = this.ao.configuration.denoiseSamples;
      Object.assign(this.ao.configuration, BUILD_STILL_AO);
    }
    this.composer.addPass(ao ? this.ao : this.renderPass);
    if (this.hot) this.composer.addPass(this.hot);
    // bloom is the show's (the LEDs): never in a clean inspection image (Build), whatever the levels say
    if (this.bloomOn && !this.filmSaved) this.composer.addPass(this.bloom);
    this.composer.addPass(this.output);
    this.composer.addPass(this.smaa);
    this.composer.addPass(this.lut);
    this.composer.addPass(this.film);
    if (this.dyn) {
      const dpr = window.devicePixelRatio || 1;
      this.dyn.max = Math.min(dpr, preset.scale);
      this.dyn.scale = this.dyn.max; // render() applies it
    }
    // Bloom's targets follow the preset's fraction.
    const buf = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.bloom.setSize(buf.x, buf.y);
  }

  setSize(w: number, h: number) {
    this.renderer.setSize(w, h);
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(w, h);
    this.syncResolution();
  }

  /** Build's moving-frame AO, made once (its own targets, sized with the composer's: syncResolution). */
  private moveAO(): N8AOPass {
    const c = this.ao.configuration;
    if (!this.aoMove) {
      const buf = this.renderer.getDrawingBufferSize(new THREE.Vector2());
      const m = new N8AOPass(this.scene, this.camera, buf.x, buf.y);
      m.configuration.gammaCorrection = false;
      m.configuration.depthAwareUpsampling = true;
      m.autoDetectTransparency = false;
      // transparency-aware like the still pass: the ghosts neither take nor cast AO in either, so the
      // shading on them does not come and go with the hand (it did: the ghosts shaded only while moving)
      m.configuration.transparencyAware = true;
      Object.assign(m.configuration, BUILD_MOVE_AO);
      protectEmitters(m);
      this.aoMove = m;
    }
    const mc = this.aoMove.configuration;
    // the look of the still AO (only a changed value costs anything: N8AO compares)
    mc.aoRadius = c.aoRadius;
    mc.distanceFalloff = c.distanceFalloff;
    mc.intensity = c.intensity;
    mc.color = c.color;
    return this.aoMove;
  }

  private applyScale(s: number) {
    if (s === this.renderer.getPixelRatio()) return;
    this.renderer.setPixelRatio(s);
    this.composer.setPixelRatio(s);
    this.syncResolution();
  }

  private syncResolution() {
    const buf = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.aoMove?.setSize(buf.x, buf.y);
    this.film.uniforms.uResolution.value.copy(buf);
    this.showScale();
  }

  /**
   * A number that changes when anything drawn does: the camera, the buffer, levels touched
   * through the pacer, and every visible mesh's placement, geometry and material (Build's
   * explode, joints, LOD swaps, ghost fades). Matrices as the last render left them; call
   * scene.updateMatrixWorld() first to see pending moves.
   */
  private signature(): number {
    let h = this.pacer.touches * 7.31 + this.envScale * 3.7
      + this.renderer.toneMappingExposure * 5.3 + this.scene.environmentIntensity * 2.9;
    const buf = this.renderer.getSize(new THREE.Vector2());
    h += buf.x * 0.013 + buf.y * 0.017;
    // the camera to 1e-4 (buildframe.ts cameraHash): OrbitControls' damping residue is not a change
    h += cameraHash(this.camera.matrixWorld.elements, 1.1) + cameraHash(this.camera.projectionMatrix.elements, 2.3);
    let n = 0;
    const visit = (o: THREE.Object3D) => {
      if (!o.visible) return;
      const m = o as THREE.Mesh;
      if (m.isMesh || (o as THREE.Line).isLine) {
        n++;
        const e = o.matrixWorld.elements;
        h += (e[12] * 1.3 + e[13] * 1.7 + e[14] * 2.1 + e[0] + e[1] * 0.7 + e[5] * 0.3 + e[9] * 1.9) * (1 + (n % 97) * 0.01);
        h += m.geometry ? m.geometry.id * 1e-3 : 0;
        const mat = m.material as THREE.Material & { color?: THREE.Color };
        if (mat && !Array.isArray(mat)) {
          h += mat.opacity * 0.37 + mat.version * 0.11 + (mat.color ? mat.color.r + mat.color.g * 0.5 + mat.color.b * 0.25 : 0);
        }
      }
      for (const ch of o.children) visit(ch);
    };
    visit(this.scene);
    return h + n;
  }

  /**
   * Build's frame kind (see SETTLE_MS), or null for the usual frame. 'skip' = the last frame
   * drawn is still what the scene looks like.
   */
  private buildFrame(now: number): 'moving' | 'changing' | 'still' | 'skip' | null {
    if (!this.filmSaved || !this.dyn) return null;
    if (!this.pacer.settled(SETTLE_MS, now)) return 'moving';
    this.scene.updateMatrixWorld();
    this.camera.updateMatrixWorld();
    return buildFrameKind({ settled: true, sig: this.signature(), drawnSig: this.drawnSig, stillAt: this.stillAt, now, refreshMs: REFRESH_MS });
  }

  render() {
    const now = performance.now();
    const interacting = this.pacer.fps(now) === Infinity;
    // Resolution adapts only while frames are meant to come at 60 fps (someone interacting);
    // a paced 30 or 15 fps interval is not a slow frame.
    // Build adapts its own scale (native first, see SETTLE_MS); the other modes the quality's cap.
    const dyn = this.filmSaved ? this.buildDyn : this.dyn;
    if (dyn && this.lastFrame >= 0 && interacting) dyn.sample(now - this.lastFrame);
    // A scale lowered in one interaction (often by a model upload's slow frames, not by the view) is
    // forgotten after a few quiet seconds: the next drag or orbit starts sharp and steps down only if its
    // own frames run long (the 1 s window above), instead of popping to a soft view the moment it starts.
    if (dyn && dyn.scale < dyn.max && this.pacer.settled(FORGET_MS, now)) dyn.reset();
    this.lastFrame = interacting ? now : -1;
    const kind = this.buildFrame(now);
    // Build's AO for this frame and the still frame's accumulation (buildframe.ts aoStep): a 'skip'
    // frame is drawn while the still AO is still accumulating, then nothing
    const step = aoStep(!!this.filmSaved && !!this.dyn, kind, this.refined);
    if (!step.draw) {
      this.skipped++;
      return;
    }
    this.refined = step.refined;
    const drawn = kind === 'skip' ? 'still' : kind; // an accumulation frame is a still frame
    if (dyn) {
      // the lowered scale is for the interaction; once it ends, the cap (Build: native, and only a
      // machine too slow for native while moving ever sees a scale change here: buildframe.ts frameScale)
      this.applyScale(frameScale(drawn, interacting, !!this.filmSaved, this.buildDyn ?? dyn, this.dyn ?? dyn));
    }
    if (kind !== 'skip') this.stillAt = kind === 'still' ? now : kind ? -Infinity : this.stillAt;
    // Build shades progressively, as CAD viewers do. Full-resolution N8AO is most of a native frame on a
    // real display (3140x2648, a close view of the head mech: 30 ms with it, 11 ms without, measured in the
    // app with a GPU finish; ~20 vs ~10 ms at 3140x2474 with a readPixels sync here), so a drag with it ran
    // at ~30 fps. Moving and changing frames take the cheap AO pass in AO's slot (half resolution, see
    // BUILD_MOVE_AO); a still frame restarts the full pass's accumulation and the next SETTLE_FRAMES - 1
    // frames add to it. Same targets throughout, nothing reallocated, no frame over budget, no pop.
    const passes = this.composer.passes;
    const aoAt = step.pass ? passes.indexOf(this.ao) : -1;
    if (aoAt >= 0 && step.pass === 'move') passes[aoAt] = this.moveAO();
    if (aoAt >= 0 && step.reset) (this.ao as unknown as { firstFrame(): void }).firstFrame(); // (N8AO: restart the accumulation)
    // (counted for the viewer's regression harness, scripts/viewer-check.mjs)
    if (kind) this.buildFrames[aoAt < 0 ? 'plain' : step.pass === 'move' ? 'move' : 'still']++;
    this.film.uniforms.uTime.value = STILL ? 0 : now / 1000;
    this.probe?.frameStart();
    // a clean inspection image (Build) keeps the room environment near its own level: the Flat
    // preset's 1.8x turned the brushed aluminium white
    const envScale = this.filmSaved ? Math.min(this.envScale, 1.15) : this.envScale;
    if (envScale === 1) {
      this.composer.render();
    } else {
      const e = this.scene.environmentIntensity;
      this.scene.environmentIntensity = e * envScale;
      this.composer.render();
      this.scene.environmentIntensity = e;
    }
    if (aoAt >= 0) passes[aoAt] = this.ao;
    if (kind && kind !== 'skip') this.drawnSig = this.signature();
    this.probe?.frameEnd();
  }
}
