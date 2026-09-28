import * as THREE from 'three';

/**
 * The droid's look: painted, used, slightly dirty animatronic in a dim cantina booth.
 *
 * The GLB only carries material *class names*. Each class becomes a MeshPhysicalMaterial
 * with a weathering layer injected by onBeforeCompile:
 *
 * - Baked masks (sim/model/build_r3x.py, shared UV atlas): `r3x_occlusion.jpg` (ambient
 *   occlusion, broad x crevice) and `r3x_edges.jpg` (exposed-edge mask from Cycles' Bevel
 *   node). Occlusion is also the material's aoMap, so it darkens ambient/env light.
 * - Procedural 3D value noise in the mesh's *own* space, so wear stays glued to a part
 *   when its joint moves. No downloaded textures.
 *
 * Layers, bottom to top: base colour variation / fading, crevice grime and rain-down
 * streaks, chipped paint (a primer rim around bare metal) on exposed edges, fine scuffs,
 * and a dust film on up-facing surfaces. Each also moves roughness/metalness/clearcoat.
 *
 * leds.ts clones the eye-lens and light-pipe materials and replaces onBeforeCompile with
 * its diffuser shader; those two classes are deliberately left unweathered here.
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
}

const DUST = 0x8a7f70;
const GRIME = 0x1f1810;

// Oga's Cantina R-3X (ex Star Tours RX-24 pilot, repainted): semi-gloss burnt orange torso
// and base, grey head and arms, dark gunmetal hardware, blue headphone cups and RX-24
// plate. Chips show silver metal under the paint; dirt sits in the seams.
export const CLASSES: Record<string, MaterialClass> = {
  paint_orange: {
    params: { color: 0xa9552b, roughness: 0.5, metalness: 0.0, clearcoat: 0.3, clearcoatRoughness: 0.45 },
    weather: {
      wear: 0.65, wearColor: 0xa9aaa6, wearRoughness: 0.38, wearMetalness: 0.85, primerColor: 0x6b675f,
      grime: 0.75, grimeColor: GRIME, streaks: 0.35, dust: 0.35, variation: 0.18, fade: 0.25,
      roughVar: 0.35, scuffs: 0.45,
    },
  },
  metal_grey: {
    // Grey paint with a metallic flake: reads as metal but not chrome.
    params: { color: 0x8e959d, roughness: 0.46, metalness: 0.22, clearcoat: 0.15, clearcoatRoughness: 0.5 },
    weather: {
      wear: 0.45, wearColor: 0xc4c6c8, wearRoughness: 0.3, wearMetalness: 0.95, primerColor: 0x55575a,
      grime: 0.8, grimeColor: GRIME, streaks: 0.25, dust: 0.3, variation: 0.1, fade: 0.1,
      roughVar: 0.45, scuffs: 0.3,
    },
  },
  metal_dark: {
    params: { color: 0x2e3034, roughness: 0.55, metalness: 0.55 },
    weather: {
      wear: 0.4, wearColor: 0x8e9194, wearRoughness: 0.32, wearMetalness: 0.9, primerColor: null,
      grime: 0.6, grimeColor: 0x15110c, streaks: 0.15, dust: 0.3, variation: 0.1, fade: 0.0,
      roughVar: 0.5, scuffs: 0.25,
    },
  },
  rubber: {
    params: { color: 0x121213, roughness: 0.9, metalness: 0 },
    weather: {
      wear: 0.0, wearColor: 0x2a2a2a, wearRoughness: 0.9, wearMetalness: 0, primerColor: null,
      grime: 0.3, grimeColor: 0x0a0806, streaks: 0.0, dust: 0.6, variation: 0.12, fade: 0.25,
      roughVar: 0.1, scuffs: 0.0,
    },
  },
  accent_blue: {
    params: { color: 0x2c5c96, roughness: 0.45, metalness: 0.1, clearcoat: 0.35, clearcoatRoughness: 0.4 },
    weather: {
      wear: 0.35, wearColor: 0xa9aaa6, wearRoughness: 0.35, wearMetalness: 0.85, primerColor: 0x5d5c58,
      grime: 0.7, grimeColor: GRIME, streaks: 0.2, dust: 0.3, variation: 0.12, fade: 0.2,
      roughVar: 0.35, scuffs: 0.35,
    },
  },
  // H_*Eye_4 'diffusion bulbs': frosted, lit from behind by the WS2812 jewel (leds.ts).
  eye_lens: { params: { color: 0x3a4048, roughness: 0.35, metalness: 0, clearcoat: 0.6 } },
  // Mic-Mouth-Split light pipe: translucent print, lit by the mouth V behind it (leds.ts).
  light_pipe: { params: { color: 0x2a2622, roughness: 0.55, metalness: 0 } },
};

// ------------------------------------------------------------------ baked masks

export interface WeatherMaps {
  occlusion: THREE.Texture;
  edges: THREE.Texture;
}

/** The build's baked masks, or null when the model was built with --no-bake. */
export async function loadWeatherMaps(renderer: THREE.WebGLRenderer): Promise<WeatherMaps | null> {
  const loader = new THREE.TextureLoader();
  const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const load = async (url: string) => {
    const t = await loader.loadAsync(url);
    t.flipY = false; // glTF UV convention
    t.colorSpace = THREE.NoColorSpace;
    t.anisotropy = aniso;
    t.needsUpdate = true;
    return t;
  };
  try {
    const [occlusion, edges] = await Promise.all([load('/model/r3x_occlusion.jpg'), load('/model/r3x_edges.jpg')]);
    return { occlusion, edges };
  } catch {
    console.info('[r3x] no baked weathering maps; procedural wear only (rebuild the model to bake them)');
    return null;
  }
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
 * `?look=occ|edge|wear|grime` shows one weathering mask instead of the shaded colour
 * (for tuning the bake and the thresholds).
 */
const DEBUG_VIEW = { occ: 1, edge: 2, wear: 3, grime: 4 }[new URLSearchParams(location.search).get('look') ?? ''] ?? 0;

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
export function applyWeathering(material: THREE.MeshPhysicalMaterial, w: Weathering, maps: WeatherMaps | null) {
  const u = weatherUniforms(w, maps);
  const hasMaps = maps ? 1 : 0;
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
uniform vec3 uWearColor, uPrimerColor, uGrimeColor, uDustColor;
${NOISE_GLSL}`)
      .replace('#include <color_fragment>', `#include <color_fragment>
  // ---- weathering masks
  vec3 wP = vWPos;
  vec3 wN = normalize(vWNrm);
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
  material.customProgramCacheKey = () => `r3x-weather-${hasMaps}`;
}


/**
 * Apply the highlight knee to lit materials made elsewhere (e.g. rig.ts's piston rod),
 * skipping anything with its own shader patch (leds.ts diffusers must keep blooming)
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

/** One material per class, shared by every mesh of that class. */
export function makeMaterial(name: string, maps: WeatherMaps | null): THREE.MeshPhysicalMaterial {
  const cls = CLASSES[name] ?? CLASSES.metal_grey;
  const material = new THREE.MeshPhysicalMaterial({ name, ...cls.params });
  if (cls.weather) {
    if (maps) {
      material.aoMap = maps.occlusion;
      material.aoMapIntensity = 1.0;
    }
    applyWeathering(material, cls.weather, maps);
  }
  return material;
}

// ------------------------------------------------------------------ environment

/**
 * A procedural cantina for image-based light: a dark, warm room with a few practicals
 * (amber pendants overhead, a teal wall strip, a red-magenta neon behind the booth).
 * It is what metal and clearcoat reflect, so the droid picks up coloured cantina
 * reflections instead of a white studio. Built once into a PMREM; no downloads.
 */
export function cantinaEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
  const env = new THREE.Scene();
  const room = new THREE.Mesh(
    new THREE.BoxGeometry(14, 6, 14),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0.028, 0.02, 0.015), side: THREE.BackSide }),
  );
  room.position.y = 2.5;
  env.add(room);
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(14, 14),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0.012, 0.01, 0.008) }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -0.49;
  env.add(floor);
  const panel = (w: number, h: number, rgb: [number, number, number], pos: [number, number, number], rotY = 0, rotX = 0) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(...rgb), side: THREE.DoubleSide }));
    m.position.set(...pos);
    m.rotation.set(rotX, rotY, 0);
    env.add(m);
  };
  // Warm pendant pools overhead (front-right of the droid, where the key comes from).
  panel(1.6, 1.0, [4.0, 2.7, 1.6], [1.8, 4.5, 2.2], 0, Math.PI / 2);
  panel(0.8, 0.8, [2.4, 1.6, 0.9], [-2.2, 4.5, 1.0], 0, Math.PI / 2);
  // Teal strip on the left wall.
  panel(0.25, 3.5, [0.15, 1.3, 1.6], [-6.9, 2.0, -1.0], Math.PI / 2);
  // Red-magenta neon behind the booth.
  panel(3.0, 0.2, [2.2, 0.25, 0.7], [1.0, 2.6, -6.9]);
  // Dim warm bar glow in front, low (what the droid "sees").
  panel(6.0, 0.6, [0.5, 0.28, 0.12], [0, 0.6, 6.9]);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const tex = pmrem.fromScene(env, 0.03).texture;
  pmrem.dispose();
  env.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.isMesh) {
      m.geometry.dispose();
      (m.material as THREE.Material).dispose();
    }
  });
  return tex;
}

// ------------------------------------------------------------------ the booth

/**
 * Dim cantina DJ booth: a warm tungsten spot as key (a pool of light on a dark floor),
 * cool teal and red-magenta rims from the room's practicals, very little fill, and a
 * matching coloured environment for reflections. Values keep every lit surface below
 * 1.0 linear, so only the LEDs cross the bloom threshold.
 */
export function setupBooth(renderer: THREE.WebGLRenderer, scene: THREE.Scene) {
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1.0;

  const bg = new THREE.Color(0x07080a);
  scene.background = bg;
  scene.fog = new THREE.Fog(bg, 3.2, 7.5);
  scene.environment = cantinaEnvironment(renderer);
  scene.environmentIntensity = 0.55;

  const key = new THREE.SpotLight(0xffdcbc, 34, 9, 0.42, 0.75, 1.6);
  key.position.set(1.3, 3.1, 2.1);
  key.target.position.set(0, 0.5, 0);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.near = 1.5;
  key.shadow.camera.far = 6;
  key.shadow.bias = -0.0002;
  key.shadow.normalBias = 0.004;
  key.shadow.radius = 3;
  scene.add(key, key.target);

  // Teal from the wall strip, behind-left; red-magenta neon behind-right. Narrow spots
  // aimed at the droid, so they outline it without washing the floor.
  const rim = (color: number, intensity: number, pos: [number, number, number]) => {
    const l = new THREE.SpotLight(color, intensity, 7, 0.3, 0.9, 1.6);
    l.position.set(...pos);
    l.target.position.set(0, 0.6, 0);
    scene.add(l, l.target);
  };
  rim(0x5ab0ff, 12, [-2.2, 1.9, -1.9]);
  rim(0xff4f78, 8, [2.0, 1.5, -2.3]);
  // Cool room fill from the front-left, so the grey paint still reads grey under tungsten.
  const fill = new THREE.DirectionalLight(0x9fb8d8, 0.5);
  fill.position.set(-2, 1.2, 2.2);
  scene.add(fill);
  // Low bounce from the booth console in front, and a whisper of room fill.
  const console_ = new THREE.PointLight(0xff9a50, 0.5, 2.5, 2);
  console_.position.set(0.0, 0.25, 0.9);
  scene.add(console_);
  scene.add(new THREE.HemisphereLight(0x3a4658, 0x140d08, 0.25));

  const floorMat = new THREE.MeshPhysicalMaterial({ color: 0x100e0c, roughness: 0.9, metalness: 0.0 });
  applyWeathering(floorMat, {
    wear: 0, wearColor: 0, wearRoughness: 0.8, wearMetalness: 0, primerColor: null,
    grime: 0.5, grimeColor: 0x080604, streaks: 0, dust: 0, variation: 0.12, fade: 0,
    roughVar: 0.15, scuffs: 0,
  }, null);
  const floor = new THREE.Mesh(new THREE.CircleGeometry(4, 96), floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  return { key };
}
