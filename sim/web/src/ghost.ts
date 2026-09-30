/**
 * Disabled outputs, ghosted: the stage manager's output enables (`state.stage.outputs`,
 * Robot Profile actuator and light-group names) gate drivers, so a disabled actuator's
 * joint still moves in frames but the real one would not. The sim shows that by drawing
 * the joint's own meshes (not its children's) dimmed and desaturated, as if unpowered.
 * They stay opaque: Bench starts with every output off, and a see-through robot read as a
 * rendering bug.
 */
import * as THREE from 'three';
import type { Rig } from './rig';

interface ProfileLike {
  actuators: { name: string; joints: Record<string, number> }[];
}

export class Ghosts {
  /** Joint -> the meshes whose nearest joint ancestor it is. */
  private readonly meshes = new Map<string, THREE.Mesh[]>();
  /** Actuator name -> its joints. */
  private readonly joints = new Map<string, string[]>();
  private readonly ghostMat = new Map<THREE.Material, THREE.Material>();
  private readonly original = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
  private key = '';

  constructor(rig: Rig, profile: ProfileLike) {
    for (const a of profile.actuators) this.joints.set(a.name, Object.keys(a.joints));
    rig.root.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      for (let p: THREE.Object3D | null = m; p; p = p.parent) {
        if (p.name.startsWith('j_') && rig.joints.has(p.name.slice(2))) {
          const j = p.name.slice(2);
          (this.meshes.get(j) ?? this.meshes.set(j, []).get(j)!).push(m);
          break;
        }
      }
    });
  }

  /** Apply the output enables; unknown names (light groups) are ignored here. */
  apply(outputs: Record<string, boolean> | null) {
    const off = new Set<string>();
    for (const [name, on] of Object.entries(outputs ?? {})) if (!on) for (const j of this.joints.get(name) ?? []) off.add(j);
    const key = [...off].sort().join(',');
    if (key === this.key) return;
    this.key = key;
    for (const [m, mat] of this.original) m.material = mat;
    this.original.clear();
    for (const j of off) {
      for (const m of this.meshes.get(j) ?? []) {
        this.original.set(m, m.material);
        m.material = Array.isArray(m.material) ? m.material.map((x) => this.ghost(x)) : this.ghost(m.material);
      }
    }
  }

  private ghost(mat: THREE.Material): THREE.Material {
    let g = this.ghostMat.get(mat);
    if (!g) {
      g = mat.clone();
      const c = (g as THREE.MeshStandardMaterial).color;
      if (c) c.lerp(new THREE.Color(0x3a3f47), 0.65);
      this.ghostMat.set(mat, g);
    }
    return g;
  }
}
