import * as THREE from 'three';
import type { JointDynamics } from './actuation/pipeline';

export interface JointSpec {
  name: string;
  parent: string | null;
  pivot: [number, number, number];
  axis: [number, number, number];
  min: number;
  max: number;
  /** Revolute joints are in degrees; prismatic (the head lift) in millimetres. */
  type?: 'revolute' | 'prismatic';
  note?: string;
}

export interface RigDoc {
  joints: JointSpec[];
  anchors: Record<string, number[]> & { mouth_kind?: string };
  dynamics: Record<string, JointDynamics>;
  chest_lights?: import('./chest').ChestLightSpec[];
  triangles: number;
}

/**
 * A joint in the 3D model. It has no dynamics of its own: its angle is whatever the
 * servo plant (actuation/pipeline.ts) says the output shaft is at.
 */
export class Joint {
  value = 0; // deg, or mm when prismatic
  readonly axis: THREE.Vector3;
  private readonly rest: THREE.Vector3;

  constructor(readonly spec: JointSpec, readonly node: THREE.Object3D) {
    this.axis = new THREE.Vector3(...spec.axis).normalize();
    this.rest = node.position.clone();
  }

  get prismatic() {
    return this.spec.type === 'prismatic';
  }

  set(v: number) {
    this.value = v;
    if (this.prismatic) this.node.position.copy(this.rest).addScaledVector(this.axis, v / 1000);
    else this.node.quaternion.setFromAxisAngle(this.axis, THREE.MathUtils.degToRad(v));
  }
}

export class Rig {
  readonly joints = new Map<string, Joint>();
  readonly anchors = new Map<string, THREE.Object3D>();
  private rod?: THREE.Mesh;
  private readonly tmpA = new THREE.Vector3();
  private readonly tmpB = new THREE.Vector3();

  constructor(readonly root: THREE.Object3D, readonly doc: RigDoc) {
    for (const spec of doc.joints) {
      const node = root.getObjectByName(`j_${spec.name}`);
      if (!node) throw new Error(`GLB is missing joint node j_${spec.name}`);
      this.joints.set(spec.name, new Joint(spec, node));
    }
    root.traverse((o) => {
      if (o.name.startsWith('a_')) this.anchors.set(o.name.slice(2), o);
    });
    this.buildPistonRod();
  }

  get(name: string) {
    const j = this.joints.get(name);
    if (!j) throw new Error(`unknown joint ${name}`);
    return j;
  }

  /** Pose the model from joint values (deg, or mm for prismatic joints). */
  apply(values: Map<string, number>) {
    for (const [name, v] of values) this.joints.get(name)?.set(v);
    this.updatePistonRod();
  }

  /**
   * The hero arm's actuator rod: a plain metal rod in the real build (the kit's HA_P_1
   * piston is cosmetic) from the HA_PJ_1 clevis on the body into the cylinder on the
   * forearm, re-aimed every frame so it slides as the shoulder moves.
   */
  private buildPistonRod() {
    const base = this.anchors.get('piston_base');
    if (!base) return;
    const geo = new THREE.CylinderGeometry(0.0032, 0.0032, 1, 12);
    geo.translate(0, 0.5, 0);
    const mat = new THREE.MeshStandardMaterial({ color: 0xc8ccd2, metalness: 0.9, roughness: 0.25 });
    this.rod = new THREE.Mesh(geo, mat);
    this.rod.castShadow = true;
    base.add(this.rod);
  }

  private updatePistonRod() {
    const base = this.anchors.get('piston_base');
    const end = this.anchors.get('piston_end');
    if (!this.rod || !base || !end) return;
    base.updateWorldMatrix(true, false);
    end.updateWorldMatrix(true, false);
    const p = base.worldToLocal(end.getWorldPosition(this.tmpA));
    const len = p.length();
    this.rod.scale.set(1, len + 0.03, 1);
    this.rod.quaternion.setFromUnitVectors(this.tmpB.set(0, 1, 0), p.normalize());
  }
}
