import { describe, expect, it } from 'vitest';
import {
  exposed, exteriorFinish, jointLabel, libraryParts, mechanismFinish, MATERIAL, motionSystems, movedBy, type LibraryItem, type TreeNode,
} from '../src/workbench/systems';
import type { MAssembly, MJoint, MPart } from '../src/workbench/manifest';
import * as THREE from 'three';
import { gearMatrix } from '../src/workbench/kinematics';
import { gearParts } from '../src/workbench/systems';

const part = (id: string, link: string, extra: Partial<MPart> = {}): MPart =>
  ({ id, name: id, class: 'mech', link, transform: { t: [0, 0, 0] }, mesh: '', bbox: [[0, 0, 0], [1, 1, 1]], ...extra });
const joint = (id: string, parent: string, child: string, extra: Partial<MJoint> = {}): MJoint => ({
  id, name: id, type: 'revolute', parent_link: parent, child_link: child, pivot: [0, 0, 0], axis: [0, 1, 0], unit: 'deg',
  limits: { min: -30, max: 30 }, profile_joint: id, ...extra,
});
const node = (key: string, asm: Partial<MAssembly>, children: TreeNode[] = []): TreeNode =>
  ({ key, asm: { id: key.split('/').pop()!, name: key, links: [], parts: [], joints: [], ...asm } as MAssembly, children });

// A column (lift then pan) carrying a head (tilt then roll, a visor on the head) and a ring whose
// servo hangs on the column, the shape of mech/out/r3x_droid with the column internals picked.
function droid() {
  const head = node('droid/column/head', {
    links: [{ id: 'neck', name: 'Neck', joint: null }, { id: 'cross', name: 'Cross', joint: 'head_tilt' },
      { id: 'head', name: 'Head', joint: 'head_roll' }, { id: 'visor', name: 'Visor', joint: 'visor' }],
    joints: [
      joint('head_tilt', 'neck', 'cross', { drive: { kind: 'push_rod_pair', servos: ['servo_l'], linkages: ['rod_l'] } }),
      joint('head_roll', 'cross', 'head'),
      joint('visor', 'head', 'visor', { name: 'Visor (the kit\'s, on a push rod)' }),
    ],
    linkages: [{ id: 'rod_l', kind: 'push_rod', servo: 'servo_l', horn: { link: 'head', centre: [0, 0, 0], axis: [1, 0, 0], radius: 1, zero_dir: [0, 1, 0], ball_offset: 0 }, ground: { link: 'neck', point: [0, 1, 0] }, rod_length: 1, parts: ['horn_l', 'rod_l'] }],
    parts: [part('coupler', 'neck'), part('cross_piece', 'cross'), part('plate', 'head'), part('servo_l', 'head', { class: 'servo' }),
      part('shell_top', 'head', { class: 'shell' }), part('h_v_1', 'visor'), part('horn_l', 'head'), part('rod_l', 'head')],
    mount: { parent_link: 'neck' },
  });
  const column = node('droid/column', {
    links: [{ id: 'column', name: 'Column', joint: null }, { id: 'carriage', name: 'Carriage', joint: 'head_lift' }, { id: 'neck', name: 'Neck', joint: 'head_pan' }],
    joints: [
      joint('head_lift', 'column', 'carriage', { type: 'prismatic', unit: 'mm', limits: { min: -37, max: 45 }, name: 'Head lift (rack)' }),
      joint('head_pan', 'carriage', 'neck', { name: 'Head pan' }),
    ],
    gears: [{ id: 'g_ring', joint: 'torso_lower', joint_assembly: 'ring', link: 'column', pivot: [0, 0, 0], axis: [0, 1, 0], deg_per_unit: 3.8, parts: ['ring_pinion'] }],
    parts: [part('post', 'column', { material: 'aluminium (6063)', class: 'hardware' }), part('ring_servo', 'column', { class: 'servo' }), part('ring_pinion', 'column'),
      part('pinion', 'carriage', { material: 'brass', class: 'hardware' }), part('neck_tube', 'neck', { class: 'hardware', name: 'Neck tube 26 mm' })],
  }, [head]);
  const ring = node('droid/ring', {
    links: [{ id: 'race', name: 'Race', joint: null }, { id: 'ring', name: 'Ring', joint: 'torso_lower' }, { id: 'arm', name: 'Arm', joint: 'poker_shoulder' }],
    joints: [
      joint('torso_lower', 'race', 'ring', { name: 'Lower ring', drive: { kind: 'gear', servos: ['ring_servo'], gears: ['column/g_ring'] } }),
      joint('poker_shoulder', 'ring', 'arm', { name: 'Poker arm shoulder' }),
      joint('fixed_one', 'ring', 'arm', { limits: { min: 0, max: 0 } }),
    ],
    parts: [part('ls_m_full', 'ring', { class: 'shell' }), part('sector', 'ring'), part('pa_w_1', 'arm', { class: 'shell' })],
  });
  return node('droid', {}, [column, ring]);
}

describe('build motion systems', () => {
  const root = droid();
  const systems = motionSystems(root);
  const sys = (id: string) => systems.find((s) => s.id === id)!;

  it('groups joints the way people name them, head down, and skips joints that cannot move', () => {
    expect(systems.map((s) => s.id)).toEqual(['head', 'visor', 'neck', 'lower_ring', 'poker_arm']);
    expect(sys('head').joints.map((j) => j.joint.id)).toEqual(['head_tilt', 'head_roll']);
    expect(sys('neck').owner.key).toBe('droid/column');
  });

  it("a system is its joints' links plus the fixed link they hang from, and its drive wherever it lives", () => {
    expect([...sys('neck').parts].sort()).toEqual(['neck_tube', 'pinion', 'post', 'ring_pinion', 'ring_servo']);
    // the visor's parent link belongs to the head system: only the visor's own link
    expect([...sys('visor').parts]).toEqual(['h_v_1']);
    // the ring's servo hangs on the column but drives the ring
    expect(sys('lower_ring').parts.has('ring_servo')).toBe(true);
    expect(sys('lower_ring').drive.has('ring_servo')).toBe(true);
    // and its pinion, declared on the column, turns with the ring (SCHEMA.md "Gear" joint_assembly)
    expect(sys('lower_ring').drive.has('ring_pinion')).toBe(true);
    expect(sys('head').drive).toEqual(new Set(['servo_l', 'horn_l', 'rod_l']));
    expect(sys('poker_arm').parts).toEqual(new Set(['pa_w_1']));
  });

  it('a joint moves everything downstream, through the sub-assemblies riding its links', () => {
    const column = root.children[0];
    const lift = movedBy(column, 'head_lift');
    expect(lift.has('pinion') && lift.has('neck_tube')).toBe(true);
    expect(lift.has('coupler') && lift.has('h_v_1')).toBe(true); // the head rides the pan hub
    expect(lift.has('post')).toBe(false);
    const tilt = movedBy(column.children[0], 'head_tilt');
    expect(tilt.has('coupler')).toBe(false);
    expect(tilt.has('h_v_1') && tilt.has('rod_l')).toBe(true);
    // a hidden (unpicked) sub-assembly is not moved
    expect(movedBy(column, 'head_pan', (n) => n.key !== 'droid/column/head').has('coupler')).toBe(false);
  });

  it('a gear turns about its own axle with its joint, found from the joint wherever it is declared', () => {
    const ring = root.children[1];
    const j = ring.asm.joints[0];
    expect(gearParts(root, ring, j)).toEqual(new Set(['ring_pinion']));
    expect(movedBy(ring, 'torso_lower', () => true, root).has('ring_pinion')).toBe(true);
    const g = { id: 'g', joint: 'x', link: 'l', pivot: [10, 0, 0] as [number, number, number], axis: [0, 1, 0] as [number, number, number], deg_per_unit: -2, parts: [] };
    const m = gearMatrix(g, 45); // -90 deg about +Y through (10, 0, 0)
    expect(new THREE.Vector3(10, 0, 0).applyMatrix4(m).distanceTo(new THREE.Vector3(10, 0, 0))).toBeLessThan(1e-9);
    const q = new THREE.Vector3(10, 0, 5).applyMatrix4(m);
    expect(q.x).toBeCloseTo(5, 9);
    expect(q.z).toBeCloseTo(0, 9);
  });

  it('short joint labels and units', () => {
    expect(jointLabel('Head tilt, servo gear on a fixed centre gear')).toBe('Head tilt');
    expect(jointLabel('Head lift (rack and pinion on a vertical MGN12 rail)')).toBe('Head lift');
  });
});

describe('build library and looks', () => {
  it('isolates a design: its nodes, deep or not, by part class', () => {
    const root = droid();
    const item: LibraryItem = { id: 'col', name: 'Column', by: 'Ours', picks: {}, nodes: ['column'], look: 'mechanism' };
    expect(libraryParts(item, root, () => true).parts.has('coupler')).toBe(false);
    expect(libraryParts({ ...item, deep: true }, root, () => true).parts.has('coupler')).toBe(true);
    const shells = libraryParts({ ...item, nodes: ['ring'], only: ['shell'] }, root, () => true).parts;
    expect([...shells].sort()).toEqual(['ls_m_full', 'pa_w_1']);
  });

  it('material-true mechanism colours, kit paint outside, and what shows from outside', () => {
    expect(mechanismFinish(part('a', 'x', { material: 'aluminium (6061-T6)', class: 'hardware' }))).toBe(MATERIAL.aluminium);
    expect(mechanismFinish(part('b', 'x', { material: 'brass', class: 'hardware' }))).toBe(MATERIAL.brass);
    expect(mechanismFinish(part('s', 'x', { class: 'servo' }))).toBe(MATERIAL.servo);
    expect(mechanismFinish(part('p', 'x', { material: 'PETG', printed: true }))).toBe(MATERIAL.printed);
    expect(exteriorFinish(part('ls_m_full', 'x', { class: 'shell' }), 'lower_ring').color).toBe(0xc55a1e); // paint_orange
    expect(exteriorFinish(part('head_top', 'x', { class: 'shell' }), 'hunter_head').color).toBe(0x3c3f44); // charcoal
    expect(exposed(part('h_v_1', 'x'))).toBe(true); // the kit visor, modelled as mechanism
    expect(exposed(part('neck_tube', 'x', { name: 'Neck tube 26 mm' }))).toBe(true);
    expect(exposed(part('coupler', 'x'))).toBe(false);
  });
});
