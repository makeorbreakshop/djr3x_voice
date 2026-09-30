import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { CANONICAL_REST, KIT_POSE, Rig, type RigDoc } from '../src/rig';
import limits from '../src/show/rig_limits.json';

/** A bare kit-shaped node tree: r3x_root > torso_lower > torso_middle > torso_top > head_lift > head_pan > head_tilt (> visor, a shell mesh). */
function kitRig(rest = CANONICAL_REST, withRoll = false) {
  const root = new THREE.Group();
  const r3x = new THREE.Group();
  r3x.name = 'r3x_root';
  root.add(r3x);
  const chain = ['torso_lower', 'torso_middle', 'torso_top', 'head_lift', 'head_pan', 'head_tilt'];
  let parent: THREE.Object3D = r3x;
  for (const name of chain) {
    const n = new THREE.Group();
    n.name = `j_${name}`;
    if (name === 'head_tilt') n.position.set(0, 0.74, 0);
    parent.add(n);
    parent = n;
  }
  const tilt = parent;
  const visor = new THREE.Group();
  visor.name = 'j_visor';
  visor.position.set(0, 0.03, 0);
  const shell = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.1));
  shell.name = 'head_tilt__shell';
  shell.position.set(0, 0.08, 0);
  if (withRoll) {
    // A rebuilt model that carries its own roll node.
    const roll = new THREE.Group();
    roll.name = 'j_head_roll';
    tilt.add(roll);
    roll.add(visor, shell);
  } else tilt.add(visor, shell);
  const doc: RigDoc = {
    joints: chain.map((name, i): RigDoc['joints'][number] => ({
      name, parent: i ? chain[i - 1] : null, pivot: [0, 0, 0], min: -90, max: 90,
      axis: name === 'head_tilt' ? [1, 0, 0] : [0, 1, 0],
      ...(name === 'head_lift' ? { type: 'prismatic' as const } : {}),
    })).concat({ name: 'visor', parent: 'head_tilt', pivot: [0, 0.77, 0], axis: [1, 0, 0], min: -15, max: 30 }),
    anchors: {},
    triangles: 0,
  };
  return new Rig(root, doc, rest);
}

/** World yaw (deg, atan2(x, z) of the node's +Z). */
const yaw = (o: THREE.Object3D) => {
  const f = new THREE.Vector3(0, 0, 1).applyQuaternion(o.getWorldQuaternion(new THREE.Quaternion()));
  return THREE.MathUtils.radToDeg(Math.atan2(f.x, f.z));
};

describe('canonical rest (rig_limits.json)', () => {
  it('turns the kit so the stack faces the base front at all zeros', () => {
    const rig = kitRig();
    // The base (r3x_root) is turned by body_yaw; the top ring, and the head on it, face +Z.
    expect(yaw(rig.root.getObjectByName('r3x_root')!)).toBeCloseTo(limits.body_yaw, 9);
    expect(yaw(rig.get('torso_top').node)).toBeCloseTo(0, 1);
    expect(yaw(rig.get('head_pan').node)).toBeCloseTo(0, 9);
  });

  it('keeps values in joint units: a value turns the part from its rest', () => {
    const rig = kitRig();
    rig.apply(new Map([['torso_lower', 10]]));
    expect(yaw(rig.get('torso_lower').node)).toBeCloseTo(limits.body_yaw + CANONICAL_REST.zeroOffset.torso_lower + 10, 9);
    expect(yaw(rig.get('head_pan').node)).toBeCloseTo(10, 9);
  });

  it('only the rings are offset, and the kit pose is the identity', () => {
    expect(Object.keys(CANONICAL_REST.zeroOffset).sort()).toEqual(['torso_lower', 'torso_middle', 'torso_top']);
    const kit = kitRig(KIT_POSE);
    expect(yaw(kit.get('head_pan').node)).toBeCloseTo(0, 9);
    expect(yaw(kit.root.getObjectByName('r3x_root')!)).toBeCloseTo(0, 9);
  });

  it('aims from the rest: straight ahead is (0, 0), and pan follows the rings', () => {
    const rig = kitRig();
    const ahead = new THREE.Vector3(0, 0.74, 3);
    const [pan, tilt] = rig.aimAt(ahead)!;
    expect(pan).toBeCloseTo(0, 9);
    expect(tilt).toBeCloseTo(0, 9);
    // A point to the droid's left (+X) needs +pan; turning the top ring 20 deg that way leaves 70.
    expect(rig.aimAt(new THREE.Vector3(3, 0.74, 0))![0]).toBeCloseTo(90, 9);
    rig.apply(new Map([['torso_top', 20]]));
    expect(rig.aimAt(new THREE.Vector3(3, 0.74, 0))![0]).toBeCloseTo(70, 9);
    // Above the eyes is negative tilt (rotation about +X tips the face down).
    rig.apply(new Map([['torso_top', 0]]));
    expect(rig.aimAt(new THREE.Vector3(0, 3.74, 3))![1]).toBeCloseTo(-45, 9);
  });
});

describe('head roll (Hunter head mech)', () => {
  /** World x of a node's local +Y: where the crown leans (droid's left is +X). */
  const lean = (o: THREE.Object3D) => new THREE.Vector3(0, 1, 0).applyQuaternion(o.getWorldQuaternion(new THREE.Quaternion())).x;

  for (const withRoll of [false, true]) {
    it(`nests inside the tilt at its pivot and carries the whole head (${withRoll ? 'model has the node' : 'synthetic'})`, () => {
      const rig = kitRig(CANONICAL_REST, withRoll);
      const roll = rig.get('head_roll');
      expect(roll.node.parent?.name).toBe('j_head_tilt');
      expect(roll.node.position.length()).toBe(0);
      expect(roll.spec).toMatchObject({ parent: 'head_tilt', axis: [0, 0, 1], min: limits.joints.head_roll.min, max: limits.joints.head_roll.max });
      expect(rig.get('visor').spec.parent).toBe('head_roll');
      expect(rig.get('head_tilt').node.children).toEqual([roll.node]);
      const shell = rig.root.getObjectByName('head_tilt__shell')!;
      expect(shell.parent).toBe(roll.node);
      // +roll is right-handed about +Z: the crown leans to the droid's right (-X).
      rig.apply(new Map([['head_roll', 10]]));
      rig.root.updateMatrixWorld(true);
      expect(lean(shell)).toBeCloseTo(-Math.sin(THREE.MathUtils.degToRad(10)), 9);
      expect(lean(rig.get('visor').node)).toBeCloseTo(lean(shell), 9);
    });
  }
});
