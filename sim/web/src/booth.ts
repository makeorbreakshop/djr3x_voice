import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { LightProbeGridWebGL } from 'three/addons/lighting/LightProbeGridWebGL.js';
import { GROUPS, RIGS, StageLights, type Group, type RGB, type StageLightsOptions } from './stagelights';

/**
 * The set: Oga's Cantina's DJ booth, built procedurally (no downloaded or kit assets),
 * and the light that makes the droid look like the park photos
 * (~/Desktop/DJ-R3X/Reference Photos/oga-*.jpg).
 *
 * Scale is the droid's: metres, origin at the floor under the droid, droid facing +Z,
 * head top at ~0.94 m. The booth is a rock alcove - curved walls and a low dome, cut
 * open at the front by a rock facade whose arch the guests look through - holding the
 * machinery panel behind the droid, four hanging six-driver speaker cabinets, cable
 * bundles, flexible ducting, stepped cassette racks either side with a lit monitor,
 * and the bar counter in front that hides the droid's pedestal.
 *
 * Light is a stage rig run by a light desk (stagelights.ts): the droid's own fixtures -
 * an amber key on his torso from the front (the only shadow caster) and blue-violet rims
 * on both sides of the head - plus the room: uplight on the side walls and behind the
 * machinery panel, a blue-white spot on the ceiling centre, a fill through the arch,
 * washes on the cassette racks and the practicals. The rock is plain tan stucco - every
 * colour on it is light. The desk recolours and dims these fixtures per cue; it adds no
 * lights of its own (every runtime light costs every lit pixel).
 *
 * Bounce: a three.js LightProbeGrid (L2 SH irradiance, r186). SH irradiance is linear in
 * the lights, so the grid is baked once per fixture group with that group alone at unit
 * white ("basis" grids), read back to the CPU, and the live grid is their sum weighted by
 * each group's current colour - exact for any cue, including split colours. The combine
 * is ~2.5k floats x 9 bases, only on frames where the desk output changed. With the grid
 * present the environment map supplies only specular (see the ShaderChunk patch below);
 * it is the booth captured from the droid's head, once per rig preset.
 *
 * `?booth=0` swaps all of this for a clean turntable stage (dark floor, studio-ish
 * cantina lights) for neutral views of the model.
 */

// ------------------------------------------------------------------ dimensions
/** Axis of the rock alcove (it sits a little behind the droid). */
const AXIS_Z = -0.2;
/** Alcove radius and the height where the walls turn into the dome. */
const R = 1.32;
const WALL_TOP = 0.98;
const DOME = 0.52;
/** The facade plane: everything of the alcove in front of it is cut away. */
const FRONT_Z = 0.78;
/** Counter top height: the droid's orange grille ring (y 0.32-0.35) sits just above it. */
const COUNTER_TOP = 0.3;
/** Droid-side edge of the counter at x = 0; it curves back round him (radius COUNTER_ARC). */
const COUNTER_BACK = 0.4;
const COUNTER_ARC = 2.0;
/** z of the counter's droid-side edge at x. */
function counterBackZ(x: number) {
  return COUNTER_BACK - COUNTER_ARC + Math.sqrt(COUNTER_ARC * COUNTER_ARC - x * x);
}

/** Dome height above the floor at horizontal distance r from the alcove axis. */
function ceilingY(r: number) {
  return r >= R ? WALL_TOP : WALL_TOP + DOME * Math.sqrt(1 - (r / R) ** 2);
}

/** Half-width of the facade opening at height y (the alcove's section at FRONT_Z). */
function archHalfWidth(y: number) {
  const dz = FRONT_Z - AXIS_Z;
  if (y <= WALL_TOP) return Math.sqrt(R * R - dz * dz);
  const s = (y - WALL_TOP) / DOME;
  if (s >= 1) return 0;
  const rr = R * Math.sqrt(1 - s * s);
  return rr > dz ? Math.sqrt(rr * rr - dz * dz) : 0;
}
const ARCH_TOP = WALL_TOP + DOME * Math.sqrt(1 - ((FRONT_Z - AXIS_Z) / R) ** 2);

// ------------------------------------------------------------------ indirect light
// With a probe grid in the scene, its irradiance is the diffuse GI; the (booth-captured)
// environment map would count the same light twice, so it keeps only its specular part.
// Must run before any material compiles - booth.ts is imported before the model loads.
THREE.ShaderChunk.lights_fragment_maps = THREE.ShaderChunk.lights_fragment_maps.replace(
  'iblIrradiance += getIBLIrradiance( geometryNormal );',
  '#ifndef USE_LIGHT_PROBES_GRID\n\t\t\tiblIrradiance += getIBLIrradiance( geometryNormal );\n\t\t\t#endif',
);

// ------------------------------------------------------------------ noise (CPU)
function hash3(x: number, y: number, z: number) {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(z | 0, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
function vnoise(x: number, y: number, z: number) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
  const s = (t: number) => t * t * (3 - 2 * t);
  const fx = s(x - xi), fy = s(y - yi), fz = s(z - zi);
  const l = (a: number, b: number, t: number) => a + (b - a) * t;
  const c = (dx: number, dy: number, dz: number) => hash3(xi + dx, yi + dy, zi + dz);
  return l(
    l(l(c(0, 0, 0), c(1, 0, 0), fx), l(c(0, 1, 0), c(1, 1, 0), fx), fy),
    l(l(c(0, 0, 1), c(1, 0, 1), fx), l(c(0, 1, 1), c(1, 1, 1), fx), fy), fz);
}
function fbm(x: number, y: number, z: number, oct = 4) {
  let s = 0, a = 0.5, n = 0;
  for (let i = 0; i < oct; i++) {
    s += a * vnoise(x, y, z);
    n += a;
    x = x * 2.03 + 1.7; y = y * 2.03 + 9.2; z = z * 2.03 + 3.1;
    a *= 0.5;
  }
  return s / n;
}

// ------------------------------------------------------------------ surface shader
/**
 * Smooth value noise baked into a tiling 64^3 texture (16 lattice cells per tile), so
 * each octave in the shaders is one trilinear fetch instead of eight hashes: the rock
 * covers most of the screen, and this is what keeps it cheap.
 */
function noiseTexture() {
  const N = 64, CELLS = 16, K = N / CELLS;
  const lattice = new Float32Array(CELLS ** 3);
  for (let i = 0; i < lattice.length; i++) lattice[i] = hash3(i, 17, 5);
  const L = (x: number, y: number, z: number) =>
    lattice[((z + CELLS) % CELLS) * CELLS * CELLS + ((y + CELLS) % CELLS) * CELLS + ((x + CELLS) % CELLS)];
  const sm = (t: number) => t * t * (3 - 2 * t);
  const data = new Uint8Array(N ** 3);
  for (let z = 0; z < N; z++) {
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const xi = Math.floor(x / K), yi = Math.floor(y / K), zi = Math.floor(z / K);
        const fx = sm((x % K) / K), fy = sm((y % K) / K), fz = sm((z % K) / K);
        const l = (a: number, b: number, t: number) => a + (b - a) * t;
        const v = l(
          l(l(L(xi, yi, zi), L(xi + 1, yi, zi), fx), l(L(xi, yi + 1, zi), L(xi + 1, yi + 1, zi), fx), fy),
          l(l(L(xi, yi, zi + 1), L(xi + 1, yi, zi + 1), fx), l(L(xi, yi + 1, zi + 1), L(xi + 1, yi + 1, zi + 1), fx), fy), fz);
        data[z * N * N + y * N + x] = Math.round(v * 255);
      }
    }
  }
  const t = new THREE.Data3DTexture(data, N, N, N);
  t.format = THREE.RedFormat;
  t.minFilter = t.magFilter = THREE.LinearFilter;
  t.wrapS = t.wrapT = t.wrapR = THREE.RepeatWrapping;
  t.unpackAlignment = 1;
  t.needsUpdate = true;
  return t;
}
let NOISE_TEX: THREE.Data3DTexture | null = null;

const NOISE_GLSL = /* glsl */ `
uniform highp sampler3D bNoiseTex;
// One lattice unit = 1/16 of the texture; the half-texel offset centres the samples.
float bNoise(vec3 x) { return texture(bNoiseTex, x * (1.0 / 16.0) + 0.5 / 64.0).r; }
float bFbm(vec3 p) {
  float s = 0.5 * bNoise(p);
  s += 0.25 * bNoise(p * 2.03 + vec3(1.7, 9.2, 3.1));
  s += 0.125 * bNoise(p * 4.09 + vec3(5.3, 2.8, 7.7));
  s += 0.0625 * bNoise(p * 8.21 + vec3(3.9, 6.1, 0.4));
  return s / 0.9375;
}
`;

/** Same soft knee as the droid (look.ts): lit surfaces stay below the bloom threshold. */
const KNEE_GLSL = /* glsl */ `
  vec3 bK = max(gl_FragColor.rgb - 0.7, 0.0);
  gl_FragColor.rgb -= bK - bK / (1.0 + bK * 3.4);`;

/**
 * Rack washes: two soft, shadowless area lights evaluated inside the booth's own surface
 * shader instead of as scene lights, so they cost a few ALU ops on booth pixels only and
 * never touch the droid (in the park the rack washes are focused on the racks too).
 * `src` is where the light comes from (for the Lambert term), `ctr`/`rad` the soft region
 * it covers. Colours are written by the light desk; the uniforms are shared by every
 * booth material.
 */
const WASH = {
  uWashSrc: { value: [new THREE.Vector3(), new THREE.Vector3()] },
  uWashCtr: { value: [new THREE.Vector3(), new THREE.Vector3()] },
  uWashCol: { value: [new THREE.Color(0, 0, 0), new THREE.Color(0, 0, 0)] },
  uWashRad: { value: [1, 1] },
};
const WASH_GLSL = /* glsl */ `
  for (int i = 0; i < 2; i++) {
    vec3 bToL = (viewMatrix * vec4(uWashSrc[i] - vBPos, 0.0)).xyz;
    float bFall = 1.0 - smoothstep(uWashRad[i] * 0.3, uWashRad[i], length(vBPos - uWashCtr[i]));
    outgoingLight += material.diffuseColor * uWashCol[i] * (max(dot(normal, normalize(bToL)), 0.0) * bFall);
  }`;

interface Surface {
  /** Noise frequency (1/m) of the broad variation; the fine layer is 4.3x. */
  freq: number;
  /** Albedo drift, 0..1. */
  variation: number;
  /** Dirt in patches and at the floor. */
  grime: number;
  grimeColor?: number;
  /** Bump height (m). */
  bump: number;
  /** Rock: cracks, pits and strong light/dark mottling. */
  rock?: boolean;
  /** Discard everything in front of the facade (the alcove shell). */
  clipFront?: boolean;
}

function surface(params: THREE.MeshStandardMaterialParameters, s: Surface): THREE.MeshStandardMaterial {
  const m = new THREE.MeshStandardMaterial(params);
  const u = {
    uFreq: { value: s.freq },
    uVar: { value: s.variation },
    uGrime: { value: s.grime },
    uGrimeColor: { value: new THREE.Color(s.grimeColor ?? 0x120e0a) },
    uBump: { value: s.bump },
    uClipZ: { value: FRONT_Z },
    bNoiseTex: { value: (NOISE_TEX ??= noiseTexture()) },
  };
  const defs = `${s.rock ? '#define B_ROCK\n' : ''}${s.clipFront ? '#define B_CLIP\n' : ''}`;
  m.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u, WASH);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vBPos;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvBPos = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
${defs}varying vec3 vBPos;
uniform float uFreq, uVar, uGrime, uBump, uClipZ;
uniform vec3 uGrimeColor;
uniform vec3 uWashSrc[2], uWashCtr[2], uWashCol[2];
uniform float uWashRad[2];
${NOISE_GLSL}`)
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
#ifdef B_CLIP
  if (vBPos.z > uClipZ) discard;
#endif`)
      .replace('#include <color_fragment>', `#include <color_fragment>
  vec3 bP = vBPos * uFreq;
  float bN1 = bFbm(bP);
  float bN2 = bFbm(bP * 4.3 + 7.1);
  float bH = bN1 * 0.7 + bN2 * 0.3;
#ifdef B_ROCK
  // Sculpted stucco rock: broad light/dark mottling, a lumpy trowelled surface, sparse
  // jagged cracks (warped ridged noise) and fine pitting.
  vec3 bW = vec3(bN1, bN2, bN1 * bN2) * 1.6;
  float bCr = 1.0 - abs(bFbm(bP * 0.7 + bW) * 2.0 - 1.0);
  float bCrack = smoothstep(0.99, 0.999, bCr) * smoothstep(0.5, 0.72, bNoise(bP * 0.5 + 4.0));
  float bLump = bNoise(vBPos * 23.0);
  float bGrain = bNoise(vBPos * 95.0);
  float bPit = smoothstep(0.8, 0.92, bNoise(vBPos * 61.0 + bN2 * 3.0));
  diffuseColor.rgb *= mix(0.55, 1.2, smoothstep(0.25, 0.75, bH)) * (0.9 + 0.2 * bLump);
  diffuseColor.rgb *= 1.0 - 0.5 * bCrack - 0.25 * bPit;
  bH = bH + (bLump - 0.5) * 0.28 + (bGrain - 0.5) * 0.07 - bCrack * 0.2 - bPit * 0.08;
#else
  diffuseColor.rgb *= 1.0 + (bN1 - 0.5) * 2.0 * uVar;
#endif
  float bG = uGrime * smoothstep(0.42, 0.85, bN2 * 0.55 + bN1 * 0.45);
  bG += uGrime * 0.5 * (1.0 - smoothstep(0.0, 0.3, vBPos.y));
  bG = clamp(bG, 0.0, 0.8);
  diffuseColor.rgb = mix(diffuseColor.rgb, uGrimeColor, bG);`)
      .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
  roughnessFactor = clamp(roughnessFactor + (bN2 - 0.5) * 0.3 + bG * 0.25, 0.05, 1.0);`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>
  metalnessFactor *= 1.0 - bG;`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
  {
    float bHt = bH * uBump;
    vec3 bDpx = dFdx(-vViewPosition);
    vec3 bDpy = dFdy(-vViewPosition);
    vec3 bR1 = cross(bDpy, normal);
    vec3 bR2 = cross(normal, bDpx);
    float bDet = dot(bDpx, bR1) * faceDirection;
    vec3 bGrad = sign(bDet) * (dFdx(bHt) * bR1 + dFdy(bHt) * bR2);
    normal = normalize(abs(bDet) * normal - bGrad);
  }`)
      .replace('#include <opaque_fragment>', `${WASH_GLSL}\n#include <opaque_fragment>${KNEE_GLSL}`);
  };
  m.customProgramCacheKey = () => `booth-${s.rock ? 'rock' : 'metal'}-${s.clipFront ? 'clip' : ''}`;
  return m;
}

// ------------------------------------------------------------------ canvas textures
function canvasTexture(w: number, h: number, draw: (g: CanvasRenderingContext2D) => void, srgb = true) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  draw(c.getContext('2d')!);
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  return t;
}

/** Perforated sheet: staggered round holes. UVs are metres, so repeat = holes per metre / 2. */
function perforated() {
  const t = canvasTexture(64, 64, (g) => {
    g.fillStyle = '#8a857c';
    g.fillRect(0, 0, 64, 64);
    g.fillStyle = '#070606';
    for (const [x, y] of [[16, 16], [48, 16], [0, 48], [32, 48], [64, 48]]) {
      g.beginPath();
      g.arc(x, y, 10, 0, Math.PI * 2);
      g.fill();
    }
  });
  t.repeat.set(55, 55);
  return t;
}

/**
 * The CRT beside the droid (stage left): a blue-white circuit diagram - a pale blue-white
 * field with a maze of blue blocks and traces (venue video stills), and a dark vignette
 * toward the curved glass's edge.
 */
function monitorScreen() {
  const W = 160, H = 176;
  return canvasTexture(W, H, (g) => {
    g.fillStyle = '#e4ecff';
    g.fillRect(0, 0, W, H);
    let seed = 11;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    // Blocks: a grid of cells, some merged, filled in two blues.
    const C = 10;
    for (let y = 8; y < H - 12; y += C + 2) {
      for (let x = 8; x < W - 12; x += C + 2) {
        const r = rnd();
        if (r < 0.34) continue;
        const w = r > 0.8 ? C * 2 + 2 : C, h = rnd() > 0.85 ? C * 2 + 2 : C;
        g.fillStyle = r > 0.62 ? '#2f4fd6' : '#6f8ff0';
        g.fillRect(x, y, Math.min(w, W - 10 - x), Math.min(h, H - 10 - y));
      }
    }
    // Traces between them.
    g.strokeStyle = '#1b35b0';
    g.lineWidth = 2;
    for (let i = 0; i < 22; i++) {
      const x = 8 + Math.floor(rnd() * 12) * 12, y = 8 + Math.floor(rnd() * 13) * 12;
      g.beginPath();
      const x2 = x + (rnd() > 0.5 ? 1 : -1) * 12 * (1 + Math.floor(rnd() * 4));
      g.moveTo(x, y);
      g.lineTo(x2, y);
      g.lineTo(x2, y + 12 * (1 + Math.floor(rnd() * 3)));
      g.stroke();
    }
    const v = g.createRadialGradient(W / 2, H / 2, W * 0.3, W / 2, H / 2, W * 0.72);
    v.addColorStop(0, 'rgba(10,20,60,0)');
    v.addColorStop(1, 'rgba(10,20,60,0.75)');
    g.fillStyle = v;
    g.fillRect(0, 0, W, H);
  });
}

// ------------------------------------------------------------------ geometry helpers
const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

function mtx(pos: [number, number, number], rot: [number, number, number] = [0, 0, 0], scale: [number, number, number] = [1, 1, 1]) {
  return new THREE.Matrix4().compose(
    new THREE.Vector3(...pos),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(rot[0], rot[1], rot[2], 'YXZ')),
    new THREE.Vector3(...scale),
  );
}

/** Tube along a curve with a radius profile (corrugation, taper); normals from the surface. */
function tube(curve: THREE.Curve<THREE.Vector3>, seg: number, radial: number, radius: (u: number, dist: number) => number) {
  const frames = curve.computeFrenetFrames(seg, false);
  const len = curve.getLength();
  const pos: number[] = [];
  const uv: number[] = [];
  const idx: number[] = [];
  const p = new THREE.Vector3();
  for (let i = 0; i <= seg; i++) {
    const u = i / seg;
    curve.getPointAt(u, p);
    const r = radius(u, u * len);
    for (let j = 0; j <= radial; j++) {
      const a = (j / radial) * Math.PI * 2;
      const n = frames.normals[i].clone().multiplyScalar(Math.cos(a)).addScaledVector(frames.binormals[i], Math.sin(a));
      pos.push(p.x + n.x * r, p.y + n.y * r, p.z + n.z * r);
      uv.push(u * len, j / radial);
    }
  }
  for (let i = 0; i < seg; i++) {
    for (let j = 0; j < radial; j++) {
      const a = i * (radial + 1) + j;
      const b = a + radial + 1;
      idx.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

/** A hanging cable: sags between two points (a parabola is close enough to a catenary). */
function cable(a: THREE.Vector3, b: THREE.Vector3, sag: number, r = 0.007) {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= 8; i++) {
    const t = i / 8;
    const p = a.clone().lerp(b, t);
    p.y -= sag * 4 * t * (1 - t);
    pts.push(p);
  }
  return tube(new THREE.CatmullRomCurve3(pts), 28, 6, () => r);
}

/** Flexible corrugated ducting. */
function duct(points: THREE.Vector3[], r: number, pitch = r * 0.45) {
  const c = new THREE.CatmullRomCurve3(points);
  const segs = Math.ceil((c.getLength() / pitch) * 4);
  return tube(c, segs, 20, (_u, d) => r * (1 + 0.07 * Math.sin((d / pitch) * Math.PI * 2)));
}

/** Collects geometry per material and merges it: the whole set is a handful of draw calls. */
class Kit {
  private parts = new Map<string, THREE.BufferGeometry[]>();
  add(mat: string, geo: THREE.BufferGeometry, m?: THREE.Matrix4) {
    let g = geo.index ? geo.toNonIndexed() : geo.clone();
    for (const k of Object.keys(g.attributes)) if (!['position', 'normal', 'uv'].includes(k)) g.deleteAttribute(k);
    if (!g.attributes.normal) g.computeVertexNormals();
    if (!g.attributes.uv) g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
    if (m) g = g.applyMatrix4(m);
    const list = this.parts.get(mat) ?? [];
    list.push(g);
    this.parts.set(mat, list);
    geo.dispose();
  }
  build(mats: Record<string, THREE.Material>, cast: Set<string>) {
    const group = new THREE.Group();
    for (const [name, list] of this.parts) {
      const mesh = new THREE.Mesh(mergeGeometries(list), mats[name]);
      list.forEach((g) => g.dispose());
      mesh.name = `booth_${name}`;
      mesh.receiveShadow = !(mats[name] as THREE.MeshBasicMaterial).isMeshBasicMaterial;
      mesh.castShadow = cast.has(name);
      group.add(mesh);
    }
    return group;
  }
}

// ------------------------------------------------------------------ the pieces

/** The alcove: vertical walls rising into a low dome, displaced into rock. */
function rockShell() {
  const AZ = 180;
  const wallSegs = 18;
  const domeSegs = 22;
  const rows: { r: number; y: number }[] = [];
  for (let i = 0; i <= wallSegs; i++) rows.push({ r: R, y: -0.05 + (i / wallSegs) * (WALL_TOP + 0.05) });
  for (let i = 1; i <= domeSegs; i++) {
    const t = (i / domeSegs) * (Math.PI / 2);
    rows.push({ r: R * Math.cos(t), y: WALL_TOP + DOME * Math.sin(t) });
  }
  const pos: number[] = [];
  const uv: number[] = [];
  const idx: number[] = [];
  for (let i = 0; i < rows.length; i++) {
    const { r, y } = rows[i];
    for (let j = 0; j <= AZ; j++) {
      const a = (j / AZ) * Math.PI * 2;
      const x = Math.sin(a) * r;
      const z = AXIS_Z - Math.cos(a) * r;
      // Inward normal of the undisplaced surface.
      const t = i > wallSegs ? ((i - wallSegs) / domeSegs) * (Math.PI / 2) : 0;
      const ny = -Math.sin(t);
      const nh = -Math.cos(t);
      const nx = Math.sin(a) * nh;
      const nz = -Math.cos(a) * nh;
      // Big lumps, medium knobs; fade the displacement out at the floor.
      const px = x * 1.25, py = y * 1.25, pz = z * 1.25;
      let d = (fbm(px, py, pz, 3) - 0.5) * 0.3 + (fbm(px * 3.4 + 5, py * 3.4, pz * 3.4, 3) - 0.5) * 0.1;
      d *= Math.min(1, (y + 0.05) / 0.25);
      pos.push(x + nx * d, y + ny * d, z + nz * d);
      uv.push(j / AZ, i / rows.length);
    }
  }
  for (let i = 0; i < rows.length - 1; i++) {
    for (let j = 0; j < AZ; j++) {
      const a = i * (AZ + 1) + j;
      const b = a + AZ + 1;
      idx.push(a, a + 1, b, b, a + 1, b + 1);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

/** The facade opening, inset from the alcove's section: bottom-left, up, over, down to bottom-right. */
function archOutline(inset: number) {
  const side: THREE.Vector2[] = [];
  const top = ARCH_TOP - inset;
  const N = 40;
  for (let i = 0; i <= N; i++) {
    const y = COUNTER_TOP - 0.02 + (i / N) * (top - (COUNTER_TOP - 0.02));
    const w = Math.max(0.05, archHalfWidth(Math.min(y + inset, ARCH_TOP - 1e-3)) - inset);
    side.push(new THREE.Vector2(w, y));
  }
  return [...side.map((p) => new THREE.Vector2(-p.x, p.y)), ...side.reverse()];
}

/** The facade the guests look through: rock wall with the arch cut out, and a rounded lip. */
function facade() {
  const outer = new THREE.Shape();
  outer.moveTo(-3.2, COUNTER_TOP - 0.02);
  outer.lineTo(3.2, COUNTER_TOP - 0.02);
  outer.lineTo(3.2, 2.8);
  outer.lineTo(-3.2, 2.8);
  outer.closePath();
  // The hole is inset from the alcove's section so the facade always overlaps the shell's cut edge.
  const pts = archOutline(0.11);
  const hole = new THREE.Path();
  hole.moveTo(pts[0].x, pts[0].y);
  for (const p of pts.slice(1)) hole.lineTo(p.x, p.y);
  hole.closePath();
  // Opening reaches down to the counter: cut the outer shape's bottom edge through it.
  outer.holes.push(hole);
  const g = new THREE.ExtrudeGeometry(outer, {
    depth: 0.12, bevelEnabled: true, bevelThickness: 0.05, bevelSize: 0.06, bevelSegments: 3, curveSegments: 4,
  });
  g.translate(0, 0, FRONT_Z - 0.12);
  return g;
}

/** Six-driver speaker cabinet (2 x 3), front facing +Z, origin at its centre. */
function speaker(kit: Kit, base: THREE.Matrix4) {
  const W = 0.38, H = 0.52, D = 0.2;
  kit.add('cabinet', new RoundedBoxGeometry(W, H, D, 3, 0.03), base);
  // Recessed baffle.
  kit.add('darkMetal', new THREE.BoxGeometry(W - 0.05, H - 0.05, 0.01), base.clone().multiply(mtx([0, 0, D / 2 - 0.004])));
  const driver = new THREE.LatheGeometry([
    // Surround roll, then a big convex dome (the park cabinets' drivers bulge outward).
    [0.08, 0], [0.078, 0.005], [0.073, 0.009], [0.067, 0.008], [0.063, 0.003], [0.06, 0.002],
    [0.055, 0.012], [0.047, 0.021], [0.036, 0.028], [0.024, 0.033], [0.012, 0.0355], [0, 0.0365],
  ].map(([r, y]) => new THREE.Vector2(r, y)), 32);
  const trim = new THREE.TorusGeometry(0.082, 0.006, 6, 32);
  for (let c = 0; c < 2; c++) {
    for (let r = 0; r < 3; r++) {
      const at = base.clone().multiply(mtx([(c - 0.5) * 0.172, (1 - r) * 0.162, D / 2 + 0.002]));
      kit.add('rubber', driver.clone(), at.clone().multiply(mtx([0, 0, 0], [Math.PI / 2, 0, 0])));
      kit.add('panel', trim.clone(), at);
    }
  }
  driver.dispose();
  trim.dispose();
  // Hanging bracket on top.
  kit.add('darkMetal', new THREE.BoxGeometry(0.2, 0.025, 0.05), base.clone().multiply(mtx([0, H / 2 + 0.012, 0])));
}

/** A rack "cassette": a small box unit with a tan pull handle, front facing +Z. */
function cassette(kit: Kit, at: THREE.Matrix4) {
  kit.add('darkMetal', new RoundedBoxGeometry(0.15, 0.065, 0.13, 2, 0.008), at);
  kit.add('tan', new THREE.BoxGeometry(0.13, 0.05, 0.006), at.clone().multiply(mtx([0, 0, 0.066])));
  const handle = new THREE.CatmullRomCurve3([V(-0.05, 0, 0.066), V(-0.05, 0, 0.09), V(0.05, 0, 0.09), V(0.05, 0, 0.066)], false, 'catmullrom', 0.1);
  kit.add('panel', tube(handle, 12, 6, () => 0.0085), at.clone().multiply(mtx([0, 0.006, 0])));
}

/** Stepped rack of cassettes either side of the droid; origin at the front-bottom-centre. */
function rack(kit: Kit, base: THREE.Matrix4, rows: number, cols: number, emissive: [number, number, number, THREE.Color][]) {
  const pitchX = 0.165;
  const width = cols * pitchX + 0.04;
  // Stepped body (side profile extruded across the width).
  const prof = new THREE.Shape();
  prof.moveTo(0.02, 0);
  for (let r = 0; r < rows; r++) {
    prof.lineTo(0.02 - r * 0.075, r * 0.1 + 0.05);
    prof.lineTo(0.02 - (r + 1) * 0.075, r * 0.1 + 0.05);
  }
  prof.lineTo(-(rows + 1) * 0.075 - 0.1, rows * 0.1 + 0.05);
  prof.lineTo(-(rows + 1) * 0.075 - 0.1, 0);
  prof.closePath();
  const body = new THREE.ExtrudeGeometry(prof, { depth: width, bevelEnabled: false });
  // Shape is in (z, y) - map shape x -> z, extrude -> x.
  body.applyMatrix4(new THREE.Matrix4().makeBasis(V(0, 0, 1), V(0, 1, 0), V(-1, 0, 0)));
  body.translate(width / 2, 0, 0);
  kit.add('panel', body, base);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const at = base.clone().multiply(mtx([(c - (cols - 1) / 2) * pitchX, r * 0.1 + 0.085, -r * 0.075 - 0.03], [-0.42, 0, 0]));
      cassette(kit, at);
    }
    // A small lit readout at the end of every other row.
    if (r % 2 === 0) {
      const p = new THREE.Vector3(((cols - 1) / 2) * pitchX + 0.05, r * 0.1 + 0.12, -r * 0.075 + 0.005).applyMatrix4(base);
      emissive.push([p.x, p.y, p.z, new THREE.Color(r === 0 ? 0xff5a2a : 0x3affc0)]);
    }
  }
}

/**
 * The machinery wall behind the droid. Face at z = PANEL_Z, facing +Z. From the venue
 * video and the 2019 shows: a trapezoid whose shoulders slope in from just above the
 * racks to a flat top at head height; a plate with a pipe elbow on the left, three
 * stacked perforated stadium vents behind the neck, a slatted plate with a burnt-out
 * round fan hole on the right, a block of cassette slots right of the torso, and square
 * grille panels with latch plates along the bottom.
 */
const PANEL_Z = -0.55;
const PANEL_W = 0.74, PANEL_SHOULDER = 0.42, PANEL_TOP = 0.86, PANEL_TOP_W = 0.46;
function machineryPanel(kit: Kit, grille: THREE.Material, mats: Record<string, THREE.Material>) {
  const Z = PANEL_Z;
  const shape = new THREE.Shape();
  shape.moveTo(-PANEL_W, 0);
  shape.lineTo(PANEL_W, 0);
  shape.lineTo(PANEL_W, PANEL_SHOULDER);
  shape.lineTo(PANEL_TOP_W, PANEL_TOP);
  shape.lineTo(-PANEL_TOP_W, PANEL_TOP);
  shape.lineTo(-PANEL_W, PANEL_SHOULDER);
  shape.closePath();
  const slab = new THREE.ExtrudeGeometry(shape, { depth: 0.1, bevelEnabled: true, bevelThickness: 0.01, bevelSize: 0.01, bevelSegments: 1 });
  slab.translate(0, 0, Z - 0.1);
  kit.add('panel', slab);
  // Raised plates: left (elbow), right (fan hole), and the vent plate behind the neck.
  const plates: [number, number, number, number][] = [[-0.56, -0.25, 0.3, 0.64], [0.25, 0.56, 0.3, 0.64]];
  const plate = (x0: number, x1: number, y0: number, y1: number, depth = 0.025) =>
    kit.add('panel', new RoundedBoxGeometry(x1 - x0, y1 - y0, depth, 2, 0.008), mtx([(x0 + x1) / 2, (y0 + y1) / 2, Z + depth / 2]));
  for (const [x0, x1, y0, y1] of plates) plate(x0, x1, y0, y1);
  plate(-0.22, 0.22, 0.38, 0.8, 0.018);
  // Three perforated vents behind the neck (stadium shapes), with dark surrounds.
  const stadium = (w: number, h: number) => {
    const sh = new THREE.Shape();
    const r = h / 2;
    sh.absarc(w / 2 - r, 0, r, -Math.PI / 2, Math.PI / 2, false);
    sh.absarc(-w / 2 + r, 0, r, Math.PI / 2, (3 * Math.PI) / 2, false);
    return sh;
  };
  const grilleParts: THREE.BufferGeometry[] = [];
  for (const y of [0.47, 0.59, 0.71]) {
    const ring = new THREE.ExtrudeGeometry(stadium(0.38, 0.095), { depth: 0.012, bevelEnabled: false, curveSegments: 10 });
    kit.add('darkMetal', ring, mtx([0, y, Z + 0.016]));
    const g = new THREE.ShapeGeometry(stadium(0.355, 0.074), 10);
    g.translate(0, y, Z + 0.03);
    grilleParts.push(g);
  }
  // Left plate: a louvre column and the pipe elbow - out of the plate, over, and down
  // into a collar lower on the plate.
  for (let r = 0; r < 8; r++) {
    kit.add('darkMetal', new THREE.BoxGeometry(0.012, 0.05, 0.01), mtx([-0.3 - (r % 2) * 0.02, 0.36 + Math.floor(r / 2) * 0.07, Z + 0.027]));
  }
  const EX = -0.42, EY = 0.47, ET = 0.024;
  const elbow = new THREE.CatmullRomCurve3([
    V(EX + 0.03, EY + 0.1, Z + 0.01), V(EX + 0.01, EY + 0.11, Z + 0.07), V(EX - 0.03, EY + 0.05, Z + 0.12),
    V(EX - 0.05, EY - 0.04, Z + 0.11), V(EX - 0.04, EY - 0.1, Z + 0.05), V(EX - 0.03, EY - 0.11, Z + 0.01),
  ]);
  kit.add('darkMetal', tube(elbow, 40, 14, () => ET));
  for (const [x, y] of [[EX + 0.03, EY + 0.1], [EX - 0.03, EY - 0.11]]) {
    kit.add('darkMetal', new THREE.CylinderGeometry(ET * 1.6, ET * 1.6, 0.016, 16).rotateX(Math.PI / 2), mtx([x, y, Z + 0.03]));
  }
  // Right plate: vertical slats round a burnt-out fan hole.
  for (let i = 0; i < 11; i++) {
    const x = 0.28 + i * 0.025;
    kit.add('darkMetal', new THREE.BoxGeometry(0.008, 0.28, 0.018), mtx([x, 0.47, Z + 0.034]));
  }
  const FX = 0.405, FY = 0.48;
  kit.add('rubber', new THREE.CircleGeometry(0.068, 28), mtx([FX, FY, Z + 0.045]));
  kit.add('darkMetal', new THREE.TorusGeometry(0.07, 0.009, 8, 28), mtx([FX, FY, Z + 0.045]));
  let seed = 3;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 9; i++) {
    // Charred, bent blade stubs round the hub.
    const a = (i / 9) * Math.PI * 2 + rnd() * 0.4;
    const len = 0.02 + rnd() * 0.035;
    kit.add('rubber', new THREE.BoxGeometry(len, 0.012, 0.004), mtx([FX + Math.cos(a) * (0.022 + len / 2), FY + Math.sin(a) * (0.022 + len / 2), Z + 0.05 + rnd() * 0.01], [rnd() * 0.6, 0, a]));
  }
  kit.add('darkMetal', new THREE.CylinderGeometry(0.016, 0.016, 0.02, 12).rotateX(Math.PI / 2), mtx([FX, FY, Z + 0.052]));
  // Pipes: along the top edge, down both sides, and up the right shoulder.
  const pipe = (a: THREE.Vector3, b: THREE.Vector3, r: number) => {
    const d = b.clone().sub(a);
    const g = new THREE.CylinderGeometry(r, r, d.length(), 14);
    g.applyMatrix4(new THREE.Matrix4().makeRotationFromQuaternion(new THREE.Quaternion().setFromUnitVectors(V(0, 1, 0), d.clone().normalize())));
    g.translate((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
    kit.add('darkMetal', g);
  };
  pipe(V(-PANEL_TOP_W + 0.02, PANEL_TOP + 0.02, Z - 0.03), V(PANEL_TOP_W - 0.02, PANEL_TOP + 0.02, Z - 0.03), 0.017);
  pipe(V(-0.71, 0.0, Z + 0.03), V(-0.71, PANEL_SHOULDER - 0.01, Z + 0.03), 0.018);
  pipe(V(0.71, 0.0, Z + 0.03), V(0.71, PANEL_SHOULDER - 0.01, Z + 0.03), 0.018);
  pipe(V(0.69, PANEL_SHOULDER + 0.04, Z + 0.02), V(0.47, PANEL_TOP - 0.05, Z + 0.02), 0.011);
  // Greebles: bolts round the plates, knobs, a junction box with a cable.
  const bolt = new THREE.CylinderGeometry(0.007, 0.007, 0.008, 8).rotateX(Math.PI / 2);
  for (const [x0, x1, y0, y1] of plates) {
    for (const x of [x0 + 0.02, x1 - 0.02]) for (const y of [y0 + 0.02, y1 - 0.02]) kit.add('darkMetal', bolt.clone(), mtx([x, y, Z + 0.029]));
  }
  bolt.dispose();
  kit.add('darkMetal', new RoundedBoxGeometry(0.1, 0.08, 0.07, 2, 0.01), mtx([0.34, 0.73, Z + 0.035]));
  for (let i = 0; i < 4; i++) {
    kit.add('darkMetal', new THREE.CylinderGeometry(0.012, 0.012, 0.03, 10).rotateX(Math.PI / 2), mtx([-0.19 + i * 0.045, 0.34, Z + 0.03]));
  }
  // Cassette slots right of the torso (2 rows x 3) and left of it (2 x 2), above the counter.
  for (const [x, y] of [[0.14, 0.36], [0.3, 0.36], [0.46, 0.36], [0.14, 0.27], [0.3, 0.27], [0.46, 0.27], [-0.3, 0.27], [-0.46, 0.27], [-0.3, 0.2], [-0.46, 0.2]]) {
    cassette(kit, mtx([x, y, Z + 0.07], [-0.25, 0, 0]));
  }
  // Square grille panels with latch plates along the foot (mostly behind the counter).
  for (const x of [-0.58, -0.12, 0.2, 0.56]) {
    kit.add('panel', new RoundedBoxGeometry(0.16, 0.13, 0.02, 2, 0.006), mtx([x, 0.09, Z + 0.012]));
    const g = new THREE.PlaneGeometry(0.12, 0.09);
    g.translate(x, 0.09, Z + 0.023);
    grilleParts.push(g);
    kit.add('darkMetal', new THREE.BoxGeometry(0.03, 0.1, 0.012), mtx([x + 0.11, 0.09, Z + 0.015]));
  }
  mats.grille = grille;
  const g = mergeGeometries(grilleParts.map((p) => (p.index ? p.toNonIndexed() : p)));
  grilleParts.forEach((p) => p.dispose());
  kit.add('grille', g);
}

// ------------------------------------------------------------------ light desk binding

/**
 * The booth's light desk: a StageLights whose output lands on the booth's fixtures.
 * Drive it with setMode / setBpm / beat / goCue / setRig. update(dt) works as usual; if no
 * caller ever calls it, the booth advances the desk itself on the render clock.
 *
 * The sim itself does not conduct: `setExternal` lands the performer's stage output (its
 * own desk, in Rust) on the fixtures and pauses this desk's program; setRig still switches
 * the booth's rig (bakes and env capture).
 */
export class BoothLights extends StageLights {
  /** While set, the render hook neither advances nor pushes the desk (the booth's own bakes). */
  paused = false;
  private manual = false;
  private synced = -1;
  private lastTick = -1;
  private external = false;

  constructor(
    opts: StageLightsOptions,
    private readonly onSync: (desk: StageLights) => void,
    private readonly onRig: (rig: string) => void,
  ) {
    super(opts);
  }

  override update(dt: number) {
    this.manual = true;
    super.update(dt);
    this.sync();
  }

  override setRig(name: string, fadeS?: number) {
    super.setRig(name, fadeS);
    this.onRig(this.rigPreset);
    this.sync();
  }

  /** Push the output to the scene if it changed since the last push. */
  sync(force = false) {
    if (!force && this.synced === this.version) return;
    this.synced = this.version;
    this.onSync(this);
  }

  /**
   * Output from outside: linear flux per group in GROUPS order (performer frames). The
   * desk's own program stops advancing until `null` hands it back.
   */
  setExternal(stage: ArrayLike<ArrayLike<number>> | null) {
    this.external = !!stage;
    if (!stage) return;
    GROUPS.forEach((g, i) => {
      const c = stage[i];
      const o = this.out[g];
      if (c) for (let k = 0; k < 3; k++) o[k] = c[k];
    });
    this.version++;
    this.sync();
  }

  /** @internal Called by the booth once per rendered pass with the render clock (s). */
  tick(nowS: number) {
    if (this.paused) return;
    if (!this.manual && !this.external) {
      const dt = this.lastTick < 0 ? 0 : Math.min(0.5, nowS - this.lastTick);
      this.lastTick = nowS;
      if (dt > 0) super.update(dt);
    }
    this.sync();
  }
}

// ------------------------------------------------------------------ stage API

export interface StageSet {
  booth: boolean;
  /** Keep the camera inside the booth's open front (call after controls.update()). */
  constrain(camera: THREE.Camera, target: THREE.Vector3): void;
  /** Camera presets for this set: [position, target]. */
  cams: Record<string, [THREE.Vector3, THREE.Vector3]>;
  /** The booth's light desk (absent on the turntable stage). See stagelights.ts. */
  lights?: BoothLights;
}

/** Build the set into the scene (tone mapping and exposure belong to post.ts). */
export function setupStage(renderer: THREE.WebGLRenderer, scene: THREE.Scene, booth = true): StageSet {
  return booth ? buildBooth(renderer, scene) : buildTurntable(renderer, scene);
}

/** OrbitControls limits that suit the set (azimuth, polar angle, distance, pan). */
export function limitControls(controls: { minDistance: number; maxDistance: number; minPolarAngle: number; maxPolarAngle: number; minAzimuthAngle: number; maxAzimuthAngle: number }, booth: boolean) {
  controls.minDistance = 0.25;
  if (booth) {
    controls.maxDistance = 3.2;
    controls.minPolarAngle = 0.35;
    controls.maxPolarAngle = 1.95;
    controls.minAzimuthAngle = -1.2;
    controls.maxAzimuthAngle = 1.2;
  } else {
    controls.maxDistance = 5;
  }
}

function buildBooth(renderer: THREE.WebGLRenderer, scene: THREE.Scene): StageSet {
  const bg = new THREE.Color(0x030203);
  scene.background = bg;
  scene.fog = new THREE.Fog(bg, 4.5, 12);

  const grilleTex = perforated();
  const mats: Record<string, THREE.Material> = {
    rock: surface({ color: 0x9c8672, roughness: 0.95, side: THREE.DoubleSide }, { freq: 2.2, variation: 0, grime: 0.25, grimeColor: 0x2a1e18, bump: 0.035, rock: true, clipFront: true }),
    facade: surface({ color: 0x7a685a, roughness: 0.95 }, { freq: 2.2, variation: 0, grime: 0.35, grimeColor: 0x2a1e18, bump: 0.02, rock: true }),
    // The ribbed rust-orange arch lip (venue video, right edge of the frame).
    rust: surface({ color: 0x9a5832, roughness: 0.92 }, { freq: 3.5, variation: 0, grime: 0.3, grimeColor: 0x2a140a, bump: 0.01, rock: true }),
    panel: surface({ color: 0x7a7063, roughness: 0.62, metalness: 0.35 }, { freq: 5, variation: 0.18, grime: 0.55, bump: 0.0015 }),
    cabinet: surface({ color: 0x8a8175, roughness: 0.6, metalness: 0.3 }, { freq: 5, variation: 0.2, grime: 0.5, bump: 0.0015 }),
    darkMetal: surface({ color: 0x34322f, roughness: 0.5, metalness: 0.55 }, { freq: 6, variation: 0.15, grime: 0.35, bump: 0.001 }),
    tan: surface({ color: 0xb89058, roughness: 0.5, metalness: 0.1 }, { freq: 7, variation: 0.2, grime: 0.45, bump: 0.001 }),
    rubber: surface({ color: 0x1a1918, roughness: 0.82 }, { freq: 8, variation: 0.1, grime: 0.2, bump: 0.0008 }),
    duct: surface({ color: 0x8f7a60, roughness: 0.42, metalness: 0.85 }, { freq: 6, variation: 0.15, grime: 0.45, bump: 0.001 }),
    counter: surface({ color: 0x2b2c2a, roughness: 0.62, metalness: 0.25 }, { freq: 3, variation: 0.1, grime: 0.3, bump: 0.001 }),
    floor: surface({ color: 0x1d1b19, roughness: 0.75, metalness: 0.4 }, { freq: 3, variation: 0.2, grime: 0.5, bump: 0.001 }),
    crt: surface({ color: 0x5e4232, roughness: 0.55, metalness: 0.4 }, { freq: 6, variation: 0.2, grime: 0.5, bump: 0.001 }),
    olive: surface({ color: 0x3c3f24, roughness: 0.6, metalness: 0.3 }, { freq: 5, variation: 0.2, grime: 0.45, bump: 0.0012 }),
    deck: surface({ color: 0xb9b2a4, roughness: 0.55, metalness: 0.15 }, { freq: 5, variation: 0.15, grime: 0.4, bump: 0.001 }),
    glow: new THREE.MeshBasicMaterial({ vertexColors: true, fog: false }),
    screen: new THREE.MeshBasicMaterial({ map: monitorScreen(), color: new THREE.Color(0.85, 0.85, 0.85), fog: false }),
  };
  const grille = surface({ map: grilleTex, color: 0x6a655e, roughness: 0.6, metalness: 0.5 }, { freq: 6, variation: 0.1, grime: 0.3, bump: 0.0005 });
  const kit = new Kit();
  const glowSpots: [number, number, number, THREE.Color][] = [];

  // Rock, with the rounded ledge where the wall turns into the dome (lit from below in
  // the 2019 shows).
  kit.add('rock', rockShell());
  kit.add('rock', new THREE.TorusGeometry(R - 0.02, 0.075, 10, 160).rotateX(Math.PI / 2), mtx([0, WALL_TOP + 0.03, AXIS_Z], [0, 0, 0], [1, 0.7, 1]));
  kit.add('facade', facade());
  // Ribbed rust arch round the opening.
  const lip = archOutline(0.15).map((p) => V(p.x, p.y, FRONT_Z - 0.06));
  const PITCH = 0.055;
  kit.add('rust', tube(new THREE.CatmullRomCurve3(lip), 260, 12, (_u, d) => 0.055 * (1 + 0.25 * Math.max(0, Math.sin((d / PITCH) * Math.PI * 2)) ** 3)));

  // Floor of the booth (the droid's pedestal stands on it) and the lit turntable ring.
  kit.add('floor', new THREE.CircleGeometry(R + 0.1, 96).rotateX(-Math.PI / 2), mtx([0, 0, AXIS_Z]));
  kit.add('darkMetal', new THREE.CylinderGeometry(0.4, 0.42, 0.025, 64), mtx([0, 0.0125, 0]));

  // Counter: the dark bar top between the facade and the droid. Its droid-side edge is an
  // arc round him (venue video: the counter curves), with a dark face below it; the guest
  // side meets the facade. Inset panel lines on the top.
  const CW = 2 * archHalfWidth(0) + 0.4;
  const half = CW / 2;
  const top = new THREE.Shape();
  top.moveTo(-half, -(FRONT_Z + 0.12));
  top.lineTo(half, -(FRONT_Z + 0.12));
  const ARC_N = 40;
  for (let i = 0; i <= ARC_N; i++) {
    const x = half - (i / ARC_N) * CW;
    top.lineTo(x, -counterBackZ(x));
  }
  top.closePath();
  const flat = (shape: THREE.Shape, depth: number, y0: number, bevel = 0) => {
    const g = new THREE.ExtrudeGeometry(shape, {
      depth, bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 2, curveSegments: 4,
    });
    g.rotateX(-Math.PI / 2);
    g.translate(0, y0, 0);
    return g;
  };
  kit.add('counter', flat(top, 0.035, COUNTER_TOP - 0.043, 0.008));
  const face = new THREE.Shape();
  for (let i = 0; i <= ARC_N; i++) {
    const x = -half + 0.05 + (i / ARC_N) * (CW - 0.1);
    if (i === 0) face.moveTo(x, -counterBackZ(x) - 0.012);
    else face.lineTo(x, -counterBackZ(x) - 0.012);
  }
  for (let i = ARC_N; i >= 0; i--) {
    const x = -half + 0.05 + (i / ARC_N) * (CW - 0.1);
    face.lineTo(x, -counterBackZ(x) - 0.09);
  }
  face.closePath();
  kit.add('counter', flat(face, COUNTER_TOP - 0.05, 0));
  for (const cx of [-0.62, 0, 0.62]) {
    const cz = FRONT_Z - 0.06, w = 0.5, d = 0.2;
    for (const [sx, sz, px, pz] of [[w, 0.008, 0, -d / 2], [w, 0.008, 0, d / 2], [0.008, d, -w / 2, 0], [0.008, d, w / 2, 0]]) {
      kit.add('darkMetal', new THREE.BoxGeometry(sx, 0.004, sz), mtx([cx + px, COUNTER_TOP + 0.001, cz + pz]));
    }
  }
  kit.add('counter', new THREE.BoxGeometry(6.4, 1.6, 0.1), mtx([0, COUNTER_TOP - 0.05 - 0.8, FRONT_Z + 0.06]));
  for (let i = -4; i <= 4; i++) {
    kit.add('darkMetal', new THREE.BoxGeometry(0.02, 0.9, 0.012), mtx([i * 0.55, COUNTER_TOP - 0.52, FRONT_Z + 0.115]));
  }
  // The deck on the counter to the droid's right (viewer's left): a cream box with a
  // rounded top and two sockets, and a violet glow on the counter under its front edge.
  const deckAt = mtx([-0.5, COUNTER_TOP + 0.028, counterBackZ(-0.5) + 0.1], [0, 0.3, 0]);
  kit.add('deck', new RoundedBoxGeometry(0.24, 0.056, 0.14, 2, 0.01), deckAt);
  kit.add('deck', new THREE.CylinderGeometry(0.05, 0.05, 0.22, 20, 1, false, -Math.PI / 2, Math.PI).rotateZ(Math.PI / 2), deckAt.clone().multiply(mtx([0, 0.02, -0.01], [0, 0, 0], [1, 0.8, 1])));
  for (const x of [-0.05, 0.05]) kit.add('rubber', new THREE.CircleGeometry(0.014, 16).rotateX(-Math.PI / 2), deckAt.clone().multiply(mtx([x, 0.061, -0.01])));

  machineryPanel(kit, grille, mats);

  // Racks either side, stepping back and angled in toward the droid.
  rack(kit, mtx([-0.76, 0, -0.03], [0, 0.62, 0]), 4, 3, glowSpots);
  rack(kit, mtx([0.76, 0, -0.03], [0, -0.62, 0]), 4, 3, glowSpots);

  // The CRT stage left (the droid's left, viewer's right) on a bracket: a chunky rust-brown
  // case, a blue-white circuit diagram, a bolt column and a three-lug hinge bar on top.
  const monAt = mtx([0.66, 0.52, 0.24], [0.06, -0.72, 0]);
  kit.add('crt', new RoundedBoxGeometry(0.21, 0.22, 0.17, 3, 0.022), monAt.clone().multiply(mtx([0, 0, -0.04])));
  kit.add('screen', new THREE.PlaneGeometry(0.15, 0.165), monAt.clone().multiply(mtx([-0.012, 0, 0.0465])));
  for (let k = 0; k < 4; k++) {
    kit.add('darkMetal', new THREE.CylinderGeometry(0.007, 0.007, 0.01, 8).rotateX(Math.PI / 2), monAt.clone().multiply(mtx([0.09, -0.075 + k * 0.05, 0.046])));
  }
  kit.add('darkMetal', new THREE.CylinderGeometry(0.011, 0.011, 0.21, 12).rotateZ(Math.PI / 2), monAt.clone().multiply(mtx([0, 0.138, -0.04])));
  for (const x of [-0.07, 0, 0.07]) kit.add('crt', new RoundedBoxGeometry(0.03, 0.04, 0.04, 2, 0.008), monAt.clone().multiply(mtx([x, 0.126, -0.04])));
  kit.add('darkMetal', new THREE.BoxGeometry(0.05, 0.3, 0.05), mtx([0.7, 0.26, 0.18], [0, -0.72, 0]));

  // The dark olive console in the right foreground, standing on the counter: its narrow,
  // hole-punched side faces the guests, the broad face (two recessed panels, a cluster of
  // hex nuts, a hose out of the corner) faces the room's right.
  const conAt = mtx([0.52, COUNTER_TOP + 0.15, 0.6], [0, Math.PI / 2 - 0.35, 0]);
  kit.add('olive', new RoundedBoxGeometry(0.26, 0.3, 0.12, 3, 0.02), conAt);
  for (const x of [-0.058, 0.058]) kit.add('darkMetal', new RoundedBoxGeometry(0.1, 0.19, 0.012, 2, 0.006), conAt.clone().multiply(mtx([x, 0.02, 0.058])));
  for (const [dx, dy] of [[0, 0], [0.03, 0], [0, -0.03], [0.03, -0.03]]) {
    kit.add('darkMetal', new THREE.CylinderGeometry(0.013, 0.013, 0.02, 6).rotateX(Math.PI / 2), conAt.clone().multiply(mtx([0.055 + dx, -0.085 + dy, 0.066])));
  }
  for (let k = 0; k < 5; k++) kit.add('rubber', new THREE.CircleGeometry(0.011, 12), conAt.clone().multiply(mtx([-0.1315, -0.1 + k * 0.05, 0], [0, -Math.PI / 2, 0])));
  kit.add('darkMetal', new THREE.CylinderGeometry(0.012, 0.012, 0.28, 12).rotateZ(Math.PI / 2), conAt.clone().multiply(mtx([0, 0.185, 0])));
  for (const x of [-0.09, 0, 0.09]) kit.add('olive', new RoundedBoxGeometry(0.03, 0.045, 0.036, 2, 0.008), conAt.clone().multiply(mtx([x, 0.168, 0])));
  const hose = [V(-0.08, -0.12, 0.06), V(-0.08, -0.12, 0.16), V(-0.02, -0.16, 0.3), V(0.06, -0.28, 0.4)].map((p) => p.applyMatrix4(conAt));
  kit.add('olive', duct(hose, 0.018));

  // Speakers: two outer, two inner, hanging and aimed at the room.
  const speakers: [number, number, number, number, number][] = [
    [-0.95, 1.0, 0.22, 1.0, 0.16], [-0.63, 1.04, -0.74, 0.32, 0.1],
    [0.95, 1.0, 0.22, -1.0, 0.16], [0.63, 1.04, -0.74, -0.32, 0.1],
  ];
  const ceil = (x: number, z: number) => ceilingY(Math.hypot(x, z - AXIS_Z)) + 0.12;
  const wallAt = (deg: number, y: number, inset = 0.1) => {
    const a = THREE.MathUtils.degToRad(deg);
    return V(Math.sin(a) * (R - inset), y, AXIS_Z - Math.cos(a) * (R - inset));
  };
  for (const [x, y, z, yaw, pitch] of speakers) {
    const m = mtx([x, y, z], [pitch, yaw, 0]);
    speaker(kit, m);
    // Two hanging wires from the bracket to the rock.
    for (const s of [-1, 1]) {
      const a = new THREE.Vector3(s * 0.08, 0.27, 0).applyMatrix4(m);
      kit.add('rubber', cable(a, V(a.x * 1.04 + s * 0.05, ceil(a.x, a.z), a.z - 0.03), 0.01, 0.004));
    }
    // Speaker cable drooping to the wall.
    const c0 = new THREE.Vector3(0, -0.1, -0.1).applyMatrix4(m);
    const deg = THREE.MathUtils.radToDeg(Math.atan2(x, -(z - AXIS_Z)));
    kit.add('rubber', cable(c0, wallAt(deg + Math.sign(x) * 12, 0.4), 0.12, 0.009));
  }

  // Cable bundles hanging from the dome: one down behind the droid's head to the panel
  // (as in the front photo), others drooping to the walls.
  for (let i = 0; i < 4; i++) {
    const o = (i - 1.5) * 0.018;
    const pts = [V(0.02 + o, ceil(0.02, -0.26), -0.26 + o), V(0.03 + o * 1.4, 1.1, -0.3), V(0.04 + o, 0.98, -0.38 + o), V(0.02 + o, PANEL_TOP + 0.05, -0.5), V(0.0 + o, PANEL_TOP, -0.58)];
    kit.add('rubber', tube(new THREE.CatmullRomCurve3(pts), 30, 6, () => 0.008));
  }
  // [ceiling x, ceiling z, wall angle (deg, 0 = behind), end height, sag]
  const drapes: [number, number, number, number, number][] = [
    [-0.55, 0.1, -100, 0.55, 0.25], [0.52, 0.12, 96, 0.6, 0.22],
    [-0.45, -0.75, -60, 0.8, 0.12], [0.5, -0.7, 64, 0.75, 0.14],
    [-0.75, 0.25, -120, 0.35, 0.2], [0.76, 0.22, 122, 0.35, 0.2],
  ];
  for (const [x0, z0, deg, y1, sag] of drapes) {
    for (let k = 0; k < 3; k++) {
      const a = V(x0 + k * 0.02, ceil(x0, z0), z0 + k * 0.015);
      kit.add('rubber', cable(a, wallAt(deg + k * 2, y1 - k * 0.05, 0.12), sag + k * 0.05, 0.006 + k * 0.001));
    }
  }

  // Flexible ducting climbing the outer sides of the racks.
  kit.add('duct', duct([V(-1.12, 0.03, 0.3), V(-1.03, 0.3, 0.14), V(-0.98, 0.52, -0.12), V(-1.02, 0.62, -0.34)], 0.055));
  kit.add('duct', duct([V(-1.2, 0.03, 0.05), V(-1.1, 0.28, -0.1), V(-1.06, 0.4, -0.3)], 0.04));
  kit.add('duct', duct([V(1.1, 0.03, 0.3), V(1.02, 0.28, 0.12), V(0.98, 0.5, -0.12), V(1.0, 0.58, -0.3)], 0.048));

  // Emissive practicals: small readouts, the pedestal ring, the deck's underglow.
  // (Kit keeps only position/normal/uv; the practicals need vertex colours, so they merge separately.)
  const cast = new Set(['panel', 'cabinet', 'darkMetal', 'tan', 'rubber', 'duct', 'counter', 'crt', 'olive', 'deck']);
  const group = kit.build(mats, cast);
  group.name = 'booth';

  const glowGeo: THREE.BufferGeometry[] = [];
  const addGlow = (g: THREE.BufferGeometry, rgb: [number, number, number]) => {
    g = g.index ? g.toNonIndexed() : g;
    for (const k of Object.keys(g.attributes)) if (k !== 'position') g.deleteAttribute(k);
    const col = new Float32Array(g.attributes.position.count * 3);
    for (let i = 0; i < g.attributes.position.count; i++) col.set(rgb, i * 3);
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    glowGeo.push(g);
  };
  for (const [x, y, z, c] of glowSpots) {
    addGlow(new THREE.BoxGeometry(0.03, 0.012, 0.004).applyMatrix4(mtx([x, y, z], [0, Math.atan2(x, 3), 0])), [c.r * 0.8, c.g * 0.8, c.b * 0.8]);
  }
  // Pedestal ring: a teal-green light ring round the droid's base (the panorama's glowing turntable).
  addGlow(new THREE.RingGeometry(0.33, 0.37, 72).rotateX(-Math.PI / 2).translate(0, 0.026, 0), [0.08, 0.6, 0.42]);
  // Deck underglow: a thin violet strip on the counter under the deck's front edge.
  addGlow(new THREE.PlaneGeometry(0.22, 0.018).rotateX(-Math.PI / 2).applyMatrix4(deckAt.clone().multiply(mtx([0, -0.027, 0.075]))), [0.5, 0.3, 1.1]);
  const glow = new THREE.Mesh(mergeGeometries(glowGeo), mats.glow);
  glowGeo.forEach((g) => g.dispose());
  glow.name = 'booth_practicals';
  glow.frustumCulled = false; // it also drives the light desk (onBeforeRender, below)
  group.add(glow);
  scene.add(group);

  // ---------------------------------------------------------------- fixtures
  // Every fixture is created at unit white and a nominal intensity (its level at dimmer
  // 1); the light desk writes colour x dimmer into light.color. 8 runtime lights in all -
  // one more than the previous rig (the hanging lamp and its glow went, two head rims
  // came in).
  const fixtures: Partial<Record<Group, THREE.Light[]>> = {};
  const fx = <T extends THREE.Light>(g: Group, l: T) => {
    (fixtures[g] ??= []).push(l);
    scene.add(l);
    const t = (l as unknown as THREE.SpotLight).target;
    if (t) scene.add(t);
    return l;
  };
  // Droid key: amber on the torso and the front of the arms, a tight spot from the room,
  // high enough that his shadow falls behind the torso rather than across the panel. The
  // only shadow caster.
  const key = fx('droid_key', new THREE.SpotLight(0xffffff, 110, 5, 0.24, 0.5, 2));
  key.position.set(-0.2, 1.3, 1.45);
  key.target.position.set(0, 0.5, 0.05);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.near = 0.8;
  key.shadow.camera.far = 3.2;
  key.shadow.bias = -0.0003;
  key.shadow.normalBias = 0.006;
  key.shadow.radius = 4;
  // Head rims: narrow blue-violet beams from each side, raking the ear cups.
  for (const [g, sx] of [['head_rim_l', -1], ['head_rim_r', 1]] as const) {
    const rim = fx(g, new THREE.SpotLight(0xffffff, 9, 2.5, 0.2, 0.55, 2));
    rim.position.set(sx * 0.66, 0.98, 0.08);
    rim.target.position.set(sx * 0.04, 0.8, 0.0);
  }
  // Room uplight: low behind the machinery panel, so the walls take most of it and the
  // dome (4x farther) stays dim, and up both side walls.
  const back = fx('back_uplight', new THREE.PointLight(0xffffff, 4.5, 2.4, 2));
  back.position.set(0, 0.3, PANEL_Z - 0.34);
  for (const [g, sx] of [['wall_wash_l', -1], ['wall_wash_r', 1]] as const) {
    const w = fx(g, new THREE.SpotLight(0xffffff, 5.5, 2.6, 0.95, 0.9, 2));
    w.position.set(sx * 0.85, 0.42, 0.4);
    w.target.position.set(sx * 1.3, 1.05, -0.35);
  }
  // The spot on the ceiling centre above him (2019 opening: blue-white), from low at the
  // front so the beam clears his head.
  const ceilingSpot = fx('ceiling_down', new THREE.SpotLight(0xffffff, 6, 3, 0.55, 0.8, 2));
  ceilingSpot.position.set(0, 0.62, 0.72);
  ceilingSpot.target.position.set(0, 1.5, -0.3);
  // Fill from the room, through the arch, from the guests' left.
  const fill = fx('fill', new THREE.SpotLight(0xffffff, 4, 6, 0.3, 0.8, 2));
  fill.position.set(-1.25, 1.2, 2.5);
  fill.target.position.set(0, 0.62, 0);

  // Rack washes (shader-side, see WASH): from in front of and above each rack.
  const WASH_GAIN = 0.55;
  WASH.uWashSrc.value[0].set(-0.3, 0.95, 0.55);
  WASH.uWashCtr.value[0].set(-0.9, 0.22, -0.1);
  WASH.uWashSrc.value[1].set(0.3, 0.95, 0.55);
  WASH.uWashCtr.value[1].set(0.9, 0.22, -0.1);
  WASH.uWashRad.value[0] = WASH.uWashRad.value[1] = 0.6;

  // Practicals: the emissive glow and the CRT follow the desk's practicals dimmer. Their
  // spill (monitor blue, pedestal teal, deck violet) exists only as bake-time lights -
  // small and local, not worth a runtime light each.
  const screenBase = (mats.screen as THREE.MeshBasicMaterial).color.clone();
  const spill: THREE.PointLight[] = [];
  for (const [c, i, d, p] of [
    [0x3f8cff, 0.2, 0.8, [0.62, 0.52, 0.32]], [0x2affb0, 0.12, 0.6, [0, 0.06, 0.36]], [0x7a48ff, 0.08, 0.5, [-0.5, COUNTER_TOP + 0.02, 0.55]],
  ] as [number, number, number, [number, number, number]][]) {
    const l = new THREE.PointLight(c, i, d, 2);
    l.position.set(...p);
    spill.push(l);
  }

  /** Write a desk output (linear flux per group) onto the fixtures, washes and practicals. */
  const setFixtures = (out: Record<Group, RGB>) => {
    for (const g of GROUPS) for (const l of fixtures[g] ?? []) l.color.setRGB(out[g][0], out[g][1], out[g][2]);
    WASH.uWashCol.value[0].setRGB(out.rack_wash_l[0], out.rack_wash_l[1], out.rack_wash_l[2]).multiplyScalar(WASH_GAIN);
    WASH.uWashCol.value[1].setRGB(out.rack_wash_r[0], out.rack_wash_r[1], out.rack_wash_r[2]).multiplyScalar(WASH_GAIN);
    const p = out.practicals;
    (mats.glow as THREE.MeshBasicMaterial).color.setRGB(p[0], p[1], p[2]);
    (mats.screen as THREE.MeshBasicMaterial).color.setRGB(screenBase.r * p[0], screenBase.g * p[1], screenBase.b * p[2]);
  };
  const zeros = () => Object.fromEntries(GROUPS.map((g) => [g, [0, 0, 0]])) as unknown as Record<Group, RGB>;

  // ---------------------------------------------------------------- bounce + reflections
  // Probe grid over the open volume of the booth (inside the rock, above the floor).
  // Kept clear of the panel, racks, counter and speaker cabinets: a probe inside solid
  // geometry sees black and would darken everything near it.
  const grid = new LightProbeGridWebGL(0.84, 0.84, 1.2, 5, 3, 4);
  grid.position.set(0, 0.78, 0.15);
  scene.add(grid);
  // ---------------------------------------------------------------- the light desk
  const q = new URLSearchParams(location.search);
  let env: { tex: THREE.Texture; lum: number } | undefined;
  let basisReady = false;
  const lights = new BoothLights(
    { rig: q.get('rig') ?? undefined, cue: q.get('cue') ?? undefined },
    (d) => {
      setFixtures(d.out);
      if (basisReady) combine(d.out);
      if (env) scene.environmentIntensity = THREE.MathUtils.clamp(roomLum(d.out) / env.lum, 0.3, 2);
    },
    (rig) => {
      env = envs.get(rig) ?? env;
      if (env) scene.environment = env.tex;
    },
  );

  // 1. The whole rig at the desk's first cue, baked once - exactly what the booth used to
  //    do, and exact for that cue. The result is copied out of the grid's atlas so the
  //    basis bakes below can reuse the atlas while this copy is on screen.
  const tStart = Date.now();
  // Bakes render the booth, which fires the desk's render hook: hold it off meanwhile.
  lights.paused = true;
  setFixtures(lights.out);
  scene.add(...spill);
  grid.bake(renderer, scene, { cubemapSize: 16, near: 0.03, far: 6, bounces: 1 });
  spill.forEach((l) => l.removeFromParent());
  const atlas = (grid as unknown as { _renderTarget: THREE.WebGL3DRenderTarget })._renderTarget;
  const firstBake = atlas.clone();
  renderer.initRenderTarget(firstBake);
  renderer.copyTextureToTexture(atlas.texture, firstBake.texture, new THREE.Box3(new THREE.Vector3(), new THREE.Vector3(atlas.width, atlas.height, atlas.depth)));
  grid.texture = firstBake.texture as unknown as typeof grid.texture;

  // Environment (specular only while the grid is present): the booth seen from the head,
  // captured once per rig preset at its initial cue - the rigs differ in overall colour;
  // cues within a rig mostly differ in level, which environmentIntensity follows.
  const roomLum = (out: Record<Group, RGB>) => {
    let s = 0;
    for (const g of ['wall_wash_l', 'wall_wash_r', 'back_uplight', 'ceiling_down', 'fill', 'rack_wash_l', 'rack_wash_r'] as Group[]) {
      s += 0.2126 * out[g][0] + 0.7152 * out[g][1] + 0.0722 * out[g][2];
    }
    return s;
  };
  const envs = new Map<string, { tex: THREE.Texture; lum: number }>();
  const pmrem = new THREE.PMREMGenerator(renderer);
  for (const rigName of Object.keys(RIGS)) {
    const d = new StageLights({ rig: rigName });
    setFixtures(d.out);
    const cap = pmrem.fromScene(scene, 0.01, 0.03, 8, { size: 128, position: V(0, 0.78, 0.25) });
    envs.set(rigName, { tex: cap.texture, lum: Math.max(1e-3, roomLum(d.out)) });
  }
  pmrem.dispose();
  env = envs.get(lights.rigPreset)!;
  scene.environment = env.tex;
  lights.paused = false;
  lights.sync(true);
  const syncMs = Date.now() - tStart;

  // 2. Basis grids, one per group that lights the booth (the head rims only touch the
  //    droid, who is not in the bake), baked in the background one per task with async
  //    readback, so they cost the page no blocking time. Until they land, the first bake
  //    stands in (identical at the first cue); after, the live grid follows every cue.
  const BASES: Group[] = ['droid_key', 'wall_wash_l', 'wall_wash_r', 'back_uplight', 'ceiling_down', 'fill', 'rack_wash_l', 'rack_wash_r', 'practicals'];
  const basis = new Map<Group, Float32Array>();
  const live = new Float32Array(atlas.width * atlas.height * atlas.depth * 4);
  const liveTex = new THREE.Data3DTexture(live, atlas.width, atlas.height, atlas.depth);
  liveTex.format = THREE.RGBAFormat;
  liveTex.type = THREE.FloatType;
  liveTex.minFilter = liveTex.magFilter = THREE.LinearFilter;
  liveTex.unpackAlignment = 1;
  // The atlas packs 9 SH coefficients x RGB (27 floats) into 7 RGBA sub-volumes, so each
  // float's colour channel is (4 * subVolume + component) % 3.
  const slicesPer = atlas.depth / 7;
  const channel = new Uint8Array(live.length);
  for (let i = 0; i < live.length; i++) {
    const zSlice = Math.floor(i / (4 * atlas.width * atlas.height));
    channel[i] = (4 * Math.floor(zSlice / slicesPer) + (i % 4)) % 3;
  }
  const combine = (out: Record<Group, RGB>) => {
    live.fill(0);
    for (const [g, b] of basis) {
      const w = out[g];
      if (w[0] === 0 && w[1] === 0 && w[2] === 0) continue;
      for (let i = 0; i < live.length; i++) live[i] += b[i] * w[channel[i]];
    }
    liveTex.needsUpdate = true;
    grid.texture = liveTex as unknown as typeof grid.texture;
  };
  const booth = new Set<THREE.Object3D>([group, grid, ...spill]);
  for (const ls of Object.values(fixtures)) for (const l of ls) booth.add(l).add((l as unknown as THREE.SpotLight).target ?? l);
  // ?still (the render harness) bakes them synchronously instead: the summed grid differs
  // from the single first bake by a few percent (the booth shader's soft knee is not
  // linear), and a screenshot must not depend on whether the bases landed in time.
  const blocking = q.has('still');
  const bakeBases = async () => {
    const t0 = Date.now();
    let cpu = 0;
    const reads: Promise<unknown>[] = [];
    for (const g of BASES) {
      if (!blocking) await new Promise((r) => setTimeout(r, 0));
      const tb = Date.now();
      lights.paused = true;
      // Only the booth: hide the droid (and his LED lights) if he has loaded meanwhile.
      const hidden = scene.children.filter((o) => o.visible && !booth.has(o));
      hidden.forEach((o) => (o.visible = false));
      const bg0 = scene.background, env0 = scene.environment;
      // Black background and no environment: both would be counted once per basis (the
      // environment is the lit booth itself; the first bake ran before it existed).
      scene.background = null;
      scene.environment = null;
      const out = zeros();
      out[g] = [1, 1, 1];
      setFixtures(out);
      if (g === 'practicals') scene.add(...spill);
      grid.bake(renderer, scene, { cubemapSize: 16, near: 0.03, far: 6, bounces: 1 });
      if (g === 'practicals') spill.forEach((l) => l.removeFromParent());
      const data = new Float32Array(live.length);
      const layer = atlas.width * atlas.height * 4;
      const prev = renderer.getRenderTarget();
      for (let z = 0; z < atlas.depth; z++) {
        renderer.setRenderTarget(atlas, z);
        const dst = data.subarray(z * layer, (z + 1) * layer);
        if (blocking) renderer.readRenderTargetPixels(atlas, 0, 0, atlas.width, atlas.height, dst);
        else reads.push(renderer.readRenderTargetPixelsAsync(atlas, 0, 0, atlas.width, atlas.height, dst));
      }
      renderer.setRenderTarget(prev);
      basis.set(g, data);
      scene.background = bg0;
      scene.environment = env0;
      hidden.forEach((o) => (o.visible = true));
      lights.paused = false;
      lights.sync(true); // back to the desk's look before the next frame
      cpu += Date.now() - tb;
    }
    if (reads.length) await Promise.all(reads);
    basisReady = true;
    firstBake.dispose();
    lights.sync(true);
    console.info(`[booth] light desk: first bake + ${envs.size} env captures ${syncMs} ms (blocking); ` +
      `${BASES.length} basis bakes ${cpu} ms CPU ${blocking ? 'blocking (?still)' : `in ${BASES.length} background tasks`}, ready after ${Date.now() - t0} ms`);
  };
  bakeBases().catch((e) => console.warn('[booth] basis bake failed; bounce stays at the first cue', e));
  // Self-driving: if nothing calls lights.update(dt), the desk advances on the render clock
  // (performance.now, which ?still virtualises), so setMode/goCue alone are enough.
  glow.onBeforeRender = () => lights.tick(performance.now() / 1000);
  (window as unknown as { __r3xStage?: BoothLights }).__r3xStage = lights;

  const target = new THREE.Vector3();
  const from = new THREE.Vector3();
  const probe = new THREE.Vector3();
  const seg = new THREE.Vector3();
  /** Is p a legal camera position looking at target? */
  const legal = (p: THREE.Vector3, tgt: THREE.Vector3) => {
    if (p.z >= FRONT_Z + 0.06) {
      // In the room: the sight line must pass through the arch, above the counter.
      if (p.y > 2.6) return false;
      const t = (p.z - FRONT_Z) / (p.z - tgt.z);
      seg.copy(p).lerp(tgt, t);
      return seg.y > COUNTER_TOP + 0.02 && seg.y < ARCH_TOP - 0.15 && Math.abs(seg.x) < archHalfWidth(seg.y) - 0.14;
    }
    // In the booth: clear of the rock, the dome, the counter, the panel and the racks.
    const r = Math.hypot(p.x, p.z - AXIS_Z);
    if (r > R - 0.22 || p.y < 0.08 || p.y > ceilingY(r) - 0.14) return false;
    if (p.z > counterBackZ(p.x) - 0.06 && p.y < COUNTER_TOP + 0.06) return false;
    if (p.x > 0.3 && p.x < 0.75 && p.z > 0.42 && p.y < COUNTER_TOP + 0.42) return false; // the olive console
    if (p.z < PANEL_Z + 0.1) return false;
    if (Math.abs(p.x) > 0.5 && p.y < 0.6 && p.z < 0.4) return false;
    return true;
  };

  return {
    booth: true,
    lights,
    cams: {
      full: [V(0.42, 0.82, 1.72), V(0, 0.58, 0)],
      face: [V(0.16, 0.84, 0.9), V(0, 0.77, 0.08)],
      arms: [V(0.1, 0.62, 1.25), V(0, 0.52, 0.1)],
      chest: [V(-0.52, 0.52, 0.4), V(-0.1, 0.45, 0.06)],
    },
    constrain(camera, tgt) {
      tgt.x = THREE.MathUtils.clamp(tgt.x, -0.7, 0.7);
      tgt.y = THREE.MathUtils.clamp(tgt.y, 0.25, 1.2);
      tgt.z = THREE.MathUtils.clamp(tgt.z, -0.4, 0.5);
      const p = camera.position;
      if (legal(p, tgt)) return;
      // Dolly in along the sight line to the farthest legal point (like a game camera
      // hitting a wall); if even that fails, leave it (the user is mid-drag).
      target.copy(tgt);
      from.copy(p);
      let lo = 0, hi = 1;
      for (let i = 0; i < 18; i++) {
        const mid = (lo + hi) / 2;
        probe.copy(target).lerp(from, mid);
        if (legal(probe, target)) lo = mid;
        else hi = mid;
      }
      if (lo > 0.02) p.copy(target).lerp(from, lo);
    },
  };
}

// ------------------------------------------------------------------ turntable (?booth=0)

/**
 * A procedural cantina for image-based light when there is no booth: a dark, warm room
 * with a few practicals. It is what metal and clearcoat reflect. Built once into a PMREM.
 */
function cantinaEnvironment(renderer: THREE.WebGLRenderer): THREE.Texture {
  const env = new THREE.Scene();
  const room = new THREE.Mesh(
    new THREE.BoxGeometry(14, 6, 14),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0.028, 0.02, 0.015), side: THREE.BackSide }),
  );
  room.position.y = 2.5;
  env.add(room);
  const panel = (w: number, h: number, rgb: [number, number, number], pos: [number, number, number], rotY = 0, rotX = 0) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(...rgb), side: THREE.DoubleSide }));
    m.position.set(...pos);
    m.rotation.set(rotX, rotY, 0);
    env.add(m);
  };
  panel(1.6, 1.0, [4.0, 2.7, 1.6], [1.8, 4.5, 2.2], 0, Math.PI / 2);
  panel(0.8, 0.8, [2.4, 1.6, 0.9], [-2.2, 4.5, 1.0], 0, Math.PI / 2);
  panel(0.25, 3.5, [0.15, 1.3, 1.6], [-6.9, 2.0, -1.0], Math.PI / 2);
  panel(3.0, 0.2, [2.2, 0.25, 0.7], [1.0, 2.6, -6.9]);
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

/** Clean stage for turntable views: tungsten key, teal and magenta rims, dark floor. */
function buildTurntable(renderer: THREE.WebGLRenderer, scene: THREE.Scene): StageSet {
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
  const rim = (color: number, intensity: number, pos: [number, number, number]) => {
    const l = new THREE.SpotLight(color, intensity, 7, 0.3, 0.9, 1.6);
    l.position.set(...pos);
    l.target.position.set(0, 0.6, 0);
    scene.add(l, l.target);
  };
  rim(0x5ab0ff, 12, [-2.2, 1.9, -1.9]);
  rim(0xff4f78, 8, [2.0, 1.5, -2.3]);
  const fill = new THREE.DirectionalLight(0x9fb8d8, 0.5);
  fill.position.set(-2, 1.2, 2.2);
  scene.add(fill);
  scene.add(new THREE.HemisphereLight(0x3a4658, 0x140d08, 0.25));

  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(4, 96),
    surface({ color: 0x100e0c, roughness: 0.9 }, { freq: 5, variation: 0.12, grime: 0.4, grimeColor: 0x080604, bump: 0.0005 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  return {
    booth: false,
    cams: {
      full: [V(0.9, 0.85, 1.9), V(0, 0.5, 0)],
      face: [V(0.16, 0.84, 0.9), V(0, 0.77, 0.08)],
      arms: [V(0.1, 0.6, 1.2), V(0, 0.5, 0.1)],
      chest: [V(-0.55, 0.5, 0.42), V(-0.1, 0.45, 0.06)],
    },
    constrain() {},
  };
}
