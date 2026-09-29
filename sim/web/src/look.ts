import * as THREE from 'three';

import palette from './palette.json';

/**
 * The droid's look: painted, used, slightly dirty animatronic.
 *
 * The paint and its weathering are BAKED into the GLB's standard glTF PBR textures by the
 * model build (sim/model/build_r3x.py + sim/web/scripts/pack-model.mjs): per UV atlas
 * (head, body) a baseColor (paint, visor bars, chips to primer and bare metal, crevice
 * grime, rain streaks, scuffs, dust, colour drift), an ORM map (occlusion, roughness,
 * metalness) and a tangent-space normal map baked from the full-resolution STLs, plus
 * per-class clearcoat. GLTFLoader turns those into ordinary MeshStandard/Physical
 * materials, so anything that renders glTF (a path tracer, the WebGPU renderer) sees the
 * same paint with no custom shader.
 *
 * Colours and weathering amounts live in ./palette.json - the one place to edit them; the
 * build reads it (rebuild the model to see a change). At runtime this module only:
 * - tunes texture sampling (anisotropy) - prepareDroidMaterials();
 * - adds the highlight knee that keeps glossy highlights below the bloom threshold, so the
 *   LEDs stay the only bloom sources - tameHighlights();
 * - `?look=albedo|occ|rough|metal|normal` shows one baked channel instead of the shading.
 *
 * leds.ts clones the eye-lens and light-pipe materials and gives them its diffuser shader.
 *
 * The procedural weathering shader below (applyWeathering) is what the droid used before
 * the bake; the bake is a port of it. It stays for procedural surfaces (setupBooth's floor).
 */

export interface Weathering {
  /** Chipped edges: 0 = none, 1 = heavy. */
  wear: number;
  /** What a chip exposes. */
  wearColor: number;
  wearRoughness: number;
  wearMetalness: number;
  /** Rim around each chip (primer / undercoat); null = no rim. */
  primerColor: number | null;
  /** Dirt packed into crevices and running down from them. */
  grime: number;
  grimeColor: number;
  streaks: number;
  /** Dust on up-facing, exposed surfaces. */
  dust: number;
  /** Large-scale value/saturation drift across a part (patchy fade). */
  variation: number;
  fade: number;
  /** Random roughness drift (smudges, polish from handling). */
  roughVar: number;
  /** Fine horizontal scratches. */
  scuffs: number;
}

export interface MaterialClass {
  params: THREE.MeshPhysicalMaterialParameters;
  weather?: Weathering;
  /**
   * Painted bands in the part's own space (the visor's chevrons): band coordinate
   * u = (mirrorX ? |x| : x) * dir[0] + z * dir[1]; a band covers `duty` of each period.
   */
  stripes?: { color: number; periodM: number; duty: number; dir: [number, number]; mirrorX: boolean };
}

interface PaletteClass {
  color: string; roughness: number; metalness: number; clearcoat?: number; clearcoatRoughness?: number;
  weather?: Record<string, number | string | null>;
  stripes?: { color: string; periodM: number; duty: number; dir: number[]; mirrorX: boolean };
}
const hex = (s: string | number | null | undefined) => (typeof s === 'string' ? parseInt(s.slice(1), 16) : 0);

/** Per-class paint, from palette.json (flat params; the weathering is baked). */
export const CLASSES: Record<string, MaterialClass> = Object.fromEntries(
  Object.entries(palette.classes as Record<string, PaletteClass>).map(([name, c]) => {
    const w = c.weather;
    return [name, {
      params: {
        color: hex(c.color), roughness: c.roughness, metalness: c.metalness,
        clearcoat: c.clearcoat ?? 0, clearcoatRoughness: c.clearcoatRoughness ?? 0,
      },
      weather: w && {
        wear: w.wear as number, wearColor: hex(w.wearColor), wearRoughness: w.wearRoughness as number,
        wearMetalness: w.wearMetalness as number, primerColor: w.primerColor ? hex(w.primerColor) : null,
        grime: w.grime as number, grimeColor: hex(w.grimeColor), streaks: w.streaks as number, dust: w.dust as number,
        variation: w.variation as number, fade: w.fade as number, roughVar: w.roughVar as number, scuffs: w.scuffs as number,
      },
      stripes: c.stripes && { ...c.stripes, color: hex(c.stripes.color), dir: [c.stripes.dir[0], c.stripes.dir[1]] },
    } satisfies MaterialClass];
  }),
);
const DUST = hex(palette.dust);

// ------------------------------------------------------------------ procedural masks

/** Optional baked masks for applyWeathering (occlusion + exposed edges on a UV atlas). */
export interface WeatherMaps {
  occlusion: THREE.Texture;
  edges: THREE.Texture;
}

// ------------------------------------------------------------------ shader

const NOISE_GLSL = /* glsl */ `
float wHash(vec3 p) {
  p = fract(p * 0.3183099 + vec3(0.71, 0.113, 0.419));
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float wNoise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(wHash(i), wHash(i + vec3(1, 0, 0)), f.x), mix(wHash(i + vec3(0, 1, 0)), wHash(i + vec3(1, 1, 0)), f.x), f.y),
    mix(mix(wHash(i + vec3(0, 0, 1)), wHash(i + vec3(1, 0, 1)), f.x), mix(wHash(i + vec3(0, 1, 1)), wHash(i + vec3(1, 1, 1)), f.x), f.y),
    f.z);
}
float wFbm(vec3 p) {
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { s += a * wNoise(p); p = p * 2.03 + vec3(1.7, 9.2, 3.1); a *= 0.5; }
  return s / 0.9375;
}
`;

const KNEE_GLSL = /* glsl */ `
  // Soft knee below 1.0: a glossy highlight can never reach the bloom threshold, so only
  // the LEDs (and their diffusers) glow, however the lights are set.
  vec3 wK = max(gl_FragColor.rgb - 0.7, 0.0);
  gl_FragColor.rgb -= wK - wK / (1.0 + wK * 3.4);`;

interface WeatherUniforms {
  [k: string]: THREE.IUniform;
}

/**
 * `?weather=occ|edge|wear|grime` shows one procedural weathering mask instead of the
 * shaded colour (for tuning applyWeathering's thresholds).
 */
const DEBUG_VIEW = { occ: 1, edge: 2, wear: 3, grime: 4 }[new URLSearchParams(location.search).get('weather') ?? ''] ?? 0;

function weatherUniforms(w: Weathering, maps: WeatherMaps | null): WeatherUniforms {
  const c = (hex: number) => new THREE.Color(hex);
  return {
    uEdgeMap: { value: maps?.edges ?? null },
    uWear: { value: w.wear },
    uWearColor: { value: c(w.wearColor) },
    uWearRough: { value: w.wearRoughness },
    uWearMetal: { value: w.wearMetalness },
    uPrimerColor: { value: c(w.primerColor ?? w.wearColor) },
    uPrimer: { value: w.primerColor === null ? 0 : 1 },
    uGrime: { value: w.grime },
    uGrimeColor: { value: c(w.grimeColor) },
    uStreaks: { value: w.streaks },
    uDust: { value: w.dust },
    uDustColor: { value: c(DUST) },
    uVariation: { value: w.variation },
    uFade: { value: w.fade },
    uRoughVar: { value: w.roughVar },
    uScuffs: { value: w.scuffs },
  };
}

/** Inject the weathering layer into a MeshPhysicalMaterial. */
export function applyWeathering(material: THREE.MeshPhysicalMaterial, w: Weathering, maps: WeatherMaps | null,
  stripes?: MaterialClass['stripes']) {
  const u = weatherUniforms(w, maps);
  u.uStripeColor = { value: new THREE.Color(stripes?.color ?? 0) };
  u.uStripePeriod = { value: stripes?.periodM ?? 1 };
  u.uStripeDuty = { value: stripes?.duty ?? 0.5 };
  u.uStripeDir = { value: new THREE.Vector2(...(stripes?.dir ?? [1, 0])) };
  const hasMaps = maps ? 1 : 0;
  const stripeKey = stripes ? (stripes.mirrorX ? 'chevron' : 'bands') : '';
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWPos;\nvarying vec3 vWNrm;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvWPos = transformed;\nvWNrm = objectNormal;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
varying vec3 vWPos;
varying vec3 vWNrm;
uniform sampler2D uEdgeMap;
uniform float uWear, uWearRough, uWearMetal, uPrimer, uGrime, uStreaks, uDust, uVariation, uFade, uRoughVar, uScuffs;
uniform vec3 uWearColor, uPrimerColor, uGrimeColor, uDustColor, uStripeColor;
uniform float uStripePeriod, uStripeDuty;
uniform vec2 uStripeDir;
${NOISE_GLSL}`)
      .replace('#include <color_fragment>', `#include <color_fragment>
  // ---- weathering masks
  vec3 wP = vWPos;
  vec3 wN = normalize(vWNrm);
${stripes ? `  // Painted bands (anti-aliased), under the weathering so grime and chips cross them.
  float wU = (${stripes.mirrorX ? 'abs(wP.x)' : 'wP.x'} * uStripeDir.x + wP.z * uStripeDir.y) / uStripePeriod;
  float wS = fract(wU);
  float wSw = fwidth(wU) * 1.5;
  float wBand = smoothstep(0.0, wSw, wS) * (1.0 - smoothstep(uStripeDuty - wSw, uStripeDuty, wS));
  diffuseColor.rgb = mix(diffuseColor.rgb, uStripeColor, wBand);` : ''}
  float wOcc = 1.0;
  float wEdge = 0.0;
#if defined( USE_AOMAP ) && ${hasMaps}
  wOcc = texture2D(aoMap, vAoMapUv).r;
  wEdge = texture2D(uEdgeMap, vAoMapUv).r;
#endif
  float nBig = wFbm(wP * 5.0);
  float nMid = wFbm(wP * 26.0 + 3.1);
  float nFine = wFbm(wP * 190.0 + 7.3);
  float exposed = smoothstep(0.55, 0.9, wOcc);

  // Colour drift and patchy fading (sun-faded, touched-up, uneven coats).
  diffuseColor.rgb *= 1.0 + (nBig - 0.5) * 2.0 * uVariation;
  float wLum = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(wLum) * 1.08, uFade * smoothstep(0.35, 0.8, nBig) * exposed);

  // Grime: packed into crevices, a little everywhere, and running down from seams.
  float crevice = 1.0 - smoothstep(0.45, 0.97, wOcc);
  float wGrime = crevice * (0.55 + 0.9 * nMid);
  float side = 1.0 - abs(wN.y);
  float streak = wFbm(vec3(wP.x * 70.0, wP.y * 5.0, wP.z * 70.0));
  wGrime += smoothstep(0.52, 0.85, streak) * uStreaks * side * (0.4 + nBig);
  wGrime += 0.16 * smoothstep(0.4, 0.9, nMid * 0.6 + nBig * 0.4) * (1.0 - exposed * 0.4);
  wGrime = clamp(wGrime * uGrime, 0.0, 0.85);
  diffuseColor.rgb = mix(diffuseColor.rgb, uGrimeColor, wGrime);

  // Chipped paint on exposed edges: primer rim, then bare metal.
  // Edge wear clusters where parts get handled (nMid) and breaks up into chips (nFine).
  float chipV = pow(wEdge, 0.7) * exposed * (0.75 + 0.7 * nMid) + (nFine - 0.5) * 0.8;
  float wT = 1.0 - uWear * 0.6;
  float wMetal = smoothstep(wT, wT + 0.02, chipV) * step(0.001, uWear);
  float wPrimer = smoothstep(wT - 0.09, wT - 0.07, chipV) * uPrimer * step(0.001, uWear);
  // Fine horizontal scratches through the top coat.
  float scr = wNoise(vec3(wP.x * 18.0, wP.y * 1400.0, wP.z * 18.0));
  float wScuff = smoothstep(0.93, 0.985, 1.0 - abs(scr - 0.5) * 2.0)
    * smoothstep(0.55, 0.75, wFbm(wP * 9.0 + 11.0)) * uScuffs * exposed;
  wPrimer = max(wPrimer, wScuff * 0.8);
  diffuseColor.rgb = mix(diffuseColor.rgb, uPrimerColor, wPrimer * (1.0 - wMetal));
  diffuseColor.rgb = mix(diffuseColor.rgb, uWearColor * (0.8 + 0.4 * nFine), wMetal);

  // Dust film on up-facing, exposed surfaces.
  float wDust = smoothstep(0.35, 0.95, wN.y) * exposed * uDust * (0.35 + 0.65 * smoothstep(0.3, 0.8, nMid));
  wDust += crevice * uDust * 0.25 * smoothstep(0.0, 0.6, wN.y);
  wDust = clamp(wDust, 0.0, 0.6);
  diffuseColor.rgb = mix(diffuseColor.rgb, uDustColor, wDust);
#if ${DEBUG_VIEW} > 0
  float wDbg = ${DEBUG_VIEW} == 1 ? wOcc : ${DEBUG_VIEW} == 2 ? wEdge : ${DEBUG_VIEW} == 3 ? max(wMetal, wPrimer * 0.5) : wGrime;
#endif`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
  roughnessFactor = clamp(roughnessFactor * (1.0 + (nMid - 0.5) * 2.0 * uRoughVar), 0.05, 1.0);
  roughnessFactor = mix(roughnessFactor, 0.85, wGrime);
  roughnessFactor = mix(roughnessFactor, 0.7, wPrimer * (1.0 - wMetal));
  roughnessFactor = mix(roughnessFactor, uWearRough + nFine * 0.15, wMetal);
  roughnessFactor = mix(roughnessFactor, 0.95, wDust);`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>
  metalnessFactor = mix(metalnessFactor, 0.0, max(wGrime * 0.8, wDust));
  metalnessFactor = mix(metalnessFactor, 0.0, wPrimer * (1.0 - wMetal));
  metalnessFactor = mix(metalnessFactor, uWearMetal, wMetal);`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
  // Relief from the paint layers: chips are recessed by the paint thickness (a crisp
  // step that catches the light), plus faint large-scale waviness. Screen-space bump.
  float wH = (1.0 - wMetal) * 0.00035 + wPrimer * (1.0 - wMetal) * 0.00008 + nMid * 0.0005;
  vec3 wDpx = dFdx(-vViewPosition);
  vec3 wDpy = dFdy(-vViewPosition);
  vec3 wR1 = cross(wDpy, normal);
  vec3 wR2 = cross(normal, wDpx);
  float wDet = dot(wDpx, wR1) * faceDirection;
  vec3 wGrad = sign(wDet) * (dFdx(wH) * wR1 + dFdy(wH) * wR2);
  normal = normalize(abs(wDet) * normal - wGrad);`)
      .replace('#include <lights_physical_fragment>', `#include <lights_physical_fragment>
#ifdef USE_CLEARCOAT
  material.clearcoat *= clamp(1.0 - wMetal - wPrimer - wGrime - wDust * 1.5, 0.0, 1.0);
#endif`)
      .replace('#include <opaque_fragment>', `#include <opaque_fragment>${KNEE_GLSL}
#if ${DEBUG_VIEW} > 0
  gl_FragColor = vec4(vec3(wDbg), 1.0);
#endif`);
  };
  material.customProgramCacheKey = () => `r3x-weather-${hasMaps}-${stripeKey}`;
}


/**
 * Apply the highlight knee to lit materials (the droid's baked materials, rig.ts's piston
 * rod, ...), skipping anything with its own shader patch (leds.ts diffusers must keep blooming)
 * and unlit materials (LED sprites, chest lights).
 */
export function tameHighlights(root: THREE.Object3D) {
  root.traverse((o) => {
    const m = (o as THREE.Mesh).material as THREE.Material | undefined;
    if (!m || !(m instanceof THREE.MeshStandardMaterial)) return;
    if (Object.prototype.hasOwnProperty.call(m, 'onBeforeCompile')) return;
    m.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', `#include <opaque_fragment>${KNEE_GLSL}`);
    };
    m.customProgramCacheKey = () => 'r3x-knee';
    m.needsUpdate = true;
  });
}

/**
 * `?look=albedo|occ|rough|metal|normal` shows one baked texture channel of the droid
 * instead of its shading (for checking the bake).
 */
const BAKE_VIEW = ({ albedo: 1, occ: 2, rough: 3, metal: 4, normal: 5 } as Record<string, number>)[
  new URLSearchParams(location.search).get('look') ?? ''] ?? 0;

/**
 * The droid's materials as GLTFLoader made them from the baked textures: sharpen texture
 * sampling at grazing angles, and wire up the ?look= debug views. Run before FaceLeds
 * (it clones the lens materials) and before tameHighlights.
 */
export function prepareDroidMaterials(root: THREE.Object3D, renderer: THREE.WebGLRenderer) {
  const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const seen = new Set<THREE.Material>();
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const m = mesh.material as THREE.MeshPhysicalMaterial;
    if (seen.has(m) || !(m instanceof THREE.MeshStandardMaterial)) return;
    seen.add(m);
    for (const t of [m.map, m.normalMap, m.roughnessMap, m.metalnessMap, m.aoMap,
      (m as THREE.MeshPhysicalMaterial).clearcoatMap, (m as THREE.MeshPhysicalMaterial).clearcoatRoughnessMap]) {
      if (t) t.anisotropy = aniso;
    }
    if (BAKE_VIEW && m.map) {
      m.onBeforeCompile = (shader) => {
        shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>
  ${[
    '',
    'gl_FragColor = vec4(texture2D(map, vMapUv).rgb, 1.0);',
    'gl_FragColor = vec4(vec3(texture2D(aoMap, vAoMapUv).r), 1.0);',
    'gl_FragColor = vec4(vec3(texture2D(roughnessMap, vRoughnessMapUv).g), 1.0);',
    'gl_FragColor = vec4(vec3(texture2D(metalnessMap, vMetalnessMapUv).b), 1.0);',
    'gl_FragColor = vec4(texture2D(normalMap, vNormalMapUv).rgb, 1.0);',
  ][BAKE_VIEW]}`);
      };
      m.customProgramCacheKey = () => `r3x-bakeview-${BAKE_VIEW}`;
      m.needsUpdate = true;
    }
  });
}

// The booth set, its lights and environment: booth.ts.
