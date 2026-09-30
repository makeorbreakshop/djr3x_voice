import { describe, expect, it } from 'vitest';
import { SHOW_FILES } from '../src/show/loader';
import { GENERATED, MECH, jointMatrices, subtrees } from '../src/mechrig/model';
import { clipLoads, RULE, TorqueModel } from '../src/mechrig/torque';
import { clipTracks, torqueMarks } from '../src/mechrig/lint';
import type { ClipDoc } from '../src/studio/model';

const CLIPS = Object.entries(SHOW_FILES).filter(([k]) => k.startsWith('show/clips/')).map(([, v]) => JSON.parse(v) as ClipDoc);
const clip = (id: string) => CLIPS.find((c) => c.id === id)!;

describe('mech model (robot.generated.json)', () => {
  it('stacks the head on the neck column, per the mech model', () => {
    expect(MECH.joints.head_pan.parent).toBeNull();
    expect(MECH.joints.head_lift.parent).toBe('head_pan');
    expect(MECH.joints.head_tilt.parent).toBe('head_lift');
    expect(subtrees().get('head_pan')).toEqual(new Set(['head_pan', 'head_lift', 'head_tilt', 'head_roll', 'visor']));
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
    const pan = GENERATED.joints.find((j) => j.name === 'head_pan')!;
    expect(pan.animation.max).toBeLessThan(34);
  });
});

describe('servo torque', () => {
  it('at rest: only gravity, and the vertical axes carry none', () => {
    const tm = new TorqueModel();
    const loads = tm.update({}, 0);
    const by = Object.fromEntries(loads.map((l) => [l.servo, l]));
    expect(Math.abs(by.pan_servo.nm)).toBeLessThan(1e-6);
    expect(Math.abs(by.top_servo.nm)).toBeLessThan(1e-6);
    // the lift holds everything above the slide: m g x pinion lever / efficiency
    const above = MECH.links.filter((l) => l.joint && ['head_lift', 'head_tilt', 'head_roll', 'visor'].includes(l.joint)).reduce((s, l) => s + l.kg, 0);
    const lift = MECH.drives.find((d) => d.servo === 'lift_servo')!;
    const expected = (above * 9.80665 * lift.mm_per_servo_deg! * (180 / Math.PI)) / 1000 / lift.eta;
    expect(by.lift_servo.nm).toBeCloseTo(expected, 4);
    for (const l of loads) expect(Number.isFinite(l.load)).toBe(true);
  });

  it('a pan acceleration costs I alpha about the pan axis (through 4:1)', () => {
    const tm = new TorqueModel();
    tm.smoothing = 0;
    const alpha = 400; // deg/s^2
    const h = 0.01;
    let last = tm.update({}, 0);
    for (let i = 1; i <= 3; i++) last = tm.update({ head_pan: 0.5 * alpha * (i * h) ** 2 }, i * h);
    const pan = last.find((l) => l.servo === 'pan_servo')!;
    // I_yy of every link above the pan about the (vertical, through the origin) axis
    let I = 0;
    for (const l of MECH.links) {
      if (!l.joint || !subtrees().get('head_pan')!.has(l.joint)) continue;
      const [x, , z] = l.com!.map((c) => c / 1000);
      I += l.inertia![1] + l.kg * (x * x + z * z);
    }
    const drive = MECH.drives.find((d) => d.servo === 'pan_servo')!;
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

describe('Build pad jog', () => {
  const mkWb = () => {
    const joint = (id: string, profile: string, min: number, max: number) =>
      ({ id, name: id, type: id === 'head_lift' ? 'prismatic' : 'revolute', parent_link: 'a', child_link: 'b', pivot: [0, 0, 0], axis: [0, 1, 0], unit: 'deg', limits: { min, max }, profile_joint: profile });
    const head = { asm: { id: 'head', joints: [joint('head_tilt', 'head_tilt', -20, 25), joint('visor', 'visor', -15, 30)] }, pose: {} as Record<string, number>, parent: null as unknown, children: [] };
    const alt = { asm: { id: 'alt', mount: { variant: { group: 'head_mech', id: 'alt', default: false } }, joints: [joint('head_tilt', 'head_tilt', -5, 5)] }, pose: {} as Record<string, number>, parent: null as unknown, children: [] };
    const neck = { asm: { id: 'neck', joints: [joint('head_pan', 'head_pan', -33.8, 33.8), joint('head_lift', 'head_lift', -37, 37)] }, pose: {} as Record<string, number>, parent: null, children: [head, alt] };
    head.parent = neck;
    alt.parent = neck;
    const nodes = [neck, head, alt];
    const wb = {
      active: true, top: neck, variants: {} as Record<string, string>, homes: 0,
      forEachNode(fn: (n: unknown) => void) { nodes.forEach(fn); },
      setJoint(n: { pose: Record<string, number> }, j: string, v: number) { n.pose[j] = v; },
      home() { this.homes++; nodes.forEach((n) => (n.pose = {})); },
    };
    return { wb, neck, head, alt };
  };
  const pad = (axes: number[], pressed: number[] = []) => {
    const buttons = Array(17).fill(0);
    for (const b of pressed) buttons[b] = 1;
    return { axes, buttons, intents: {}, mode: 'idle' } as unknown as import('../src/generated/PadFrame').PadFrame;
  };

  it('left stick pans at 60 % of v_max, clamped to the mech range; unfitted heads are skipped', async () => {
    const { PadJog } = await import('../src/mechrig/padjog');
    const { wb, neck, head, alt } = mkWb();
    const jog = new PadJog(wb as never);
    jog.feed(pad([1, 0, 0, 0]));
    let t = 0;
    jog.tick(t);
    for (let i = 0; i < 10; i++) jog.tick((t += 50));
    const v = GENERATED.joints.find((j) => j.name === 'head_pan')!.v_max;
    expect(neck.pose.head_pan).toBeCloseTo(0.6 * v * 0.5, 3);
    for (let i = 0; i < 100; i++) jog.tick((t += 50));
    expect(neck.pose.head_pan).toBe(33.8);
    jog.feed(pad([0, 1, 0, 0], [7]));
    jog.tick((t += 50));
    expect(head.pose.head_tilt).toBeGreaterThan(0); // stick down tips the face down
    expect(alt.pose.head_tilt ?? 0).toBe(0);
    expect(neck.pose.head_lift).toBeGreaterThan(0); // R2 lifts
    expect(jog.owns).toBe(true);
  });

  it('D-pad cycles Head -> Body -> each joint; triangle homes', async () => {
    const { PadJog } = await import('../src/mechrig/padjog');
    const { wb, head } = mkWb();
    const jog = new PadJog(wb as never);
    let t = 0;
    const press = (b: number) => {
      jog.feed(pad([0, 0, 0, 0], [b]));
      jog.tick((t += 20));
      jog.feed(pad([0, 0, 0, 0]));
      jog.tick((t += 20));
    };
    press(15); // Body has no joints here, so the next option is the first joint
    expect(jog.sel).toBe(1);
    press(15);
    press(15); // Head, head_pan, head_lift, head_tilt (no Body group: none of its joints here)
    jog.feed(pad([0, 0, 0, 0], [12]));
    for (let i = 0; i < 5; i++) jog.tick((t += 50));
    expect(head.pose.head_tilt).toBeGreaterThan(0);
    press(3);
    expect(wb.homes).toBe(1);
    expect(head.pose.head_tilt ?? 0).toBe(0);
  });
});
