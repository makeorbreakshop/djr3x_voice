import * as THREE from 'three';
import { Rig } from './rig';

/** One LED: WS2812 channel values 0..255 (PWM duty, i.e. linear light). */
export type RGB = [number, number, number];
export const LEDS_PER_EYE = 7;
/** Eye strip: LEDs 0-6 the left eye, 7-13 the right (rex_face firmware). */
export const LEFT_EYE_START = 0;
export const RIGHT_EYE_START = 7;
export const NUM_MOUTH_LEDS = 8;
/** FastLED.setBrightness on the face board. */
export const OUTPUT_BRIGHTNESS = 128;

/**
 * Where the light physically comes from.
 *
 * Eyes: the kit's H_*Eye_4 part is the "Eye Distortion / Diffusion Bulb" (guide p.67/69),
 * a solid insert centred behind each louvred grille. The WS2812 7-LED jewel sits against
 * the back of the bulb, so the ring pattern shows *on the bulb* (THINKING's cyan dots
 * travel around it).
 *
 * Mouth: the build uses the "Mic-Mouth-Split" parts - a grille with through-slots, a
 * translucent light pipe whose bars sit in those slots, and a back mount holding the
 * 8-LED V behind the pipe. The LEDs light the pipe; the pipe glows through the slots.
 * (Without those parts the kit's solid H_M_1 is lit from inside instead.)
 *
 * Wiring assumptions - confirm against the real harness and flip here if needed:
 * - "left eye" (LEDs 0-6) is the droid's left (+X, the viewer's right).
 * - Jewel LED 0 is the centre; 1-6 run clockwise seen from the front, 1 at the top.
 * - Mouth V: 0 at the top of the viewer-left arm down to 3 at the tip, 4-7 back up the right.
 */
export const LEFT_EYE_IS_DROIDS_LEFT = true;
const JEWEL_RING_RADIUS = 0.0085; // 7-LED WS2812 jewel, ~23 mm board
/** Visual gain from LED light to emissive radiance; values > 1 feed the bloom. */
const LED_GAIN = 6;

/** Mouth V (left arm, top to tip) as fractions of the lit part's half-width / half-height. */
const MOUTH_V: [number, number][] = [[-0.82, 0.8], [-0.58, 0.3], [-0.34, -0.22], [-0.1, -0.74]];

/**
 * Linear LED light after FastLED.setBrightness(128). A WS2812 channel value is a PWM duty
 * cycle, so it is already linear light - decoding it as sRGB would dim a half-lit LED to
 * a fifth and push every mixed colour toward its dominant primary (the amber idle eyes
 * came out deep red), leaving nothing bright enough for the tone mapper to whiten the
 * core the way a camera sees a real LED.
 */
export function ledColor(rgb: RGB, out: THREE.Color): THREE.Color {
  const k = (OUTPUT_BRIGHTNESS + 1) / 256 / 255;
  return out.setRGB(rgb[0] * k, rgb[1] * k, rgb[2] * k, THREE.LinearSRGBColorSpace);
}

// ------------------------------------------------------------------ diffuser shader

const MAX_LEDS = 8;

interface DiffuserUniforms {
  uLedPos: { value: THREE.Vector3[] };
  uLedCol: { value: THREE.Color[] };
  uLedCount: { value: number };
  uLedSigma: { value: number };
}

/**
 * Light from point LEDs scattering through a diffusing part: each fragment gets
 * sum_i colour_i * exp(-d_i^2 / 2 sigma^2) added as emission. Cheap, and it makes the part
 * read as lit from inside rather than as a flat emissive colour.
 */
function makeDiffuser(base: THREE.Material, sigma: number): { material: THREE.Material; u: DiffuserUniforms } {
  const material = base.clone();
  const u: DiffuserUniforms = {
    uLedPos: { value: Array.from({ length: MAX_LEDS }, () => new THREE.Vector3()) },
    uLedCol: { value: Array.from({ length: MAX_LEDS }, () => new THREE.Color(0, 0, 0)) },
    uLedCount: { value: 0 },
    uLedSigma: { value: sigma },
  };
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vLedWorld;')
      .replace('#include <worldpos_vertex>',
        '#include <worldpos_vertex>\nvLedWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
varying vec3 vLedWorld;
uniform vec3 uLedPos[${MAX_LEDS}];
uniform vec3 uLedCol[${MAX_LEDS}];
uniform int uLedCount;
uniform float uLedSigma;`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
for (int i = 0; i < ${MAX_LEDS}; i++) {
  if (i >= uLedCount) break;
  vec3 d = vLedWorld - uLedPos[i];
  totalEmissiveRadiance += uLedCol[i] * exp(-dot(d, d) / (2.0 * uLedSigma * uLedSigma));
}`);
  };
  material.customProgramCacheKey = () => `led-diffuser-${sigma}`;
  return { material, u };
}

// ------------------------------------------------------------------ one lit part

class LitPart {
  private readonly u?: DiffuserUniforms;
  readonly light: THREE.PointLight;
  private readonly glowMat: THREE.SpriteMaterial;
  private readonly acc = new THREE.Color();
  private readonly c = new THREE.Color();

  constructor(
    private readonly anchor: THREE.Object3D,
    /** LED positions in the anchor's local frame. */
    private readonly local: THREE.Vector3[],
    diffuserMesh: THREE.Mesh | null,
    sigma: number,
    glowAt: THREE.Vector3,
    glowSize: number,
    lightRange: number,
    /** Emission gain: a diffusion bulb scatters the jewel's light through its whole body. */
    private readonly gain = LED_GAIN,
  ) {
    if (diffuserMesh) {
      const { material, u } = makeDiffuser(diffuserMesh.material as THREE.Material, sigma);
      diffuserMesh.material = material;
      this.u = u;
      u.uLedCount.value = local.length;
    }
    this.glowMat = new THREE.SpriteMaterial({
      map: glowTexture(), color: 0x000000, blending: THREE.AdditiveBlending,
      depthWrite: false, transparent: true, toneMapped: false,
    });
    const glow = new THREE.Sprite(this.glowMat);
    glow.scale.setScalar(glowSize);
    glow.position.copy(glowAt);
    anchor.add(glow);
    this.light = new THREE.PointLight(0x000000, 0, lightRange, 2);
    this.light.position.copy(glowAt);
    anchor.add(this.light);
  }

  update(colors: RGB[]) {
    this.acc.setRGB(0, 0, 0);
    this.anchor.updateWorldMatrix(true, false);
    colors.forEach((rgb, i) => {
      ledColor(rgb, this.c);
      this.acc.add(this.c);
      if (this.u) {
        this.u.uLedCol.value[i].copy(this.c).multiplyScalar(this.gain);
        this.u.uLedPos.value[i].copy(this.local[i]).applyMatrix4(this.anchor.matrixWorld);
      }
    });
    this.acc.multiplyScalar(1 / colors.length);
    this.glowMat.color.copy(this.acc).multiplyScalar(0.9 * (this.gain / LED_GAIN));
    this.light.color.copy(this.acc);
    this.light.intensity = Math.min(1, Math.max(this.acc.r, this.acc.g, this.acc.b)) * 0.4;
  }
}

let _glowTex: THREE.Texture | null = null;
function glowTexture() {
  if (_glowTex) return _glowTex;
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d')!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.35, 'rgba(255,255,255,0.3)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  _glowTex = new THREE.CanvasTexture(c);
  return _glowTex;
}

// ------------------------------------------------------------------ the face

function findMesh(root: THREE.Object3D, nodeName: string): THREE.Mesh | null {
  let hit: THREE.Mesh | null = null;
  root.getObjectByName(nodeName)?.traverse((o) => {
    if (!hit && (o as THREE.Mesh).isMesh) hit = o as THREE.Mesh;
  });
  return hit;
}

/** Bounding box of `mesh` in `frame`'s local coordinates (rest pose). */
function boxIn(frame: THREE.Object3D, mesh: THREE.Mesh): THREE.Box3 {
  mesh.geometry.computeBoundingBox();
  const m = new THREE.Matrix4().copy(frame.matrixWorld).invert().multiply(mesh.matrixWorld);
  return mesh.geometry.boundingBox!.clone().applyMatrix4(m);
}

export class FaceLeds {
  private readonly left: LitPart;
  private readonly right: LitPart;
  private readonly mouth: LitPart;

  constructor(rig: Rig) {
    rig.root.updateWorldMatrix(true, true);
    const anchor = (n: string) => {
      const o = rig.anchors.get(n);
      if (!o) throw new Error(`GLB is missing anchor a_${n}`);
      return o;
    };

    const eye = (side: 'L' | 'R') => {
      const a = anchor(`eye_${side}`);
      const bulb = findMesh(rig.root, `head_tilt__H_${side}Eye_4`);
      // Jewel against the back of the diffusion bulb (fallback: 30 mm behind the grille face).
      let back = -0.03;
      let front = -0.018;
      if (bulb) {
        const b = boxIn(a, bulb);
        back = b.min.z;
        front = b.max.z;
      }
      const z = back - 0.001;
      const pos = [new THREE.Vector3(0, 0, z)];
      for (let k = 0; k < 6; k++) {
        const t = (k * Math.PI) / 3;
        pos.push(new THREE.Vector3(Math.sin(t) * JEWEL_RING_RADIUS, Math.cos(t) * JEWEL_RING_RADIUS, z));
      }
      // sigma ~ bulb depth: the frosted bulb glows through its whole body, as a real
      // diffuser does, rather than only at the face nearest the jewel.
      return new LitPart(a, pos, bulb, 0.011, new THREE.Vector3(0, 0, front + 0.004), 0.05, 0.1, LED_GAIN * 3.5);
    };
    const [first, second] = LEFT_EYE_IS_DROIDS_LEFT ? (['L', 'R'] as const) : (['R', 'L'] as const);
    this.left = eye(first);
    this.right = eye(second);

    // a_mouth sits on the front face of the lit part (light pipe, or the solid grille).
    const pipe = rig.doc.anchors.mouth_kind?.toString() === 'light_pipe';
    const [w, h, d] = rig.doc.anchors.mouth_size;
    const z = pipe ? -d - 0.004 : -0.019;
    const arm = MOUTH_V.map(([x, y]) => new THREE.Vector3((x * w) / 2, (y * h) / 2, z));
    const mouthPos = [...arm, ...arm.map((p) => new THREE.Vector3(-p.x, p.y, p.z)).reverse()];
    const lit = findMesh(rig.root, pipe ? 'head_tilt__H_MOUTH_PIPE' : 'head_tilt__H_M_1');
    this.mouth = new LitPart(anchor('mouth'), mouthPos, lit, pipe ? 0.011 : 0.013,
      new THREE.Vector3(0, 0, 0.004), 0.06, 0.12);
  }

  /** Firmware pixels, as frames carry them: 14 eye LEDs, 8 mouth LEDs. */
  update(eyes: RGB[], mouth: RGB[]) {
    this.left.update(eyes.slice(LEFT_EYE_START, LEFT_EYE_START + LEDS_PER_EYE));
    this.right.update(eyes.slice(RIGHT_EYE_START, RIGHT_EYE_START + LEDS_PER_EYE));
    this.mouth.update(mouth.slice(0, NUM_MOUTH_LEDS));
  }
}
