import * as THREE from 'three';
import restDoc from './show/rig_limits.json';

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
  /** Per-joint mass/inertia for the servo plant (read by the Rust performer). */
  dynamics?: Record<string, unknown>;
  chest_lights?: import('./chestlights').ChestLightSpec[];
  triangles: number;
}

/**
 * The canonical body frame (show/SPEC.md "Frame and zeros"): the kit model is exported in
 * a display pose, so the rest is applied here, once - `bodyYaw` turns the whole model so
 * the base's front is +Z, and each joint's `zeroOffset` is how far its part is turned from
 * the kit pose at value 0. `?rest=kit` shows the raw kit pose (look-dev comparisons).
 */
export interface RestPose {
  bodyYaw: number;
  zeroOffset: Record<string, number>;
}

export const KIT_POSE: RestPose = { bodyYaw: 0, zeroOffset: {} };

export const CANONICAL_REST: RestPose = {
  bodyYaw: restDoc.body_yaw,
  zeroOffset: Object.fromEntries(
    Object.entries(restDoc.joints as Record<string, { zero_offset?: number }>)
      .filter(([, j]) => j.zero_offset !== undefined)
      .map(([name, j]) => [name, j.zero_offset!]),
  ),
};

export function restFromUrl(search = location.search): RestPose {
  return new URLSearchParams(search).get('rest') === 'kit' ? KIT_POSE : CANONICAL_REST;
}

/**
 * A joint in the 3D model. It has no dynamics of its own: its angle is whatever the
 * performer's frames say the output shaft is at.
 */
export class Joint {
  value = 0; // deg, or mm when prismatic
  readonly axis: THREE.Vector3;
  private readonly rest: THREE.Vector3;
  /** Local rotation at value 0, and the axis it turns about in that rest frame. */
  private readonly restQuat: THREE.Quaternion;
  private spin: THREE.Vector3;
  /** Prismatic: the slide direction in the parent's frame. */
  private slide: THREE.Vector3;
  private readonly q = new THREE.Quaternion();

  constructor(
    readonly spec: JointSpec,
    readonly node: THREE.Object3D,
    /** deg: node angle = value + zeroOffset (revolute only). */
    readonly zeroOffset = 0,
  ) {
    this.axis = new THREE.Vector3(...spec.axis).normalize();
    this.rest = node.position.clone();
    this.restQuat = new THREE.Quaternion().setFromAxisAngle(this.axis, THREE.MathUtils.degToRad(zeroOffset));
    this.spin = this.axis.clone();
    this.slide = this.axis.clone();
  }

  get prismatic() {
    return this.spec.type === 'prismatic';
  }

  set(v: number) {
    this.value = v;
    if (this.prismatic) this.node.position.copy(this.rest).addScaledVector(this.slide, v / 1000);
    else this.node.quaternion.copy(this.restQuat).multiply(this.q.setFromAxisAngle(this.spin, THREE.MathUtils.degToRad(v)));
  }

  /**
   * Hang the joint's node from another parent, keeping where it is and the world axis it moves
   * about (call at the rest pose, world matrices current). Used when the rig's joint tree
   * differs from the model's (the Physical rig stands the head on the base, not the rings).
   */
  rehang(parent: THREE.Object3D) {
    const wq = new THREE.Quaternion();
    const world = this.slide.clone().applyQuaternion(this.node.parent!.getWorldQuaternion(wq)).normalize();
    parent.attach(this.node);
    this.rest.copy(this.node.position);
    this.restQuat.copy(this.node.quaternion);
    this.spin = world.clone().applyQuaternion(this.node.getWorldQuaternion(wq).invert());
    this.slide = world.clone().applyQuaternion(parent.getWorldQuaternion(wq).invert());
  }
}

/**
 * Head roll (Hunter's gimbal: roll about +Z, nested inside the tilt at the same pivot, the
 * gimbal centre). The kit model has no roll node, so when the GLB lacks `j_head_roll` one is
 * inserted between `j_head_tilt` and all of its children, at the tilt's pivot: rolling it
 * turns everything above the tilt (shell, visor, eyes, LEDs). A model that has the node is
 * used as is, and both end up with the same tree and the same spec.
 */
export function ensureHeadRoll(root: THREE.Object3D, doc: RigDoc): void {
  const tiltAt = doc.joints.findIndex((j) => j.name === 'head_tilt');
  const tilt = root.getObjectByName('j_head_tilt');
  if (tiltAt < 0 || !tilt) return;
  if (!root.getObjectByName('j_head_roll')) {
    const roll = new THREE.Group();
    roll.name = 'j_head_roll';
    roll.userData.synthetic = true;
    for (const c of [...tilt.children]) roll.add(c); // keeps each child's local transform
    tilt.add(roll);
  }
  if (!doc.joints.some((j) => j.name === 'head_roll')) {
    const lim = (restDoc.joints as Record<string, { min: number; max: number }>).head_roll;
    doc.joints.splice(tiltAt + 1, 0, {
      name: 'head_roll', parent: 'head_tilt', pivot: [...doc.joints[tiltAt].pivot], axis: [0, 0, 1],
      min: lim.min, max: lim.max, type: 'revolute',
      note: "Hunter's head mech: roll about +Z at the gimbal centre; + leans the crown to the droid's right",
    });
    for (const j of doc.joints) if (j.parent === 'head_tilt' && j.name !== 'head_roll') j.parent = 'head_roll';
  }
}

export class Rig {
  readonly joints = new Map<string, Joint>();
  readonly anchors = new Map<string, THREE.Object3D>();
  private rod?: THREE.Mesh;
  private spring?: THREE.Mesh;
  private static readonly SPRING_H = 0.055; // m, at head lift 0
  private readonly tmpA = new THREE.Vector3();
  private readonly tmpB = new THREE.Vector3();

  /**
   * `parents`: the active profile's joint tree (joint -> parent joint, null = the body). Where it
   * differs from the model's, the joint's node is re-hung at the rest pose, so the textured
   * model moves like the rig the performer runs.
   */
  constructor(
    readonly root: THREE.Object3D,
    readonly doc: RigDoc,
    readonly restPose: RestPose = CANONICAL_REST,
    parents?: Record<string, string | null>,
  ) {
    (root.getObjectByName('r3x_root') ?? root).rotation.y = THREE.MathUtils.degToRad(restPose.bodyYaw);
    ensureHeadRoll(root, doc);
    for (const spec of doc.joints) {
      const node = root.getObjectByName(`j_${spec.name}`);
      if (!node) throw new Error(`GLB is missing joint node j_${spec.name}`);
      this.joints.set(spec.name, new Joint(spec, node, spec.type === 'prismatic' ? 0 : restPose.zeroOffset[spec.name] ?? 0));
    }
    root.traverse((o) => {
      if (o.name.startsWith('a_')) this.anchors.set(o.name.slice(2), o);
    });
    this.buildPistonRod();
    this.buildNeckSpring();
    // Start at the rest pose (every joint 0), which is what Centres measures its zeros on.
    this.apply(new Map(doc.joints.map((j) => [j.name, 0])));
    root.updateMatrixWorld(true);
    if (parents) this.rehangTo(parents);
  }

  /** Re-hang joints whose parent in `parents` differs from the model's (rest pose). */
  private rehangTo(parents: Record<string, string | null>) {
    const base = this.doc.joints.find((j) => !j.parent);
    const body = base ? this.joints.get(base.name)!.node.parent : null;
    if (!body) return;
    const order: string[] = [];
    const seen = new Set<string>();
    const visit = (n: string) => {
      if (seen.has(n) || !(n in parents) || !this.joints.has(n)) return;
      seen.add(n);
      const p = parents[n];
      if (p) visit(p);
      order.push(n);
    };
    Object.keys(parents).forEach(visit);
    const moved: string[] = [];
    for (const n of order) {
      const spec = this.joints.get(n)!.spec;
      const want = parents[n] ?? null;
      if ((spec.parent ?? null) === want) continue;
      const target = want ? this.joints.get(want)?.node : body;
      if (!target) continue;
      const node = this.joints.get(n)!.node;
      let up: THREE.Object3D | null = target;
      while (up && up !== node) up = up.parent; // never hang a node under its own descendant
      if (up === node) continue;
      this.joints.get(n)!.rehang(target);
      spec.parent = want;
      moved.push(n);
    }
    if (moved.length) {
      this.root.updateMatrixWorld(true);
      this.rehung = moved;
    }
  }

  /** Joints re-hung to follow the active profile's tree (empty: the model's own). */
  rehung: string[] = [];

  get(name: string) {
    const j = this.joints.get(name);
    if (!j) throw new Error(`unknown joint ${name}`);
    return j;
  }

  /** Pose the model from joint values (deg, or mm for prismatic joints). */
  apply(values: Map<string, number>) {
    for (const [name, v] of values) this.joints.get(name)?.set(v);
    this.updatePistonRod();
    if (this.spring) {
      // The spring spans top cap -> neck collar, so it stretches with the head lift (mm).
      const lift = this.joints.get('head_lift')?.value ?? 0;
      this.spring.scale.y = Math.max(0.3, (Rig.SPRING_H + lift / 1000) / Rig.SPRING_H);
    }
  }

  /**
   * The head's (pan, tilt) in degrees that would face world point `p`, from the current pose
   * of the joints above the neck (the head rides the top ring). Pan 0 / tilt 0 is the rest:
   * with the rings at rest, pan 0 faces the base's front.
   */
  aimAt(p: THREE.Vector3): [number, number] | null {
    const pan = this.joints.get('head_pan');
    const tilt = this.joints.get('head_tilt');
    if (!pan?.node.parent || !tilt) return null;
    const local = pan.node.parent.worldToLocal(this.tmpA.copy(p));
    const dy = local.y - (pan.node.position.y + tilt.node.position.y);
    const yaw = THREE.MathUtils.radToDeg(Math.atan2(local.x, local.z));
    // Rotation about +X tips the face down, so looking up is negative tilt.
    const pitch = -THREE.MathUtils.radToDeg(Math.atan2(dy, Math.hypot(local.x, local.z)));
    return [yaw - pan.zeroOffset, pitch - tilt.zeroOffset];
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

  /**
   * The coil spring round the base of the neck (on the real droid and the Hasbro figure,
   * not in the printable kit). Sits on the top cap (TR_N, y ~0.608 m) and turns with the
   * neck; `apply` stretches it with the head lift.
   */
  private buildNeckSpring() {
    const parent = this.joints.get('head_pan')?.node;
    if (!parent) return;
    const turns = 5.5;
    const r = 0.021;
    const h = Rig.SPRING_H;
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= 160; i++) {
      const t = i / 160;
      const a = t * turns * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * r, t * h, Math.sin(a) * r));
    }
    const geo = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 220, 0.0032, 10, false);
    const mat = new THREE.MeshPhysicalMaterial({ color: 0x2c2f33, roughness: 0.45, metalness: 0.6, clearcoat: 0.2 });
    this.spring = new THREE.Mesh(geo, mat);
    this.spring.castShadow = true;
    this.spring.receiveShadow = true;
    // head_pan's frame is the torso axis at the origin; the top cap is at y ~0.608.
    this.spring.position.set(0, 0.6085, 0);
    parent.add(this.spring);
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
