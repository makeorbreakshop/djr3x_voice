/**
 * Middle-ring logic panel lights (renderer only): 33 pixels behind the three front panels -
 * per panel a column of 8 LED holes and 3 square windows over diffuser blocks. Pixel order
 * = strip order (rig.json "chest_lights"). What they show comes from the performer's chest
 * firmware (Rust, r3x-performer-core leds/chest.rs), as frames.
 */

import * as THREE from 'three';
import type { RGB } from './leds';
import type { Rig } from './rig';

export interface ChestLightSpec {
  kind: 'dot' | 'window';
  pos: [number, number, number];
  normal: [number, number, number];
  w: number;
  h: number;
  panel: string;
}

/**
 * Emissive colour of one small body LED: board brightness 128/255, then `gain`, hue kept but
 * the brightest channel capped at `cap`. Channel values are PWM duty, i.e. linear light (see
 * leds.ts ledColor). Bloom starts at 1.0 (post.ts), so the cap keeps a lit LED a coloured dot
 * or square with at most a small halo - uncapped, white and bright colours at gain 3-4 reach 2.0 in
 * every channel and bloom (at a quarter resolution) into white blobs larger than the panel.
 */
export function bodyLedColor(out: THREE.Color, p: RGB, gain: number, cap = BODY_LED_CAP): THREE.Color {
  out.setRGB((p[0] / 255) * 0.5 * gain, (p[1] / 255) * 0.5 * gain, (p[2] / 255) * 0.5 * gain, THREE.LinearSRGBColorSpace);
  const m = Math.max(out.r, out.g, out.b);
  if (m > cap) out.multiplyScalar(cap / m);
  // Bloom keys on luminance: a white or pale LED at the channel cap would still flare, so
  // its luminance stops just under the threshold (a saturated colour never gets near it).
  const y = 0.2126 * out.r + 0.7152 * out.g + 0.0722 * out.b;
  return cap !== Infinity && y > BODY_LED_LUMA ? out.multiplyScalar(BODY_LED_LUMA / y) : out;
}

/** Luminance ceiling of a body LED (post.ts blooms from 1.0). */
export const BODY_LED_LUMA = 0.95;

/** Brightest channel of a body LED (just over the bloom threshold: a glint, not a flare). */
export const BODY_LED_CAP = 1.3;

/** Lights behind the panel openings, riding the middle ring (`link`). */
export class ChestLights {
  private readonly mats: THREE.MeshBasicMaterial[] = [];
  private readonly meshes: THREE.Mesh[] = [];

  /** `specs` are in the model's kit frame (rig.json); see Rig.kitToLocal. */
  constructor(rig: Rig, link: string, specs: ChestLightSpec[], private readonly gain = 3.5) {
    const parent = rig.get(link).node;
    const z = new THREE.Vector3(0, 0, 1);
    for (const s of specs) {
      const geo = s.kind === 'dot'
        ? new THREE.CircleGeometry(0.0014, 12)
        : new THREE.PlaneGeometry(s.w * 0.9, s.h * 0.9);
      const mat = new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false });
      const m = new THREE.Mesh(geo, mat);
      m.name = `chest_led_${s.kind}`;
      m.position.copy(rig.kitToLocal(parent, s.pos));
      // Kit-frame directions are the link's own (joints carry no rotation at the kit pose).
      m.quaternion.setFromUnitVectors(z, new THREE.Vector3(...s.normal).normalize());
      parent.add(m);
      this.mats.push(mat);
      this.meshes.push(m);
    }
  }

  private shown = true;
  private covered = new Set<number>();

  /** Hidden while an electronics package brings its own body LEDs (electronics.ts). */
  setVisible(on: boolean) {
    this.shown = on;
    this.meshes.forEach((m, i) => (m.visible = on && !this.covered.has(i)));
  }

  /** Pixels drawn by a diffuser pane instead (electronics.ts Diffusers). */
  setCovered(indices: Iterable<number>) {
    this.covered = new Set(indices);
    this.setVisible(this.shown);
  }

  update(pixels: RGB[]) {
    pixels.forEach((p, i) => {
      const mat = this.mats[i];
      if (mat) bodyLedColor(mat.color, p, this.gain);
    });
  }
}
