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
  private quality: Quality;
  private lastFrame = -1;
  private toneMap: ToneMap;
  private exposure = 1;
  private bloomOn = true;
  private aoOn = true;
  private readonly bloomCap: { value: number };
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
    c.intensity = 4;
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
    this.ao.configuration.intensity = intensity;
    if (on !== this.aoOn) {
      this.aoOn = on;
      this.build();
    }
    this.pacer.touch();
  }

  private filmSaved: { grain: number; vignette: number; aberration: number } | null = null;

  /** Inspection (Build): no bloom, grain or lens fringing, a light vignette; off restores the film. */
  setClean(on: boolean) {
    const u = this.film.uniforms;
    const was = !!this.filmSaved;
    if (on && !this.filmSaved) {
      this.filmSaved = { grain: u.uGrain.value, vignette: u.uVignette.value, aberration: u.uAberration.value };
      u.uGrain.value = 0;
      u.uAberration.value = 0;
      u.uVignette.value = 0.12;
    } else if (!on && this.filmSaved) {
      u.uGrain.value = this.filmSaved.grain;
      u.uVignette.value = this.filmSaved.vignette;
      u.uAberration.value = this.filmSaved.aberration;
      this.filmSaved = null;
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
      if (this.dyn.scale !== this.dyn.max) this.applyScale(this.dyn.max);
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

  private applyScale(s: number) {
    if (this.dyn) this.dyn.scale = s;
    this.renderer.setPixelRatio(s);
    this.composer.setPixelRatio(s);
    this.syncResolution();
  }

  private syncResolution() {
    const buf = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.film.uniforms.uResolution.value.copy(buf);
    this.showScale();
  }

  render() {
    const now = performance.now();
    // Resolution adapts only while frames are meant to come at 60 fps (someone interacting);
    // a paced 30 or 15 fps interval is not a slow frame.
    if (this.dyn && this.lastFrame >= 0 && this.pacer.fps(now) === Infinity) {
      const s = this.dyn.sample(now - this.lastFrame);
      if (s !== null) this.applyScale(s);
    }
    this.lastFrame = this.pacer.fps(now) === Infinity ? now : -1;
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
    this.probe?.frameEnd();
  }
}
