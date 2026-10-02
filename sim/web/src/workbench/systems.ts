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
 * - Looks: each part's manifest `finish` (SCHEMA.md "Finish"), assigned where the part is declared: the
 *   Exterior its paint through palette.json (the kit's from mech/assemblies/kit/finish.json, the table
 *   the Original is baked from), the Mechanism its filament or purchased colour, else its material.
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
  source?: string;
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

/** The published designs the build is assembled from: the root's `designs` (SCHEMA.md). Each
 *  is isolated from the droid's own tree, so switching is instant and a design shows as placed. */
export function libraryFrom(root: MAssembly | null | undefined): LibraryItem[] {
  return (root?.designs ?? []).map((d) => ({
    id: d.id, name: d.name, by: d.author ?? '', source: d.source, picks: d.picks ?? {}, nodes: d.assemblies ?? [],
    deep: d.deep, only: d.only, except: d.except, look: d.look ?? 'mechanism',
  }));
}

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
type PaletteClass = { color: string; roughness: number; metalness: number };
const CLASSES = palette.classes as Record<string, PaletteClass>;

/** A palette.json paint class as a flat finish (the weathering is the Original's, baked). */
export function paintFinish(name: string): Finish | null {
  const c = CLASSES[name];
  return c ? { color: hex(c.color), roughness: c.roughness, metalness: c.metalness } : null;
}

/** Shown where a part seen from outside has no paint (or a paint the palette lacks): loud on purpose,
 *  so a missing finish is fixed where the part is declared (SCHEMA.md "Finish") instead of guessed. */
export const MISSING_FINISH: Finish = { color: 0xff00d4, metalness: 0, roughness: 0.5 };

/** A kit part (or one standing in for a kit part): its finish carries the kit code (assemblies/kit/finish.json). */
export const isKitPart = (p: MPart) => !!p.finish?.kit;

/** Seen from outside the droid: the part's `exposed` flag (SCHEMA.md), else shells only. */
export function exposed(p: MPart): boolean {
  return p.exposed ?? p.class === 'shell';
}

/** Seen on the finished droid and printed (or a shell): it must say how it is painted (SCHEMA.md "Finish").
 *  Purchased metal seen from outside may stay bare (it shows its material). */
export function needsPaint(p: MPart): boolean {
  return !p.replaced_by && exposed(p) && (!!p.printed || p.class === 'shell');
}

/** What is wrong with a part's paint, or null. */
export function finishProblem(p: MPart): string | null {
  const paint = p.finish?.paint;
  if (paint && paint !== 'none' && !CLASSES[paint]) return `paint "${paint}" is not in palette.json`;
  if (!paint && needsPaint(p)) return 'seen from outside but has no paint';
  return null;
}

/** Exterior: the part's paint (palette.json); a bare or unpainted part its real colour (mechanismFinish);
 *  a part that should be painted but is not, MISSING_FINISH - never a guess. */
export function exteriorFinish(p: MPart): Finish {
  const paint = p.finish?.paint;
  if (paint && paint !== 'none') return paintFinish(paint) ?? MISSING_FINISH;
  if (!paint && needsPaint(p)) return MISSING_FINISH;
  return mechanismFinish(p);
}

/** Material-true: aluminium, steel, brass; printed parts in their filament colour (`finish.print`), else
 *  one dark neutral; servos near black. */
export const MATERIAL = {
  // brushed / clear-anodised 6061: a mid silver, rough enough that the room never mirrors in it
  aluminium: { color: 0xa9afb7, metalness: 0.55, roughness: 0.55 },
  steel: { color: 0x8a9098, metalness: 0.7, roughness: 0.42 },
  brass: { color: 0xb8904a, metalness: 0.7, roughness: 0.4 },
  printed: { color: 0x4d5057, metalness: 0.0, roughness: 0.62 },
  servo: { color: 0x26282c, metalness: 0.2, roughness: 0.45 },
  black: { color: 0x1e1f22, metalness: 0.0, roughness: 0.7 },
  /** A moulded or clear plastic part (polycarbonate wheels). */
  plastic: { color: 0xb9c2c6, metalness: 0.0, roughness: 0.35 },
  fastener: { color: 0x3c3f45, metalness: 0.8, roughness: 0.38 },
  /** Inspect: one light neutral so the check colours own the view. */
  neutral: { color: 0xa9aeb5, metalness: 0.0, roughness: 0.55 },
} satisfies Record<string, Finish>;

/** The material's surface (metalness, roughness, a default colour). */
function materialFinish(p: MPart): Finish {
  const m = (p.material ?? '').toLowerCase();
  if (p.class === 'servo' || m.startsWith('servo')) return MATERIAL.servo;
  if (p.class === 'fastener') return MATERIAL.fastener;
  if (p.printed) return MATERIAL.printed;
  if (m.includes('brass')) return MATERIAL.brass;
  if (m.includes('alumin')) return MATERIAL.aluminium;
  if (m.includes('polycarbonate')) return MATERIAL.plastic;
  if (m.includes('nylon') || m.includes('rubber')) return MATERIAL.black;
  if (m.includes('steel') || p.class === 'bearing' || p.class === 'hardware') return MATERIAL.steel;
  return MATERIAL.printed;
}

/** Mechanism: a printed part in its filament colour, a purchased one in its own colour (`finish.color`:
 *  goBILDA grey, black anodising) on its material's surface, else the material. */
export function mechanismFinish(p: MPart): Finish {
  const base = materialFinish(p);
  const c = p.printed ? p.finish?.print?.color : p.finish?.color;
  return c ? { ...base, color: hex(c) } : base;
}

/** The print list: printed parts grouped by filament and colour (the build's parts, replaced ones left out). */
export interface PrintGroup { filament: string; color: string; colorName: string; count: number; parts: string[] }
export function printList(parts: Iterable<MPart>): { groups: PrintGroup[]; unknown: string[] } {
  const by = new Map<string, PrintGroup>();
  const unknown: string[] = [];
  for (const p of parts) {
    if (!p.printed || p.replaced_by) continue;
    const pr = p.finish?.print;
    if (!pr?.filament || !pr.color) {
      unknown.push(p.id);
      continue;
    }
    const key = `${pr.filament}|${pr.color.toLowerCase()}`;
    let g = by.get(key);
    if (!g) by.set(key, (g = { filament: pr.filament, color: pr.color, colorName: pr.color_name ?? pr.color, count: 0, parts: [] }));
    g.count++;
    g.parts.push(p.id);
  }
  const groups = [...by.values()].sort((a, b) => a.filament.localeCompare(b.filament) || b.count - a.count);
  return { groups, unknown };
}

// ------------------------------------------------------------------ a focus's context

/**
 * What a focused system's parts are mounted to, kept in view as solid context (the visor's servo mounts and
 * the head plate under them; the column posts the neck's sled rides on): parts on a joint's parent link -
 * the side that stays put - reached from the system's parts through the manifest's mates and fastener joins,
 * up to `hops` steps (a servo -> its mount -> the plate the mount is bolted to).
 */
export function contextParts<N extends TreeNode>(joints: SysJoint<N>[], parts: Set<string>, hops = 2): Set<string> {
  const out = new Set<string>();
  const nodes = [...new Set(joints.map((j) => j.node))];
  for (const node of nodes) {
    const parentLinks = new Set(joints.filter((j) => j.node === node).map((j) => j.joint.parent_link));
    const linkOf = new Map(node.asm.parts.map((p) => [p.id, p.link]));
    const ok = (id: string) => !parts.has(id) && parentLinks.has(linkOf.get(id) ?? '');
    // who touches whom: mates (part to part) and fasteners (every part a fastener joins)
    const adj = new Map<string, Set<string>>();
    const link = (a: string, b: string) => {
      if (a === b) return;
      (adj.get(a) ?? adj.set(a, new Set()).get(a)!).add(b);
      (adj.get(b) ?? adj.set(b, new Set()).get(b)!).add(a);
    };
    const mates = (node.asm as typeof node.asm & { mates?: { a?: { part?: string }; b?: { part?: string } }[] }).mates ?? [];
    for (const m of mates) if (m.a?.part && m.b?.part) link(m.a.part, m.b.part);
    for (const f of node.asm.fasteners ?? []) for (const a of f.joins) for (const b of f.joins) link(a, b);
    let front = [...parts].filter((id) => linkOf.has(id));
    for (let h = 0; h < hops && front.length; h++) {
      const next: string[] = [];
      for (const id of front) for (const o of adj.get(id) ?? []) {
        if (ok(o) && !out.has(o)) {
          out.add(o);
          next.push(o);
        }
      }
      front = next;
    }
  }
  return out;
}
