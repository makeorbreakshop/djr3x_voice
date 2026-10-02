/**
 * Weathering at render time (Build's Exterior look and the assembly video): palette.json's per-class `weather`
 * spec - the one the Original's Blender bake reads (sim/model/build_r3x.py) - drawn by a shader extension on
 * the part's own material, so parts the GLB does not have (Hunter's head, the kit's STLs) wear the same park
 * look. Everything is in the part's object space (mm), so the wear rides with a part as it moves:
 *
 * - paint variation and fade: 3D fbm noise over the colour, a slow lightening;
 * - edge wear: chips at sharp convex edges (curvature from the normal's screen derivatives, broken up by noise),
 *   primer first, then bare metal (`wearColor`, metallic and smoother);
 * - grime in seams and on undersides, vertical streaks running down sides, dust on up-facing surfaces;
 * - roughness variation and fine scuffs;
 * - printed internals (unpainted): faint layer lines, matte.
 *
 * One shader program for every part (it extends the rim overlay in workbench.ts): `amount` 0 turns it off.
 */

import * as THREE from 'three';
import palette from '../palette.json';

export interface WeatherSpec {
  wear: number; wearColor: string; wearRoughness: number; wearMetalness: number; primerColor: string;
  grime: number; grimeColor: string; streaks: number; dust: number; variation: number; fade: number; roughVar: number; scuffs: number;
}
type Cls = { weather?: WeatherSpec; stripes?: { color: string; periodM: number; duty: number; dir: [number, number] } };
const CLASSES = palette.classes as unknown as Record<string, Cls>;
const DUST = new THREE.Color(palette.dust as string);
let edgeK = 1;

/** The uniforms one material carries. */
export interface WeatherUniforms {
  uWOn: { value: THREE.Vector4 };       // amount, printed, stripe duty, stripe period (mm)
  uW1: { value: THREE.Vector4 };        // wear, grime, streaks, dust
  uW2: { value: THREE.Vector4 };        // variation, fade, roughVar, scuffs
  uW3: { value: THREE.Vector4 };        // wearRoughness, wearMetalness, stripe dir x, stripe dir y
  uWearC: { value: THREE.Color }; uPrimerC: { value: THREE.Color }; uGrimeC: { value: THREE.Color };
  uDustC: { value: THREE.Color }; uStripeC: { value: THREE.Color };
  /** Paint regions (finish `regions`): the part's axis (xyz) and radius (w), its centre; two regions'
   *  [rMin, rMax, facing sign, on] and colours. */
  uRAx: { value: THREE.Vector4 }; uRC: { value: THREE.Vector3 };
  uR0: { value: THREE.Vector4 }; uR0C: { value: THREE.Color }; uR1: { value: THREE.Vector4 }; uR1C: { value: THREE.Color };
  /** How much an edge counts (a coarse or finely ribbed mesh less: its creases are not wear edges). */
  uEdgeK: { value: number };
}

export function weatherUniforms(): WeatherUniforms {
  return {
    uWOn: { value: new THREE.Vector4() }, uW1: { value: new THREE.Vector4() }, uW2: { value: new THREE.Vector4() },
    uW3: { value: new THREE.Vector4() }, uWearC: { value: new THREE.Color() }, uPrimerC: { value: new THREE.Color() },
    uGrimeC: { value: new THREE.Color() }, uDustC: { value: DUST.clone() }, uStripeC: { value: new THREE.Color() },
    uRAx: { value: new THREE.Vector4(0, 1, 0, 1) }, uRC: { value: new THREE.Vector3() },
    uR0: { value: new THREE.Vector4() }, uR0C: { value: new THREE.Color() }, uR1: { value: new THREE.Vector4() }, uR1C: { value: new THREE.Color() },
    uEdgeK: { value: 1 },
  };
}

/** Set a material's weathering from its paint class (`amount` scales it all; 0 off); `printed` for a bare print. */
export function setWeather(u: WeatherUniforms, paint: string | undefined, amount: number, printed = false, edgeScale = 1) {
  u.uRAx.value.w = u.uRAx.value.w || 1;
  u.uWOn.value.w = 1;
  edgeK = edgeScale;
  u.uEdgeK.value = edgeScale;
  const c = paint ? CLASSES[paint] : undefined;
  const w = c?.weather;
  if (!w || amount <= 0) {
    u.uWOn.value.set(printed && amount > 0 ? amount : 0, printed ? 1 : 0, 0, 1);
    u.uW1.value.set(0, 0, 0, 0);
    u.uW2.value.set(0.04, 0, 0.25, 0);
    return;
  }
  const st = c?.stripes;
  u.uWOn.value.set(amount, 0, st ? st.duty : 0, st ? st.periodM * 1000 : 1);
  // (a mesh decimated far below its source is creased all over: its edge wear is scaled down, `edgeScale`)
  u.uW1.value.set(w.wear, w.grime, w.streaks, w.dust);
  u.uW2.value.set(w.variation, w.fade, w.roughVar, w.scuffs * edgeK);
  u.uW3.value.set(w.wearRoughness, w.wearMetalness, st ? st.dir[0] : 1, st ? st.dir[1] : 0);
  u.uWearC.value.set(w.wearColor);
  u.uPrimerC.value.set(w.primerColor);
  u.uGrimeC.value.set(w.grimeColor);
  if (st) u.uStripeC.value.set(st.color);
}

export interface PaintRegion { paint: string; facing?: 'out' | 'in'; r: [number, number] }

/**
 * A part painted by region (finish `regions`, finish.json `paint_regions`): its own axis - the least-variance
 * direction of its vertices, pointing away from the droid's vertical axis (`toAxis`: from the part's centre
 * toward that axis, object space) - its radius about it, and up to two regions' colours. The same rule as
 * sim/model/build_r3x.py split_paint_regions().
 */
export function setRegions(u: WeatherUniforms, geo: THREE.BufferGeometry | null, regions: PaintRegion[] | undefined, toAxis: THREE.Vector3,
  colorOf: (paint: string) => number | null) {
  u.uR0.value.w = 0;
  u.uR1.value.w = 0;
  if (!geo || !regions?.length) return;
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const c = new THREE.Vector3();
  const v = new THREE.Vector3();
  const n = pos.count;
  for (let i = 0; i < n; i++) c.add(v.fromBufferAttribute(pos, i));
  c.divideScalar(Math.max(1, n));
  // covariance and its least-variance eigenvector (power iteration on the inverse is overkill: a few Jacobi sweeps)
  const m = [0, 0, 0, 0, 0, 0, 0, 0, 0];
  for (let i = 0; i < n; i++) {
    v.fromBufferAttribute(pos, i).sub(c);
    const a = [v.x, v.y, v.z];
    for (let r = 0; r < 3; r++) for (let k = 0; k < 3; k++) m[r * 3 + k] += a[r] * a[k];
  }
  const ax = smallestEigen(m);
  if (ax.dot(toAxis) > 0) ax.negate(); // away from the droid's axis
  let R = 0;
  for (let i = 0; i < n; i++) {
    v.fromBufferAttribute(pos, i).sub(c);
    R = Math.max(R, v.clone().addScaledVector(ax, -v.dot(ax)).length());
  }
  u.uRAx.value.set(ax.x, ax.y, ax.z, R || 1);
  u.uRC.value.copy(c);
  regions.slice(0, 2).forEach((r, k) => {
    const col = colorOf(r.paint);
    if (col === null) return;
    (k ? u.uR1 : u.uR0).value.set(r.r[0], r.r[1], r.facing === 'in' ? -1 : 1, 1);
    (k ? u.uR1C : u.uR0C).value.setHex(col);
  });
}

function smallestEigen(m: number[]): THREE.Vector3 {
  // Jacobi rotations on the symmetric 3x3
  const a = [[m[0], m[1], m[2]], [m[3], m[4], m[5]], [m[6], m[7], m[8]]];
  const V = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweep = 0; sweep < 12; sweep++) {
    for (const [p, q] of [[0, 1], [0, 2], [1, 2]]) {
      if (Math.abs(a[p][q]) < 1e-12) continue;
      const th = 0.5 * Math.atan2(2 * a[p][q], a[q][q] - a[p][p]);
      const cs = Math.cos(th), sn = Math.sin(th);
      for (let k = 0; k < 3; k++) {
        const akp = a[k][p], akq = a[k][q];
        a[k][p] = cs * akp - sn * akq;
        a[k][q] = sn * akp + cs * akq;
      }
      for (let k = 0; k < 3; k++) {
        const apk = a[p][k], aqk = a[q][k];
        a[p][k] = cs * apk - sn * aqk;
        a[q][k] = sn * apk + cs * aqk;
      }
      for (let k = 0; k < 3; k++) {
        const vkp = V[k][p], vkq = V[k][q];
        V[k][p] = cs * vkp - sn * vkq;
        V[k][q] = sn * vkp + cs * vkq;
      }
    }
  }
  let i = 0;
  for (let k = 1; k < 3; k++) if (a[k][k] < a[i][i]) i = k;
  return new THREE.Vector3(V[0][i], V[1][i], V[2][i]).normalize();
}

const NOISE = /* glsl */ `
float wHash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float wNoise(vec3 x) {
  vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(wHash(i), wHash(i + vec3(1,0,0)), f.x), mix(wHash(i + vec3(0,1,0)), wHash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(wHash(i + vec3(0,0,1)), wHash(i + vec3(1,0,1)), f.x), mix(wHash(i + vec3(0,1,1)), wHash(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float wFbm(vec3 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 4; i++) { s += a * wNoise(p); p *= 2.03; a *= 0.5; } return s; }
`;

/** The shader edits (onBeforeCompile), applied after the rim overlay's. */
export function weatherShader(sh: THREE.WebGLProgramParametersWithUniforms, u: WeatherUniforms) {
  Object.assign(sh.uniforms, u);
  sh.vertexShader = sh.vertexShader
    .replace('#include <common>', '#include <common>\nvarying vec3 vOPos;\nvarying vec3 vONrm;')
    .replace('#include <begin_vertex>', '#include <begin_vertex>\nvOPos = transformed;\nvONrm = objectNormal;');
  sh.fragmentShader = sh.fragmentShader
    .replace('#include <common>', `#include <common>
uniform vec4 uWOn; uniform vec4 uW1; uniform vec4 uW2; uniform vec4 uW3;
uniform vec3 uWearC; uniform vec3 uPrimerC; uniform vec3 uGrimeC; uniform vec3 uDustC; uniform vec3 uStripeC;
uniform float uEdgeK; uniform vec4 uRAx; uniform vec3 uRC; uniform vec4 uR0; uniform vec3 uR0C; uniform vec4 uR1; uniform vec3 uR1C;
varying vec3 vOPos; varying vec3 vONrm;
${NOISE}
float wEdge; float wGrimeM; float wMetal; float wRough;`)
    .replace('#include <color_fragment>', `#include <color_fragment>
wEdge = 0.0; wGrimeM = 0.0; wMetal = 0.0; wRough = 0.0;
// paint regions (a blue ring on the ear cup's face, a grey hub): by the face's direction along the part's axis
// and its radius about it
if (uR0.w > 0.0 || uR1.w > 0.0) {
  vec3 d = vOPos - uRC;
  float rr = length(d - uRAx.xyz * dot(d, uRAx.xyz)) / uRAx.w;
  float fa = dot(normalize(vONrm), uRAx.xyz);
  if (uR0.w > 0.0 && fa * uR0.z > 0.5 && rr >= uR0.x && rr <= uR0.y) diffuseColor.rgb = uR0C;
  if (uR1.w > 0.0 && fa * uR1.z > 0.5 && rr >= uR1.x && rr <= uR1.y) diffuseColor.rgb = uR1C;
}
if (uWOn.x > 0.0) {
  vec3 P = vOPos;                         // mm, object space
  vec3 N = normalize(vONrm);
  float amt = uWOn.x;
  if (uWOn.y > 0.5) {
    // a bare print: faint layer lines (0.2 mm), a little tone variation
    float layer = 0.5 + 0.5 * sin(P.y * 31.4159);
    diffuseColor.rgb *= 1.0 - 0.035 * amt * layer - 0.04 * amt * (wFbm(P * 0.08) - 0.5);
    wRough = 0.06 * amt * layer;
  } else {
    // stripes (the visor's cream bars)
    if (uWOn.z > 0.0) {
      // bands across the droid's plan (x, z), as the Original's bake lays them; edges antialiased
      float t = fract(dot(P.xz, normalize(uW3.zw)) / uWOn.w);
      float aa = fwidth(dot(P.xz, normalize(uW3.zw)) / uWOn.w) * 1.5 + 1e-4;
      float band = smoothstep(0.0, aa, t) * (1.0 - smoothstep(uWOn.z - aa, uWOn.z, t));
      diffuseColor.rgb = mix(diffuseColor.rgb, uStripeC, band);
    }
    // the bake's noises (sim/model/build_r3x.py): big patches, mid clusters, fine grain
    float nBig = wFbm(P * 0.008 + 11.0);
    float nMid = wFbm(P * 0.05);
    float nFine = wNoise(P * 0.6);
    // paint variation and fade
    diffuseColor.rgb *= 1.0 + ((nBig - 0.5) * 2.4 + (nMid - 0.5) * 2.0) * uW2.x * amt;
    float lum = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(lum * 1.08), uW2.y * amt * smoothstep(0.35, 0.8, nBig));
    // edges: curvature per mm from the normal's screen derivatives; wear clusters where parts get handled and
    // breaks into chips (the bake's chipV), primer first, then metal
    float curv = length(fwidth(N)) / max(length(fwidth(P)), 1e-3);
    float edge = smoothstep(0.006, 0.09, curv) * (1.0 - 0.75 * smoothstep(0.25, 0.9, curv)) * uEdgeK;
    float chipN = nMid;
    float chipV = pow(edge, 0.7) * (0.55 + 1.1 * nMid) * (0.7 + 0.6 * nBig) + (nFine - 0.5) * 0.8 * uEdgeK;
    float wT = 1.0 - uW1.x * amt * 0.6;
    float metal = smoothstep(wT - 0.01, wT + 0.04, chipV) * step(0.001, uW1.x);
    // a few small chips out on the flats, where it gets knocked
    float flat_ = smoothstep(0.86, 0.9, wNoise(P * 0.22 + 2.0)) * smoothstep(0.5, 0.7, nMid) * uW1.x * amt * uEdgeK;
    metal = max(metal, flat_);
    float primer = smoothstep(wT - 0.11, wT - 0.06, chipV) * step(0.001, uW1.x);
    // fine scuffs: thin scratches on flat paint
    float sc = smoothstep(0.82, 0.95, wNoise(P * vec3(0.9, 0.08, 0.9) + vec3(0.0, wNoise(P * 0.05) * 6.0, 0.0)));
    primer = max(primer, sc * uW2.w * amt * 0.5);
    diffuseColor.rgb = mix(diffuseColor.rgb, uPrimerC, clamp(primer * (1.0 - metal), 0.0, 1.0));
    metal *= 0.75; // (decimated meshes are creased all over: keep bare metal to chips, not a chrome coat)
    diffuseColor.rgb = mix(diffuseColor.rgb, uWearC * (0.75 + 0.3 * nFine), clamp(metal, 0.0, 1.0));
    wEdge = clamp(primer, 0.0, 1.0); wMetal = clamp(metal, 0.0, 1.0);
    // grime in seams (concave: high curvature where the noise says dirt collects) and on undersides
    float under = smoothstep(0.1, -0.6, N.y);
    // (no baked occlusion here: seams by curvature where wear is not, undersides, and the bake's patchy film)
    float seam = smoothstep(0.03, 0.4, curv) * (1.0 - smoothstep(0.4, 0.7, chipV));
    float grime = (seam * 0.8 + under * 0.35) * (0.55 + 0.9 * nMid) + 0.5 * smoothstep(0.3, 0.8, nMid * 0.6 + nBig * 0.4)
      + 0.6 * smoothstep(0.66, 0.8, wNoise(P * 0.35 + 5.0)) * smoothstep(0.45, 0.7, nMid); // dirt specks
    // streaks running down the sides
    float side = 1.0 - abs(N.y);
    float streak = smoothstep(0.52, 0.85, wNoise(vec3(P.x * 0.25, P.y * 0.008, P.z * 0.25))) * side * uW1.z;
    grime = clamp((grime + streak * (0.4 + nBig)) * uW1.y * amt, 0.0, 0.85);
    diffuseColor.rgb = mix(diffuseColor.rgb, uGrimeC, grime);
    wGrimeM = grime;
    // dust on what faces up
    float dust = smoothstep(0.55, 0.95, N.y) * uW1.w * amt * (0.5 + wFbm(P * 0.04));
    diffuseColor.rgb = mix(diffuseColor.rgb, uDustC, clamp(dust * 1.6, 0.0, 0.7));
    wRough = uW2.z * amt * (wFbm(P * 0.05 + 7.0) - 0.5) * 0.5 + grime * 0.2 + dust * 0.25;
  }
}`)
    .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
if (uWOn.x > 0.0) roughnessFactor = clamp(mix(roughnessFactor + wRough, uW3.x, wMetal), 0.04, 1.0);`)
    .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>
if (uWOn.x > 0.0) metalnessFactor = mix(metalnessFactor, uW3.y, wMetal);`);
}
