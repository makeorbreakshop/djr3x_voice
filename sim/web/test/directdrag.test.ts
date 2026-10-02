import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import {
  couplingAt, couplingRangeA, jointChain, partDrive, pickChain, pointAngle, rayAngle, rayLineParam, rotaryValue, softLimit, solveIK, springStep, pickByDirection,
  type ChainNode, type Coupling,
} from '../src/workbench/drag';
import { gearMatrix, linkMatrices } from '../src/workbench/kinematics';
import type { MAssembly, Manifest } from '../src/workbench/manifest';
import { actuators, servoAngle, solveServo } from '../src/mechrig/servos';
import type { AsmNode } from '../src/workbench/workbench';

const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

// The built manifests (mech/out, local only: the tests on them skip when absent).
const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as { existsSync(u: URL): boolean; readFileSync(u: URL, enc: string): string };
const load = (id: string): MAssembly | null => {
  const u = new URL(`../../../mech/out/${id}/manifest.json`, import.meta.url);
  return fs.existsSync(u) ? (JSON.parse(fs.readFileSync(u, 'utf8')) as Manifest).root : null;
};
const HUNTER = load('hunter_head');
const COLUMN = load('column_internals');
const DROID = load('r3x_droid');
const built = !!(HUNTER && COLUMN);

describe('drag projections', () => {
  it('revolute: the swept angle in the plane square to the axis, like a door', () => {
    const axis = V(0, 1, 0), centre = V(0, 0, 0), u = V(1, 0, 0);
    // grabbed at +X; the cursor straight down onto +Z: a quarter turn the right-hand way about +Y is -90
    const ray = new THREE.Ray(V(0, 500, 100), V(0, -1, 0));
    const c = rayAngle(ray, centre, axis, u)!;
    expect(c).toBeCloseTo(-90, 6);
    expect(rotaryValue(10, 10, 0, c, 1)).toBeCloseTo(-80, 6);
    // a pinion turning 4 deg per joint deg: the joint moves a quarter of the sweep
    expect(rotaryValue(0, 0, 0, c, -4)).toBeCloseTo(22.5, 6);
    // the short way round past +-180, and from a value already part way
    expect(rotaryValue(170, 0, 0, -170, 1)).toBeCloseTo(190, 6);
    // the plane seen edge on: no answer (the caller drags along the screen instead)
    expect(rayAngle(new THREE.Ray(V(-500, 0, 50), V(1, 0, 0)), centre, axis, u)).toBeNull();
  });

  it('prismatic: the drag projected onto the axis line', () => {
    const origin = V(10, 0, 0), dir = V(0, 1, 0);
    const ray = new THREE.Ray(V(10, 25, 300), V(0, 0, -1));
    expect(rayLineParam(ray, origin, dir)).toBeCloseTo(25, 6);
    const skew = new THREE.Ray(V(-200, -40, 300), V(1, 0.2, -1.5).normalize());
    const s = rayLineParam(skew, origin, dir)!;
    // the closest point's offset is square to the line
    const t = skew.closestPointToPoint(origin.clone().addScaledVector(dir, s), new THREE.Vector3());
    expect(t.sub(origin.clone().addScaledVector(dir, s)).dot(dir)).toBeCloseTo(0, 6);
    expect(rayLineParam(new THREE.Ray(V(0, 0, 0), V(0, 1, 0)), origin, dir)).toBeNull();
  });

  it('limits: a tanh cushion into each end, never past it, the value as dragged inside', () => {
    expect(softLimit(0, -20, 7.5, 2.5)).toBe(0);
    expect(softLimit(4.9, -20, 7.5, 2.5)).toBe(4.9);
    for (const v of [7.5, 8, 12]) expect(softLimit(v, -20, 7.5, 2.5)).toBeLessThan(7.5);
    expect(softLimit(400, -20, 7.5, 2.5)).toBeLessThanOrEqual(7.5);
    expect(softLimit(400, -20, 7.5, 2.5)).toBeGreaterThan(7.499);
    expect(softLimit(-400, -20, 7.5, 2.5)).toBeGreaterThanOrEqual(-20);
    expect(softLimit(-21, -20, 7.5, 2.5)).toBeGreaterThan(-20);
    // monotone and smooth: no step anywhere, slope 1 at the knee
    let prev = -Infinity, slope = NaN;
    for (let v = -30; v <= 20; v += 0.05) {
      const y = softLimit(v, -20, 7.5, 2.5);
      expect(y).toBeGreaterThanOrEqual(prev);
      if (prev > -Infinity) {
        const s = (y - prev) / 0.05;
        if (!Number.isNaN(slope)) expect(Math.abs(s - slope)).toBeLessThan(0.03);
        slope = s;
      }
      prev = y;
    }
    expect(softLimit(30, -20, 25, 0)).toBe(25);
  });

  it('the shown pose follows the solve through a critically damped spring: quick, no overshoot', () => {
    let x = 0, v = 0;
    const seen: number[] = [];
    for (let i = 0; i < 30; i++) {
      [x, v] = springStep(x, v, 10, 72, 1 / 60);
      seen.push(x);
    }
    expect(Math.max(...seen)).toBeLessThanOrEqual(10 + 1e-9); // never past the target
    expect(seen[3]).toBeGreaterThan(8); // 4 frames (~67 ms): most of the way
    expect(seen.every((y, i) => i === 0 || y >= seen[i - 1])).toBe(true);
    // stable at a long frame
    [x, v] = springStep(0, 0, 10, 72, 0.5);
    expect(x).toBeGreaterThan(9.9);
    expect(x).toBeLessThanOrEqual(10);
  });

  it('direction lock: the joint whose motion matches the first pixels (either way), the nearer on a tie', () => {
    const tilt = { x: 0.2, y: -6 }, roll = { x: 5, y: 0.3 };
    expect(pickByDirection([roll, tilt], { x: 1, y: 8 })).toBe(1);
    expect(pickByDirection([roll, tilt], { x: -8, y: 1 })).toBe(0);
    expect(pickByDirection([roll, tilt], { x: 6, y: -5.5 })).toBe(0); // a near tie: the nearer joint
    expect(pickByDirection([{ x: 0, y: 0 }, tilt], { x: 3, y: 0 })).toBe(1); // one that does not move on screen never wins
    expect(pickByDirection([{ x: 0, y: 0 }], { x: 3, y: 0 })).toBe(-1);
  });

  it('coupled limits: the dependent range from the table, and the driver stops where the dependent would collide', () => {
    const fromDroid = (DROID as { couplings?: Coupling[] } | null)?.couplings?.find((c) => c.joint === 'head_tilt' && c.depends_on === 'head_lift');
    const c: Coupling = fromDroid ?? {
      joint: 'head_tilt', depends_on: 'head_lift',
      table: [[-37, -20, 15], [-32, -20, 15], [-27, -20, 20], [-22, -20, 20], [-17, -20, 20], [-12, -20, 25], [0, -20, 25], [45, -20, 25]],
    };
    expect(couplingAt(c, 0)).toEqual([-20, 25]);
    expect(couplingAt(c, -37)).toEqual([-20, 15]);
    expect(couplingAt(c, -60)).toEqual([-20, 15]); // past the table: its end
    const mid = couplingAt(c, -14.5)!;
    expect(mid[1]).toBeGreaterThan(20);
    expect(mid[1]).toBeLessThan(25);
    // the head tilted fully forward: the lift may come down only until the tilt range closes on it
    const [lo, hi] = couplingRangeA(c, 25, 0, -37, 45);
    expect(hi).toBe(45);
    expect(lo).toBeGreaterThan(-13);
    expect(lo).toBeLessThanOrEqual(-12);
    // level: the whole lift range
    expect(couplingRangeA(c, 0, 0, -37, 45)).toEqual([-37, 45]);
    // already outside at the current lift: nothing is trapped
    expect(couplingRangeA(c, 25, -30, -37, 45)).toEqual([-37, 45]);
  });

  it("Hunter's head: the tilt's coupled limit by roll narrows as the head rolls (the horn arm meets the head top)", () => {
    const all = [...((DROID as { couplings?: Coupling[] } | null)?.couplings ?? []), ...((HUNTER as { couplings?: Coupling[] } | null)?.couplings ?? [])];
    const c = all.find((x) => x.joint === 'head_tilt' && x.depends_on === 'head_roll');
    if (!c) return; // not built with Hunter's cut-down horns
    const level = couplingAt(c, 0)!;
    const rolled = couplingAt(c, 12)!;
    expect(rolled[1]).toBeLessThan(level[1]);
    expect(couplingAt(c, -12)![1]).toBeLessThan(level[1]);
    // halfway out, between the two
    const mid = couplingAt(c, 6)![1];
    expect(mid).toBeLessThanOrEqual(level[1]);
    expect(mid).toBeGreaterThanOrEqual(rolled[1]);
  });
});

describe('IK (damped least squares)', () => {
  // a two-joint gimbal, as Hunter's head: tilt about X, then roll about Z, both through the origin
  const gimbal = HUNTER ?? ({
    id: 'g', name: 'g', parts: [],
    links: [{ id: 'neck', name: 'neck', joint: null }, { id: 'cross', name: 'cross', joint: 'head_tilt' }, { id: 'head', name: 'head', joint: 'head_roll' }],
    joints: [
      { id: 'head_tilt', name: 'tilt', type: 'revolute', parent_link: 'neck', child_link: 'cross', pivot: [0, 0, 0], axis: [1, 0, 0], unit: 'deg', limits: { min: -20, max: 25 }, profile_joint: null },
      { id: 'head_roll', name: 'roll', type: 'revolute', parent_link: 'cross', child_link: 'head', pivot: [0, 0, 0], axis: [0, 0, 1], unit: 'deg', limits: { min: -12, max: 12 }, profile_joint: null },
    ],
  } as MAssembly);
  const grab = V(0, 150, 60);
  const fk = (q: number[]) => grab.clone().applyMatrix4(linkMatrices(gimbal.links, gimbal.joints, { head_tilt: q[0], head_roll: q[1] }).get('head')!);
  const lim = (id: string) => gimbal.joints.find((j) => j.id === id)!.limits;
  const J = [{ ...lim('head_tilt'), weight: 1 }, { ...lim('head_roll'), weight: 1 }];

  it("converges on the head's tilt and roll for a reachable point", () => {
    // well inside the head's limits (Hunter's cut-down horns: tilt to +7.5 only)
    const goal = [J[0].min * 0.6, J[1].min * 0.5];
    const q = solveIK(fk, [0, 0], J, fk(goal), { iters: 80 });
    expect(q[0]).toBeCloseTo(goal[0], 1);
    expect(q[1]).toBeCloseTo(goal[1], 1);
    expect(fk(q).distanceTo(fk(goal))).toBeLessThan(0.05);
  });

  it('follows the cursor in the screen plane (depth is free) and holds the limits', () => {
    const normal = V(0, 0, -1); // looking along -Z at the head
    const target = fk([Math.min(8, J[0].max * 0.7), 5]).add(V(0, 0, 40)); // the same screen point, nearer the camera
    const q = solveIK(fk, [0, 0], J, target, { iters: 80, normal });
    const e = target.clone().sub(fk(q));
    expect(Math.hypot(e.x, e.y)).toBeLessThan(0.1);
    // the head's own limits (the built manifest's when there is one: Hunter's cut-down horns narrow them)
    const far = solveIK(fk, [0, 0], J, fk([J[0].max - 0.1, J[1].max - 0.1]).add(V(0, 0, -200)), { iters: 80 });
    expect(far[0]).toBeLessThanOrEqual(J[0].max);
    expect(far[1]).toBeLessThanOrEqual(J[1].max);
    const out = solveIK(fk, [0, 0], J, V(0, -400, 400), { iters: 80 });
    expect(out[0]).toBeLessThanOrEqual(J[0].max + 1e-9);
    expect(out[0]).toBeGreaterThanOrEqual(J[0].min - 1e-9);
  });

  it('warm-started frame to frame, the solution moves continuously along a smooth cursor path', () => {
    let q = [0, 0];
    let worst = 0;
    const N = 60;
    for (let i = 1; i <= N; i++) {
      const t = i / N;
      const goal = [J[0].min * 0.7 * Math.sin(t * Math.PI), J[1].max * 0.8 * Math.sin(t * 2 * Math.PI)];
      const next = solveIK(fk, q, J, fk(goal), { iters: 8, maxStep: 4 });
      worst = Math.max(worst, Math.abs(next[0] - q[0]), Math.abs(next[1] - q[1]));
      q = next;
    }
    // the path's own step is under 1.6 deg a frame: the solve never jumps beyond a small multiple of it
    expect(worst).toBeLessThan(3);
    expect(fk(q).distanceTo(fk([J[0].min * 0.7 * Math.sin(Math.PI), 0]))).toBeLessThan(1);
  });

  it("direction lock on the head: down the screen picks tilt, across picks roll", () => {
    // seen from the front (+Z toward the camera): screen x = world x, screen y = -world y
    const scr = (p: THREE.Vector3) => ({ x: p.x, y: -p.y });
    const at = fk([0, 0]);
    const motion = [[0.5, 0], [0, 0.5]].map(([dt, dr]) => {
      const p = fk([dt, dr]);
      return { x: (scr(p).x - scr(at).x) / 0.5, y: (scr(p).y - scr(at).y) / 0.5 };
    });
    expect(pickByDirection(motion, { x: 0.5, y: 8 })).toBe(0); // tilt
    expect(pickByDirection(motion, { x: 8, y: 0.5 })).toBe(1); // roll
  });

  it('a handle drags its one joint: the ring on tilt turns tilt alone, by the swept angle', () => {
    const tilt = gimbal.joints.find((j) => j.id === 'head_tilt')!;
    const axis = V(...tilt.axis), pivot = V(...tilt.pivot);
    // the ring's handle point, and the cursor where that point would be 5 degrees on
    const onRing = V(0, 0, 40);
    const centre = pivot.clone().addScaledVector(axis, onRing.clone().sub(pivot).dot(axis));
    const moved = onRing.clone().sub(centre).applyAxisAngle(axis.clone().normalize(), (5 * Math.PI) / 180).add(centre);
    const ray = new THREE.Ray(moved.clone().addScaledVector(axis, 300), axis.clone().negate());
    const c = rayAngle(ray, centre, axis, onRing.clone().sub(centre))!;
    const v = rotaryValue(0, 0, 0, c, 1);
    expect(v).toBeCloseTo(5, 6);
    // only tilt: the grabbed point now sits where the cursor is, roll untouched
    const p = onRing.clone().applyMatrix4(linkMatrices(gimbal.links, gimbal.joints, { head_tilt: v }).get('cross')!);
    expect(p.distanceTo(moved)).toBeLessThan(1e-6);
  });

  it('weights favour the nearest joint', () => {
    // a small move the roll alone could make: with the tilt weighted down, the roll takes it
    const target = fk([0, 4]);
    const q = solveIK(fk, [0, 0], [{ ...J[0], weight: 0.1 }, { ...J[1], weight: 1 }], target, { iters: 3 });
    expect(Math.abs(q[1])).toBeGreaterThan(Math.abs(q[0]) * 10);
  });
});

describe.skipIf(!built)('what a grabbed part moves (built manifests)', () => {
  const column = { asm: COLUMN!, parent: null } as ChainNode;
  const mount = (COLUMN!.children as { ref?: string; mount?: MAssembly['mount'] }[]).find((c) => c.ref?.includes('hunter_head'))!.mount;
  const hunter = { asm: { ...HUNTER!, mount }, parent: column } as ChainNode;
  const ids = (c: { joint: { id: string } }[]) => c.map((x) => x.joint.id);

  it("the head's chain runs to the ground; by default the drag stops at the head's own joints", () => {
    const chain = jointChain(hunter, 'head');
    expect(ids(chain)).toEqual(['head_roll', 'head_tilt', 'head_pan', 'head_lift']);
    expect(ids(pickChain(chain, 'default'))).toEqual(['head_roll', 'head_tilt']);
    expect(ids(pickChain(chain, 'nearest'))).toEqual(['head_roll']);
    expect(ids(pickChain(chain, 'extend'))).toEqual(['head_roll', 'head_tilt', 'head_pan', 'head_lift']);
    // the neck (the head's ground link) rides the column's pan and lift
    expect(ids(pickChain(jointChain(hunter, 'neck'), 'default'))).toEqual(['head_pan', 'head_lift']);
    // the column itself is grounded
    expect(jointChain(column, 'column')).toEqual([]);
  });

  it("a servo horn drives tilt and roll through the push rods; its partner holds", () => {
    expect(partDrive(HUNTER!, 'horn_arm_l')).toEqual({ kind: 'linkage', linkage: 'rod_l', servo: 'servo_l' });
    expect(partDrive(HUNTER!, 'rod_r')).toEqual({ kind: 'linkage', linkage: 'rod_r', servo: 'servo_r' });
    expect(partDrive(HUNTER!, 'servo_l')).toBeNull(); // the body rides the head like any part
    const node = { asm: HUNTER!, pose: {} } as unknown as AsmNode;
    const acts = actuators([node]);
    const l = acts.find((a) => a.servo === 'servo_l')!;
    const r = acts.find((a) => a.servo === 'servo_r')!;
    expect(l.kind).toBe('pair');
    const r0 = servoAngle(r)!;
    // the horn grabbed and swept 15 degrees about its axis: the servo turns 15
    const deg = rotaryValue(servoAngle(l)!, servoAngle(l)!, 0, 15, 1);
    const sol = solveServo(l, deg)!;
    expect(sol).not.toBeNull();
    const pose = { ...sol };
    expect(servoAngle(l, pose)).toBeCloseTo(deg, 2);
    expect(servoAngle(r, pose)).toBeCloseTo(r0, 2);
    // one servo alone moves both: the head tilts and rolls
    expect(Math.abs(pose.head_tilt)).toBeGreaterThan(0.5);
    expect(Math.abs(pose.head_roll)).toBeGreaterThan(0.5);
    // then the other horn: from there it moves the head on, the first servo held where it was put
    const both = solveServo(r, r0 + 15, pose)!;
    expect(both).not.toBeNull();
    expect(servoAngle(l, both)).toBeCloseTo(deg, 2);
    expect(Math.hypot(both.head_tilt - pose.head_tilt, both.head_roll - pose.head_roll)).toBeGreaterThan(0.5);
  });

  it("Hunter's visor: either servo's horn drives the visor 1:1, the two servos turning opposite ways", () => {
    const vj = HUNTER!.joints.find((j) => j.id === 'visor')!;
    if (vj.drive?.kind !== 'direct') return; // built with Anderson's linkage (HUNTER_VISOR_DRIVE)
    expect(vj.drive.servos).toEqual(['visor_servo_l', 'visor_servo_r']);
    for (const [horn, servo, sign] of [['visor_horn_l', 'visor_servo_l', 1], ['visor_horn_r', 'visor_servo_r', -1]] as const) {
      const d = partDrive(HUNTER!, horn) as { kind: string; gear: NonNullable<MAssembly['gears']>[number] };
      expect(d.kind).toBe('gear');
      expect(d.gear.joint).toBe('visor');
      expect(d.gear.servo).toBe(servo);
      expect(d.gear.servo_deg_per_unit).toBe(sign);
      // the horn grabbed and swept 10 degrees about the visor axis: the visor turns 10
      expect(rotaryValue(0, 0, 0, 10, d.gear.deg_per_unit)).toBeCloseTo(10, 6);
    }
    const node = { asm: HUNTER!, pose: { visor: 12 } } as unknown as AsmNode;
    const acts = actuators([node]);
    const l = acts.find((a) => a.servo === 'visor_servo_l')!;
    const r = acts.find((a) => a.servo === 'visor_servo_r')!;
    expect(l.kind).toBe('direct');
    expect(servoAngle(l)).toBeCloseTo(12, 6);
    expect(servoAngle(r)).toBeCloseTo(-12, 6);
    // either servo slider moves the one visor joint
    expect(solveServo(l, 20)).toEqual({ visor: 20 });
    expect(solveServo(r, 20)).toEqual({ visor: -20 });
  });

  it('a pinion turns through its ratio: the lift pinion moves the lift, the gear following the cursor', () => {
    const d = partDrive(COLUMN!, 'col_lift_pinion');
    expect(d?.kind).toBe('gear');
    const g = (d as { gear: NonNullable<MAssembly['gears']>[number] }).gear;
    expect(g.joint).toBe('head_lift');
    // grabbed on the pinion's rim and swept +30 degrees about its axle
    const v = rotaryValue(0, 0, 0, 30, g.deg_per_unit);
    expect(v).toBeCloseTo(30 / g.deg_per_unit, 6);
    // the gear at that lift has turned the grabbed point by exactly the sweep
    const pivot = V(...g.pivot), axis = V(...g.axis);
    const u = new THREE.Vector3(1, 0, 0).cross(axis).lengthSq() > 1e-6 ? V(1, 0, 0) : V(0, 1, 0);
    const rim = pivot.clone().add(u.clone().addScaledVector(axis.clone().normalize(), -u.dot(axis.clone().normalize())).normalize().multiplyScalar(19));
    const turned = rim.clone().applyMatrix4(gearMatrix(g, v));
    expect(pointAngle(turned, pivot, axis, rim.clone().sub(pivot))).toBeCloseTo(30, 4);
    // the ring pinions drive the kit's rings in another assembly
    const ring = partDrive(COLUMN!, 'col_lower_pinion') as { gear: { joint: string; joint_assembly?: string } };
    expect(ring.gear.joint).toBe('torso_lower');
    expect(ring.gear.joint_assembly).toBe('lower_ring');
  });
});
