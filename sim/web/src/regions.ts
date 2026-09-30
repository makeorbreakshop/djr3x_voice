/**
 * Body regions for the Bench Rig tab: outputs and joints grouped as Head, Arms, Torso and
 * Lights & stage, derived from the Robot Profile rather than listed by hand.
 *
 * - A joint in the subtree of the head's root (the child of a torso ring whose own subtree
 *   holds `visor`/`head_*`) is Head.
 * - An arm root is any other child of a torso ring; its subtree is Arms. An actuator named
 *   after an arm (`heroarm` turns the ring that carries the hero arm) is Arms too.
 * - Everything else under the torso chain is Torso.
 * - Light groups on the face board are Head; other lights are Lights & stage.
 */

export const REGIONS = ['Head', 'Arms', 'Torso', 'Lights & stage'] as const;
export type Region = (typeof REGIONS)[number] | 'Other';

interface ProfileLike {
  joints: { name: string; parent?: string | null }[];
  actuators: { name: string; joints: Record<string, number> }[];
  lights: { name: string; driver: { type: string; board?: string } }[];
}

export class BodyRegions {
  private parent = new Map<string, string | null>();
  private jointRegion = new Map<string, Region>();
  private armPrefixes: string[] = [];

  constructor(private profile: ProfileLike) {
    for (const j of profile.joints) this.parent.set(j.name, j.parent ?? null);
    const children = (n: string) => profile.joints.filter((j) => j.parent === n).map((j) => j.name);
    const subtree = (n: string): string[] => [n, ...children(n).flatMap(subtree)];
    // Torso rings: the root and its descendants named like it (torso_lower -> torso_middle -> torso_top).
    const root = profile.joints.find((j) => !j.parent)?.name;
    const ringPrefix = root?.split('_')[0] ?? '';
    const rings = profile.joints.filter((j) => ringPrefix && j.name.startsWith(`${ringPrefix}_`)).map((j) => j.name);
    for (const r of rings) this.jointRegion.set(r, 'Torso');
    for (const r of rings) {
      for (const c of children(r)) {
        if (rings.includes(c)) continue;
        const tree = subtree(c);
        const head = tree.some((n) => n === 'visor' || n.startsWith('head_'));
        if (!head) this.armPrefixes.push(c.split('_')[0]);
        for (const n of tree) this.jointRegion.set(n, head ? 'Head' : 'Arms');
      }
    }
  }

  ofJoint(name: string): Region {
    return this.jointRegion.get(name) ?? 'Other';
  }

  /** An output (actuator or light group) by name. */
  ofOutput(name: string): Region {
    const light = this.profile.lights.find((l) => l.name === name);
    if (light) return light.driver.type === 'serial_led' && light.driver.board === 'face' ? 'Head' : 'Lights & stage';
    const a = this.profile.actuators.find((x) => x.name === name);
    if (!a) return 'Other';
    if (this.armPrefixes.some((p) => a.name.startsWith(p))) return 'Arms';
    const first = Object.keys(a.joints)[0];
    return first ? this.ofJoint(first) : 'Other';
  }

  /** The region of the actuator that drives `joint` (so joints sit with their outputs). */
  ofDrivenJoint(joint: string): Region {
    const a = this.profile.actuators.find((x) => joint in x.joints);
    return a ? this.ofOutput(a.name) : this.ofJoint(joint);
  }

  /** Names grouped by region, in REGIONS order (then Other), input order kept within a group. */
  group(names: string[], of: (n: string) => Region): [Region, string[]][] {
    const order: Region[] = [...REGIONS, 'Other'];
    return order.map((r) => [r, names.filter((n) => of(n) === r)] as [Region, string[]]).filter(([, l]) => l.length);
  }
}
