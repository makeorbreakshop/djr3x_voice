/**
 * Build's navigation model, as pure functions over the assembly tree (no three.js):
 *
 * - Motion systems: the droid's joints grouped the way people think of him (Head, Visor, Neck,
 *   each ring, each arm), by profile joint, from whatever assemblies are fitted. A system's parts
 *   are the links its joints move, the fixed link they hang from (when no other joint moves it),
 *   joint-less sub-assemblies bolted to those links, and the servos and linkages that drive them,
 *   wherever those live (a ring's servo hangs on the column).
 * - What a joint moves: every part downstream of it, through links and mounted sub-assemblies
 *   (the head rides the column's pan hub), so hovering a joint shows what is connected.
 * - The library: each community design as published, isolated out of the droid's tree with
 *   the variant picks that make it present.
 * - Looks: material-true colours for the mechanism, the kit paint for the shells (palette.json,
 *   the same rules the visual model is baked with: sim/model/build_r3x.py MATERIAL_RULES).
 */

import palette from '../palette.json';
import type { MAssembly, MJoint, MPart, PartClass } from './manifest';

export type Look = 'exterior' | 'mechanism' | 'inspect';

/** The slice of a workbench node these functions read. `key` is unique (the path of ids):
 *  an assembly id may repeat (Hunter's head is an option under two internals variants). */
export interface TreeNode {
  key: string;
  asm: MAssembly;
  children: TreeNode[];
}

export interface SysJoint<N extends TreeNode = TreeNode> { node: N; joint: MJoint }

export interface MotionSystem<N extends TreeNode = TreeNode> {
  id: string;
  name: string;
  joints: SysJoint<N>[];
  /** The node whose steps, checks and BOM the panel shows for this system. */
  owner: N;
  /** Part ids drawn as the system. */
  parts: Set<string>;
  /** Of those, the actuators and linkage parts (tinted as the drive). */
  drive: Set<string>;
}

interface SysDef { id: string; name: string; joints: string[] }

/** By profile joint (else the joint id). Order = the list's order, head down. */
export const SYSTEM_DEFS: SysDef[] = [
  { id: 'head', name: 'Head', joints: ['head_tilt', 'head_roll'] },
  { id: 'visor', name: 'Visor', joints: ['visor'] },
  { id: 'neck', name: 'Neck', joints: ['head_lift', 'head_pan'] },
  { id: 'top_ring', name: 'Top ring', joints: ['torso_top'] },
  { id: 'hero_arm', name: 'Hero arm', joints: ['hero_shoulder', 'hero_elbow', 'hero_wrist'] },
  { id: 'middle_ring', name: 'Middle ring', joints: ['torso_middle'] },
  { id: 'throttle_arm', name: 'Throttle arm', joints: ['throttle_shoulder', 'throttle_elbow', 'throttle_wrist'] },
  { id: 'lower_ring', name: 'Lower ring', joints: ['torso_lower'] },
  { id: 'poker_arm', name: 'Poker arm', joints: ['poker_shoulder', 'poker_elbow', 'poker_wrist'] },
];

/** A joint name without its parenthetical or trailing clause ("Head tilt, servo gear on…"). */
export const jointLabel = (name: string) => name.replace(/\s*\([^)]*\)\s*/g, ' ').split(/,\s/)[0].trim();

/** An assembly name without its parentheticals ("Central column internals (default)"). */
export const assemblyLabel = (name: string) => name.replace(/\s*\([^)]*\)\s*/g, ' ').replace(/\s+/g, ' ').trim();

const moves = (j: MJoint) => j.type !== 'fixed' && j.limits.max > j.limits.min;

function walk<N extends TreeNode>(n: N, fn: (n: N) => void, shown: (n: N) => boolean) {
  if (!shown(n)) return;
  fn(n);
  for (const c of n.children as N[]) walk(c, fn, shown);
}

/** Every part id under `n` (its whole shown subtree). */
export function subtreeParts<N extends TreeNode>(n: N, shown: (n: N) => boolean = () => true): Set<string> {
  const out = new Set<string>();
  walk(n, (x) => x.asm.parts.forEach((p) => out.add(p.id)), shown);
  return out;
}

/** Links of `node` downstream of `link` (inclusive), through the node's own joints. */
function downstream(node: TreeNode, link: string): Set<string> {
  const out = new Set([link]);
  for (let grew = true; grew;) {
    grew = false;
    for (const j of node.asm.joints) {
      if (out.has(j.parent_link) && !out.has(j.child_link)) {
        out.add(j.child_link);
        grew = true;
      }
    }
  }
  return out;
}

/** Child nodes mounted on one of `links` of `node`. */
const mountedOn = <N extends TreeNode>(node: N, links: Set<string>) =>
  (node.children as N[]).filter((c) => links.has(c.asm.mount?.parent_link ?? ''));

const hasJoints = (n: TreeNode): boolean => n.asm.joints.some(moves) || n.children.some(hasJoints);

/** Parts of the gear entries a joint turns (SCHEMA.md "Gear"), wherever they are declared: the
 *  joint's `drive.gears` ("<assembly>/<gear>"), and any gear naming the joint (`joint_assembly`
 *  for one in another assembly: the column's ring pinions turn with the rings). */
export function gearParts<N extends TreeNode>(root: N | null, node: N, joint: MJoint, shown: (n: N) => boolean = () => true): Set<string> {
  const out = new Set<string>();
  const named = new Set(joint.drive?.gears ?? []);
  const visit = (n: N) => {
    for (const g of n.asm.gears ?? []) {
      const owner = g.joint_assembly ?? n.asm.id;
      if (named.has(`${n.asm.id}/${g.id}`) || (g.joint === joint.id && owner === node.asm.id)) g.parts.forEach((p) => out.add(p));
    }
  };
  walk(root ?? node, visit, shown);
  return out;
}

/** Part ids the joint moves: downstream links, the sub-assemblies riding them, the linkages
 *  (horn and rod) attached to any of them, and the gears it turns (`root`: where to look for them). */
export function movedBy<N extends TreeNode>(node: N, jointId: string, shown: (n: N) => boolean = () => true, root: N | null = null): Set<string> {
  const j = node.asm.joints.find((x) => x.id === jointId);
  const out = new Set<string>();
  if (!j) return out;
  gearParts(root, node, j, shown).forEach((p) => out.add(p));
  const links = downstream(node, j.child_link);
  for (const p of node.asm.parts) if (links.has(p.link)) out.add(p.id);
  for (const lk of node.asm.linkages ?? []) {
    if (links.has(lk.horn.link) || links.has(lk.ground.link)) lk.parts.forEach((p) => out.add(p));
  }
  for (const c of mountedOn(node, links)) subtreeParts(c, shown).forEach((p) => out.add(p));
  return out;
}

/** The motion systems of the fitted tree (only `shown` nodes; joints that cannot move skipped). */
export function motionSystems<N extends TreeNode>(root: N, shown: (n: N) => boolean = () => true): MotionSystem<N>[] {
  const nodes: N[] = [];
  walk(root, (n) => nodes.push(n), shown);
  const byId = new Map<string, MotionSystem<N>>();
  const order: string[] = [];
  for (const node of nodes) {
    for (const joint of node.asm.joints) {
      if (!moves(joint)) continue;
      const key = joint.profile_joint ?? joint.id;
      const def = SYSTEM_DEFS.find((d) => d.joints.includes(key));
      const id = def?.id ?? `${node.key}:${joint.id}`;
      let s = byId.get(id);
      if (!s) {
        s = { id, name: def?.name ?? jointLabel(joint.name), joints: [], owner: node, parts: new Set(), drive: new Set() };
        byId.set(id, s);
        order.push(id);
      }
      s.joints.push({ node, joint });
    }
  }
  // part id -> where it is (drive servos are found across the tree: the rings' hang on the column)
  const where = new Map<string, MPart>();
  for (const n of nodes) for (const p of n.asm.parts) if (!where.has(p.id)) where.set(p.id, p);
  for (const s of byId.values()) {
    for (const { node, joint } of s.joints) {
      const childLinks = new Set(node.asm.joints.map((x) => x.child_link));
      const links = new Set([joint.child_link]);
      if (!childLinks.has(joint.parent_link)) links.add(joint.parent_link);
      for (const p of node.asm.parts) if (links.has(p.link)) s.parts.add(p.id);
      // joint-less sub-assemblies bolted on (Anderson's ring drive on the ring's race)
      for (const c of mountedOn(node, links)) if (shown(c) && !hasJoints(c)) subtreeParts(c, shown).forEach((p) => s.parts.add(p));
      for (const sv of joint.drive?.servos ?? []) {
        if (where.has(sv)) {
          s.parts.add(sv);
          s.drive.add(sv);
        }
      }
      for (const p of gearParts(root, node, joint, shown)) {
        s.parts.add(p);
        s.drive.add(p);
      }
      for (const lid of joint.drive?.linkages ?? []) {
        const lk = node.asm.linkages?.find((l) => l.id === lid);
        for (const p of lk?.parts ?? []) {
          s.parts.add(p);
          s.drive.add(p);
        }
      }
    }
  }
  const rank = (id: string) => {
    const i = SYSTEM_DEFS.findIndex((d) => d.id === id);
    return i < 0 ? SYSTEM_DEFS.length + order.indexOf(id) : i;
  };
  return [...byId.values()].sort((a, b) => rank(a.id) - rank(b.id));
}

// ------------------------------------------------------------------ library

export interface LibraryItem {
  id: string;
  name: string;
  /** Who published it. */
  by: string;
  /** Variant picks that put it in the model (groups the manifest lacks are ignored). */
  picks: Record<string, string>;
  /** Assembly ids that make it up (the first shown node with each id). */
  nodes: string[];
  /** Include each node's whole subtree, not only its own parts. */
  deep?: boolean;
  only?: PartClass[];
  except?: PartClass[];
  look: Look;
}

/**
 * The published designs this droid is assembled from. Isolated from the droid's own tree, so
 * switching is instant and a design shows exactly as it is placed in our build.
 * (Manifest need, for the mech side: a `designs` list in the manifest would replace this table.)
 */
export const LIBRARY: LibraryItem[] = [
  { id: 'kit', name: 'Kit shells', by: 'Kit', picks: { internals: 'anderson_morton', head_mech: 'r3x_anderson', base_side_panels: 'closed' },
    nodes: ['base', 'base_panels_closed', 'lower_ring', 'middle_ring', 'top_ring', 'head_r3x'], only: ['shell'], look: 'exterior' },
  { id: 'anderson', name: 'R-3X Animation', by: 'Anderson', picks: { internals: 'anderson_morton', head_mech: 'r3x_anderson' },
    nodes: ['r3x_neck_drive', 'head_r3x', 'r3x_lower_drive', 'r3x_top_drive', 'r3x_neck_guide_race'], except: ['shell'], look: 'mechanism' },
  { id: 'hunter', name: 'Head gimbal', by: 'Hunter', picks: { internals: 'column' }, nodes: ['hunter_head'], deep: true, look: 'mechanism' },
  { id: 'morton', name: 'Lower cage', by: 'Sam Morton', picks: { internals: 'anderson_morton', base_frame: 'morton' }, nodes: ['morton_frame'], look: 'mechanism' },
  { id: 'randall', name: 'Printed frame', by: 'Riane Randall', picks: { internals: 'anderson_morton', base_frame: 'randall' }, nodes: ['randall_frame'], look: 'mechanism' },
  { id: 'mouth', name: 'Mic-Mouth-Split', by: 'Trevor Zaharichuk', picks: { internals: 'anderson_morton', head_mech: 'r3x_anderson', mouth: 'mic_mouth_split' },
    nodes: ['mouth_split'], look: 'mechanism' },
  { id: 'column', name: 'Central column', by: 'Ours', picks: { internals: 'column' }, nodes: ['column_internals'], look: 'mechanism' },
];

/** Does the tree hold any of the item's assemblies (whatever is picked)? */
export function libraryAvailable(item: LibraryItem, root: TreeNode): boolean {
  let hit = false;
  walk(root, (n) => (hit ||= item.nodes.includes(n.asm.id)), () => true);
  return hit;
}

/** The item's part ids in the tree as currently picked. */
export function libraryParts<N extends TreeNode>(item: LibraryItem, root: N, shown: (n: N) => boolean): { parts: Set<string>; nodes: N[] } {
  const parts = new Set<string>();
  const found: N[] = [];
  for (const id of item.nodes) {
    let node: N | null = null;
    walk(root, (n) => { if (!node && n.asm.id === id) node = n; }, shown);
    if (!node) continue;
    found.push(node);
    const n = node as N;
    const list = item.deep ? [...subtreeParts(n, shown)] : n.asm.parts.map((p) => p.id);
    const cls = new Map<string, PartClass>();
    walk(n, (x) => x.asm.parts.forEach((p) => cls.set(p.id, p.class)), shown);
    for (const id of list) {
      const c = cls.get(id)!;
      if (item.only && !item.only.includes(c)) continue;
      if (item.except?.includes(c)) continue;
      parts.add(id);
    }
  }
  return { parts, nodes: found };
}

// ------------------------------------------------------------------ looks

export interface Finish { color: number; metalness: number; roughness: number }

const hex = (s: string) => parseInt(s.replace('#', ''), 16);
const paint = (name: string): Finish => {
  const c = (palette.classes as Record<string, { color: string; roughness: number; metalness: number }>)[name];
  return { color: hex(c.color), roughness: c.roughness, metalness: c.metalness };
};

/** Kit part code -> paint class (sim/model/build_r3x.py MATERIAL_RULES, matched on the id). */
const PAINT_RULES: [RegExp, string][] = [
  [/^H_[LR]EYE_4$/i, 'eye_lens'], [/^H_[LR]EYE_/i, 'metal_dark'], [/^H_[LR]E_1$/i, 'accent_blue'], [/^H_[LR]E_2$/i, 'metal_dark'],
  [/^H_HP_/i, 'metal_dark'], [/^H_M_1$/i, 'metal_dark'], [/^H_V_1$/i, 'visor_stripes'], [/^H_/i, 'paint_charcoal'],
  [/^RX-?24$/i, 'metal_dark'], [/_DNP$/i, 'rubber'], [/^TR_RR_FULL$/i, 'rubber'], [/^MS_P_[12]_FULL$/i, 'metal_dark'],
  [/^(LS_M_FULL|TR_NR_FULL)/i, 'paint_orange'], [/^(MS_MAIN_FULL|TR_N_[123])/i, 'paint_charcoal'],
  [/^(B_B_|B_S_[12]$|B_T_|B_M_D)/i, 'paint_orange'], [/^(B_MT_|B_SM|B_S_[COP])/i, 'metal_dark'], [/^P_/i, 'metal_dark'],
  [/^(TR[-_]|LS_|MS_)/i, 'metal_grey'], [/^(HA_W_[12]|TA_W_[12]|PA_W_[12])$/i, 'paint_orange'],
  [/^(HA_PS_|HA_P_1$|TA_B_|PA_W_3$)/i, 'metal_grey'], [/^(HA_|TA_|PA_)/i, 'paint_lightgrey'],
];

/** A kit part (its id is the kit's code: H_V_1, TR_NR_Full...), whatever its class. */
export const isKitPart = (p: MPart) => PAINT_RULES.some(([re]) => re.test(p.id));

/**
 * Seen from outside even though it is not a shell: kit pieces modelled as mechanism (the visor
 * brow and arms) and the neck tube between the body and the head. A manifest `exposed` flag
 * wins (manifest need: the mech side marking these would retire the name rule).
 */
export function exposed(p: MPart): boolean {
  const flag = (p as MPart & { exposed?: boolean }).exposed;
  if (flag !== undefined) return flag;
  return p.class === 'shell' || isKitPart(p) || /\bneck tube\b/i.test(p.name);
}

/** A shell in the R3X paint. Non-kit shells take the paint of where they sit (a head shell is charcoal). */
export function exteriorFinish(p: MPart, nodeId: string): Finish {
  for (const [re, cls] of PAINT_RULES) if (re.test(p.id)) return paint(cls);
  return paint(/head/.test(nodeId) ? 'paint_charcoal' : 'paint_lightgrey');
}

/** Material-true: aluminium, steel, brass, one dark neutral for printed parts, servos near black. */
export const MATERIAL = {
  aluminium: { color: 0xe2e5e9, metalness: 0.3, roughness: 0.4 },
  steel: { color: 0x9a9fa7, metalness: 0.7, roughness: 0.32 },
  brass: { color: 0xd0a650, metalness: 0.75, roughness: 0.32 },
  printed: { color: 0x4d5057, metalness: 0.0, roughness: 0.62 },
  servo: { color: 0x26282c, metalness: 0.2, roughness: 0.45 },
  black: { color: 0x1e1f22, metalness: 0.0, roughness: 0.7 },
  fastener: { color: 0x3c3f45, metalness: 0.8, roughness: 0.38 },
  /** Inspect: one light neutral so the check colours own the view. */
  neutral: { color: 0xa9aeb5, metalness: 0.0, roughness: 0.55 },
} satisfies Record<string, Finish>;

export function mechanismFinish(p: MPart): Finish {
  const m = (p.material ?? '').toLowerCase();
  if (p.class === 'servo' || m.startsWith('servo')) return MATERIAL.servo;
  if (p.class === 'fastener') return MATERIAL.fastener;
  if (m.includes('brass')) return MATERIAL.brass;
  if (m.includes('alumin')) return MATERIAL.aluminium;
  if (m.includes('nylon') || m.includes('rubber')) return MATERIAL.black;
  if (m.includes('steel') || p.class === 'bearing' || (p.class === 'hardware' && !p.printed)) return MATERIAL.steel;
  return MATERIAL.printed;
}
