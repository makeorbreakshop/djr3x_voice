/**
 * Middle-ring logic panel lights (renderer only): 33 pixels behind the three front panels -
 * per panel a column of 8 LED holes and 3 square windows over diffuser blocks. Pixel order
 * = strip order (rig.json "chest_lights"). What they show comes from the performer's chest
 * firmware (Rust, r3x-performer-core leds/chest.rs), as frames.
 */

import * as THREE from 'three';
import type { RGB } from './leds';

export interface ChestLightSpec {
  kind: 'dot' | 'window';
  pos: [number, number, number];
  normal: [number, number, number];
  w: number;
  h: number;
  panel: string;
}

/** Lights behind the panel openings, posed in the middle ring's frame. */
export class ChestLights {
  private readonly mats: THREE.MeshBasicMaterial[] = [];
  private readonly c = new THREE.Color();

  constructor(parent: THREE.Object3D, specs: ChestLightSpec[], private readonly gain = 3.5) {
    parent.updateWorldMatrix(true, false);
    const inv = new THREE.Matrix4().copy(parent.matrixWorld).invert();
    const z = new THREE.Vector3(0, 0, 1);
    for (const s of specs) {
      const geo = s.kind === 'dot'
        ? new THREE.CircleGeometry(0.0016, 12)
        : new THREE.PlaneGeometry(s.w * 0.95, s.h * 0.95);
      const mat = new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false });
      const m = new THREE.Mesh(geo, mat);
      m.position.set(...s.pos).applyMatrix4(inv);
      m.quaternion.setFromUnitVectors(z, new THREE.Vector3(...s.normal).normalize());
      parent.add(m);
      this.mats.push(mat);
    }
  }

  update(pixels: RGB[]) {
    pixels.forEach((p, i) => {
      // Addressable LEDs at the same global brightness as the face (128/255). Channel
      // values are PWM duty, i.e. linear light (see leds.ts ledColor).
      this.c.setRGB((p[0] / 255) * 0.5, (p[1] / 255) * 0.5, (p[2] / 255) * 0.5, THREE.LinearSRGBColorSpace);
      this.mats[i]?.color.copy(this.c).multiplyScalar(this.gain);
    });
  }
}
