import { describe, expect, it } from 'vitest';
import { SHOW_FILES } from '../src/show/loader';
import { GENERATED, MECH, jointMatrices, subtrees } from '../src/mechrig/model';
import { clipLoads, RULE, TorqueModel } from '../src/mechrig/torque';
import { clipTracks, torqueMarks } from '../src/mechrig/lint';
import type { ClipDoc } from '../src/studio/model';

const CLIPS = Object.entries(SHOW_FILES).filter(([k]) => k.startsWith('show/clips/')).map(([, v]) => JSON.parse(v) as ClipDoc);
const clip = (id: string) => CLIPS.find((c) => c.id === id)!;

describe('mech model (robot.generated.json)', () => {
  it('stacks the head on the central column, per the mech model (lift carries the pan)', () => {
    expect(MECH.joints.head_lift.parent).toBeNull();
    expect(MECH.joints.head_pan.parent).toBe('head_lift');
    expect(MECH.joints.head_tilt.parent).toBe('head_pan');
    expect(subtrees().get('head_lift')).toEqual(new Set(['head_lift', 'head_pan', 'head_tilt', 'head_roll', 'visor']));
    expect(subtrees().get('head_pan')).toEqual(new Set(['head_pan', 'head_tilt', 'head_roll', 'visor']));
  });

  it('poses links through the chain (lift raises the visor pivot)', () => {
    const jm = jointMatrices({ head_lift: 10 });
    const p = [0, 0, 0];
    const v = MECH.joints.visor.pivot;
    const m = jm.get('visor')!.elements;
    p[1] = m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13];
    expect(p[1]).toBeCloseTo(v[1] + 10, 6);
  });

  it('carries the profile limits the generator derived', () => {
    // the column's pan: +-90 in the droid (hard), the animation range pulled inside it
    const pan = GENERATED.joints.find((j) => j.name === 'head_pan')!;
    expect(MECH.joints.head_pan.mech_limits.max).toBe(90);
    expect(pan.animation.max).toBeLessThan(MECH.joints.head_pan.mech_limits.max);
    expect(pan.animation.max).toBeGreaterThan(34); // wider than the old 4:1 neck gear
    expect(MECH.joints.head_lift.mech_limits).toEqual({ min: -37, max: 45 });
  });
});

describe('servo torque', () => {
  it('at rest: only gravity, and the vertical axes carry none', () => {
    const tm = new TorqueModel();
    const loads = tm.update({}, 0);
    const by = Object.fromEntries(loads.map((l) => [l.servo, l]));
    expect(Math.abs(by.col_pan_servo.nm)).toBeLessThan(1e-6);
    expect(Math.abs(by.col_top_servo.nm)).toBeLessThan(1e-6);
    // the lift holds everything on the carriage: m g x pulley lever / efficiency
    const above = MECH.links.filter((l) => l.joint && subtrees().get('head_lift')!.has(l.joint)).reduce((s, l) => s + l.kg, 0);
    const lift = MECH.drives.find((d) => d.servo === 'col_lift_servo')!;
    const expected = (above * 9.80665 * lift.mm_per_servo_deg! * (180 / Math.PI)) / 1000 / lift.eta;
    expect(by.col_lift_servo.nm).toBeCloseTo(expected, 4);
    for (const l of loads) expect(Number.isFinite(l.load)).toBe(true);
  });

  it('a pan acceleration costs I alpha about the pan axis (1:1 on the column)', () => {
    const tm = new TorqueModel();
    tm.smoothing = 0;
    const alpha = 400; // deg/s^2
    const h = 0.01;
    let last = tm.update({}, 0);
    for (let i = 1; i <= 3; i++) last = tm.update({ head_pan: 0.5 * alpha * (i * h) ** 2 }, i * h);
    const pan = last.find((l) => l.servo === 'col_pan_servo')!;
    // I_yy of every link above the pan about the (vertical, through the origin) axis
    let I = 0;
    for (const l of MECH.links) {
      if (!l.joint || !subtrees().get('head_pan')!.has(l.joint)) continue;
      const [x, , z] = l.com!.map((c) => c / 1000);
      I += l.inertia![1] + l.kg * (x * x + z * z);
    }
    const drive = MECH.drives.find((d) => d.servo === 'col_pan_servo')!;
    const expected = (I * alpha * (Math.PI / 180)) / drive.servo_deg_per_unit! / drive.eta;
    const weight = MECH.links.filter((l) => l.joint && subtrees().get('head_pan')!.has(l.joint)).reduce((s, l) => s + l.kg, 0) * 9.80665;
    const friction = (0.02 * weight * 0.11) / drive.servo_deg_per_unit! / drive.eta;
    expect(pan.nm).toBeCloseTo(expected + friction, 3);
  });

  it('the head gimbal servos share tilt and roll through the rods', () => {
    const tm = new TorqueModel();
    const [l, r] = ['servo_l', 'servo_r'].map((s) => tm.update({ head_tilt: 15 }, 0).find((x) => x.servo === s)!);
    expect(Number.isFinite(l.nm) && Number.isFinite(r.nm)).toBe(true);
    const J = tm.jacobian(['hunter_head/rod_l', 'hunter_head/rod_r'], ['head_tilt', 'head_roll'], {})!;
    expect(Math.sign(J[0][0])).toBe(-Math.sign(J[1][0])); // tilt: horns opposite
    expect(Math.sign(J[0][1])).toBe(Math.sign(J[1][1])); // roll: horns together
  });
});

describe('clip loads (Studio lint)', () => {
  it('scores head_cant and beat_bop, and every committed clip stays finite', () => {
    const out: Record<string, string> = {};
    for (const doc of CLIPS) {
      const loads = clipLoads(clipTracks(doc), doc.duration);
      for (const l of loads) expect(Number.isFinite(l.peak)).toBe(true);
      out[doc.id] = loads.slice(0, 2).map((l) => `${l.servo} ${(l.peak * 100).toFixed(0)}%`).join(', ');
    }
    // eslint-disable-next-line no-console
    console.log('peak servo loads per clip:\n' + Object.entries(out).map(([k, v]) => `  ${k}: ${v}`).join('\n'));
    expect(out.head_cant).toBeTruthy();
    expect(out.beat_bop).toBeTruthy();
  });

  it('marks a clip that breaks the 70 % rule', () => {
    const doc = clip('beat_bop');
    // the same bop, 10x faster: accelerations x100
    const fast: ClipDoc = { ...doc, id: 'fast_bop', duration: 0.05, tracks: Object.fromEntries(Object.entries(doc.tracks).map(([k, t]) => [k, { ...t, keys: t.keys.map(([u, v]) => [u / 10, v * 3] as [number, number]) }])) };
    const marks = torqueMarks(fast);
    expect(marks.length).toBeGreaterThan(0);
    expect(marks[0].message).toMatch(/70 %/);
    expect(torqueMarks(doc).every((m) => !m.message.includes(`> ${RULE}`))).toBe(true);
  });
});

describe('Build Servos view (actuator layer)', () => {
  // Hunter's gimbal from the generated mech block: tilt then roll about the gimbal centre, the
  // two push rods from the head's horns to the neck post (body frame, mm).
  const gimbal = () => {
    const piv = MECH.joints.head_tilt.pivot;
    const lk = (id: string) => {
      const l = MECH.linkages[`hunter_head/${id}`];
      return { ...l, horn: { ...l.horn, link: 'head' }, ground: { ...l.ground, link: 'neck' } };
    };
    const joint = (id: string, parent: string, child: string, axis: [number, number, number], lim: number) => ({
      id, name: id, type: 'revolute' as const, parent_link: parent, child_link: child, pivot: piv, axis, unit: 'deg',
      limits: { min: -lim, max: lim }, profile_joint: id,
      drive: { kind: 'push_rod_pair', servos: ['servo_l', 'servo_r'], linkages: ['rod_l', 'rod_r'] },
    });
    const asm = {
      id: 'hunter_head', name: 'g', links: [{ id: 'neck', name: 'n', joint: null }, { id: 'cross', name: 'c', joint: 'head_tilt' }, { id: 'head', name: 'h', joint: 'head_roll' }],
      joints: [joint('head_tilt', 'neck', 'cross', [1, 0, 0], 20), joint('head_roll', 'cross', 'head', [0, 0, 1], 12)],
      linkages: [lk('rod_l'), lk('rod_r')], parts: [],
    };
    return { asm, pose: {} as Record<string, number>, parent: null, children: [], links: new Map(), rodZero: new Map(), rods: new Map() } as never;
  };

  it('lists one slider per real actuator; the gimbal pair is two servos over two joints', async () => {
    const { actuators } = await import('../src/mechrig/servos');
    const acts = actuators([gimbal()]);
    expect(acts.map((a) => a.servo)).toEqual(['servo_l', 'servo_r']);
    expect(acts[0].joints.map((j) => j.id)).toEqual(['head_tilt', 'head_roll']);
  });

  it('gimbal L alone gives a combined tilt + roll; R stays where it was; round trip', async () => {
    const { actuators, servoAngle, solveServo } = await import('../src/mechrig/servos');
    const node = gimbal() as { pose: Record<string, number> };
    const [l, r] = actuators([node as never]);
    expect(Math.abs(servoAngle(l)!)).toBeLessThan(0.01); // built at rest
    const sol = solveServo(l, 10)!;
    expect(sol).not.toBeNull();
    expect(Math.abs(sol.head_tilt)).toBeGreaterThan(1);
    expect(Math.abs(sol.head_roll)).toBeGreaterThan(1);
    Object.assign(node.pose, sol);
    expect(servoAngle(l)!).toBeCloseTo(10, 2);
    expect(servoAngle(r)!).toBeCloseTo(0, 2);
    // then R the same way as L: the differential turns that mostly into roll
    Object.assign(node.pose, solveServo(r, 10)!);
    expect(Math.abs(node.pose.head_roll)).toBeGreaterThan(Math.abs(node.pose.head_tilt));
  });

  it('flags what the linkage cannot reach instead of clamping it', async () => {
    const { actuators, solveServo } = await import('../src/mechrig/servos');
    const [l] = actuators([gimbal()]);
    expect(solveServo(l, 170)).toBeNull();
  });
});
