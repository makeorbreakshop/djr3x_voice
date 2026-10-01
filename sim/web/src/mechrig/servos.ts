/**
 * Build's actuator layer: Joints | Servos on the Joints tab. Joints is the panel's own sliders
 * (buildpanel.ts); Servos is one slider per real actuator - gimbal L and R, the visor servo, pan,
 * lift, the ring servos, the hero shoulder and wrist - driving the actuator angle and showing the
 * joint values the mechanism makes of it (forward kinematics: gear ratio, rack, or the push rods'
 * closed form inverted by Newton steps, so gimbal L alone gives the combined tilt + roll).
 *
 * Servo ranges are the actuator's (the rod's `servo_range_deg`, else the profile actuator's
 * `range_deg` about its centre); a pose that leaves a joint's range is flagged, not hidden. Sim
 * only. Mounted into the Joints tab's DOM (no change to buildpanel.ts).
 */

import { linkMatrices, solveRod } from '../workbench/kinematics';
import type { MJoint, MLinkage } from '../workbench/manifest';
import type { AsmNode, Workbench } from '../workbench/workbench';
import { GENERATED } from './model';

/** The assemblies actually fitted: a child that is a non-default option is skipped. */
export function fittedNodes(wb: Workbench): AsmNode[] {
  const out: AsmNode[] = [];
  const off = (n: AsmNode) => {
    for (let c: AsmNode | null = n; c; c = c.parent) {
      const v = (c.asm.mount as { variant?: { group: string; id: string; default?: boolean } } | undefined)?.variant;
      if (v && (wb.variants?.[v.group] ? wb.variants[v.group] !== v.id : !v.default)) return true;
    }
    return false;
  };
  wb.forEachNode((n) => {
    if (!off(n)) out.push(n);
  });
  return out;
}

export interface Actuator {
  servo: string;
  node: AsmNode;
  kind: 'gear' | 'rack' | 'direct' | 'rod' | 'pair';
  /** Joint ids it moves (a pair: both). */
  joints: MJoint[];
  /** Its own linkage, and (pair) every linkage of the group in the joints' order of servos. */
  linkage?: MLinkage;
  group?: MLinkage[];
  /** Servo deg per joint unit (gear/direct), or mm per servo deg (rack). */
  ratio: number;
  range: [number, number];
}

const PROFILE_RANGE = new Map<string, number>(
  ((GENERATED as unknown as { actuators?: { joints: Record<string, number>; calibration: { range_deg: number } }[] }).actuators ?? [])
    .flatMap((a) => Object.keys(a.joints).map((j) => [j, a.calibration.range_deg] as [string, number])),
);

export function actuators(nodes: AsmNode[]): Actuator[] {
  const out: Actuator[] = [];
  const seen = new Set<string>();
  for (const node of nodes) {
    for (const j of node.asm.joints) {
      const d = j.drive;
      if (!d || !d.servos?.length || j.type === 'fixed' || !(j.limits.max > j.limits.min)) continue;
      const half = (PROFILE_RANGE.get(j.profile_joint ?? '') ?? 270) / 2;
      const drive = d as typeof d & { servo_deg_per_joint_deg?: number; mm_per_servo_deg?: number };
      // signed servo deg per joint unit (SCHEMA.md drive.servo_deg_per_unit) wins over the older unsigned keys
      const sdpu = d.servo_deg_per_unit;
      if (d.kind === 'push_rod_pair') {
        const lks = (d.linkages ?? []).map((id) => node.asm.linkages!.find((l) => l.id === id)!).filter(Boolean);
        const joints = node.asm.joints.filter((x) => x.drive?.kind === 'push_rod_pair' && (x.drive.linkages ?? []).join() === (d.linkages ?? []).join());
        for (const lk of lks) {
          if (seen.has(lk.servo)) continue;
          seen.add(lk.servo);
          out.push({ servo: lk.servo, node, kind: 'pair', joints, linkage: lk, group: lks, ratio: 1, range: (lk.servo_range_deg as [number, number]) ?? [-135, 135] });
        }
        continue;
      }
      const servo = d.servos[0];
      if (seen.has(servo)) continue;
      seen.add(servo);
      if (d.kind === 'push_rod') {
        const lk = node.asm.linkages?.find((l) => l.id === d.linkages?.[0]);
        if (!lk) continue;
        out.push({ servo, node, kind: 'rod', joints: [j], linkage: lk, ratio: 1, range: (lk.servo_range_deg as [number, number]) ?? [-135, 135] });
      } else if (j.type === 'prismatic') {
        out.push({ servo, node, kind: 'rack', joints: [j], ratio: sdpu ? 1 / sdpu : drive.mm_per_servo_deg ?? 1, range: [-half, half] });
      } else {
        const ratio = sdpu ?? drive.servo_deg_per_joint_deg ?? (d.gear_ratio ? 1 / d.gear_ratio : 1);
        out.push({ servo, node, kind: d.kind === 'direct' ? 'direct' : 'gear', joints: [j], ratio, range: [-half, half] });
      }
    }
  }
  return out;
}

/** Servo angles of `lks` at a node pose (null where a rod is out of reach). */
function rodAngles(node: AsmNode, lks: MLinkage[], pose: Record<string, number>): (number | null)[] {
  const ms = linkMatrices(node.asm.links, node.asm.joints, pose);
  return lks.map((lk) => solveRod(lk, ms.get(lk.horn.link)!, ms.get(lk.ground.link)!)?.servoDeg ?? null);
}

/** The actuator's angle at the node's current pose. */
export function servoAngle(a: Actuator, pose = a.node.pose): number | null {
  const v = pose[a.joints[0].id] ?? 0;
  if (a.kind === 'rack') return v / a.ratio;
  if (a.kind === 'gear' || a.kind === 'direct') return v * a.ratio;
  return rodAngles(a.node, [a.linkage!], pose)[0];
}

/**
 * Forward kinematics of a servo move: the joint values that put `a` at `deg` (and, for the
 * gimbal pair, keep its partner where it is). Newton on the rods' closed form; null when the
 * linkage cannot reach.
 */
export function solveServo(a: Actuator, deg: number, pose = a.node.pose): Record<string, number> | null {
  const j = a.joints[0];
  if (a.kind === 'rack') return { [j.id]: deg * a.ratio };
  if (a.kind === 'gear' || a.kind === 'direct') return { [j.id]: deg / a.ratio };
  const lks = a.kind === 'pair' ? a.group! : [a.linkage!];
  const target = a.kind === 'pair' ? rodAngles(a.node, lks, pose).map((v, i) => (lks[i].servo === a.servo ? deg : v)) : [deg];
  if (target.some((v) => v === null)) return null;
  const ids = a.joints.map((x) => x.id);
  const x = ids.map((id) => pose[id] ?? 0);
  const at = (xs: number[]) => rodAngles(a.node, lks, { ...pose, ...Object.fromEntries(ids.map((id, i) => [id, xs[i]])) });
  for (let it = 0; it < 30; it++) {
    const f0 = at(x);
    if (f0.some((v) => v === null)) return null;
    const r = f0.map((v, i) => (v as number) - (target[i] as number));
    if (Math.max(...r.map(Math.abs)) < 1e-4) break;
    const J = ids.map(() => ids.map(() => 0)); // J[row servo][col joint]
    for (let c = 0; c < ids.length; c++) {
      const xp = [...x];
      xp[c] += 0.05;
      const fp = at(xp);
      if (fp.some((v) => v === null)) return null;
      for (let row = 0; row < ids.length; row++) J[row][c] = ((fp[row] as number) - (f0[row] as number)) / 0.05;
    }
    let dx: number[];
    if (ids.length === 1) dx = [r[0] / J[0][0]];
    else {
      const det = J[0][0] * J[1][1] - J[0][1] * J[1][0];
      if (Math.abs(det) < 1e-9) return null;
      dx = [(J[1][1] * r[0] - J[0][1] * r[1]) / det, (-J[1][0] * r[0] + J[0][0] * r[1]) / det];
    }
    for (let c = 0; c < ids.length; c++) x[c] -= Math.max(-10, Math.min(10, dx[c]));
  }
  const res = at(x);
  if (res.some((v, i) => v === null || Math.abs((v as number) - (target[i] as number)) > 0.05)) return null;
  return Object.fromEntries(ids.map((id, i) => [id, x[i]]));
}

const LABEL: Record<string, string> = {
  servo_l: 'gimbal L', servo_r: 'gimbal R', visor_servo: 'visor', pan_servo: 'neck pan', lift_servo: 'head lift',
  lower_servo: 'lower ring', top_servo: 'top ring', hero_shoulder_servo: 'hero shoulder', hero_wrist_servo: 'hero wrist',
  // the central column's (mech/assemblies/column)
  col_pan_servo: 'neck pan', col_lift_servo: 'head lift', col_lower_servo: 'lower ring', col_top_servo: 'top ring',
};
const sgn = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}`;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

/** The Joints | Servos toggle and the Servos list, injected into Build's Joints tab. */
export class ServoView {
  on = false;
  dragging = false;
  private acts: Actuator[] = [];
  private sig = '';
  private body: HTMLElement | null = null;
  private failed = new Set<string>();

  constructor(private readonly wb: Workbench) {}

  private mount(): boolean {
    if (this.body?.isConnected) return true;
    const tab = document.querySelector<HTMLElement>('.tab-body[data-body="inspect"]');
    const bar = tab?.querySelector('.bp-bar');
    const joints = document.getElementById('bp-joints');
    if (!tab || !bar || !joints) return false;
    const seg = document.createElement('span');
    seg.className = 'seg bp-seg servo-toggle';
    seg.setAttribute('role', 'group');
    seg.setAttribute('aria-label', 'Pose by');
    seg.innerHTML = `<button class="bp-small" data-pose-by="joints" aria-pressed="true" title="Pose joints directly">Joints</button><button class="bp-small" data-pose-by="servos" aria-pressed="false" title="Drive each real actuator; the mechanism decides the joints">Servos</button>`;
    bar.insertBefore(seg, bar.firstChild?.nextSibling ?? null);
    const body = document.createElement('div');
    body.id = 'bp-servos';
    body.hidden = true;
    joints.after(body);
    this.body = body;
    seg.querySelectorAll<HTMLButtonElement>('[data-pose-by]').forEach((b) => {
      b.onclick = () => {
        this.on = b.dataset.poseBy === 'servos';
        seg.querySelectorAll('[data-pose-by]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        joints.hidden = this.on;
        body.hidden = !this.on;
        this.sig = '';
        this.render();
      };
    });
    body.addEventListener('pointerdown', () => (this.dragging = true));
    addEventListener('pointerup', () => (this.dragging = false));
    body.addEventListener('input', (e) => {
      const inp = e.target as HTMLInputElement;
      const a = this.acts.find((x) => x.servo === inp.dataset.servo);
      if (a) this.set(a, Number(inp.value));
    });
    return true;
  }

  /** Move one actuator: solve the joints, pose the workbench once. */
  set(a: Actuator, deg: number) {
    const sol = solveServo(a, deg);
    if (!sol) {
      this.failed.add(a.servo);
      this.render();
      return;
    }
    this.failed.delete(a.servo);
    const ids = Object.keys(sol);
    for (const id of ids) a.node.pose[id] = sol[id];
    this.wb.setJoint(a.node, ids[0], sol[ids[0]]); // one re-pose and panel refresh
    for (const id of ids) a.node.pose[id] = sol[id]; // setJoint clamps to limits +/- 40: keep the solve
    this.render();
  }

  /** Per frame while Build is open (cheap: values only, rows rebuilt when the tree changes). */
  render() {
    if (!this.wb.active || !this.mount() || !this.on || !this.body) return;
    const nodes = fittedNodes(this.wb);
    const sc = this.wb.scope;
    const sig = nodes.map((n) => n.key).join('|') + (sc ? `#${sc.kind}:${sc.id}` : '');
    if (sig !== this.sig) {
      this.sig = sig;
      // in a scope: only the actuators of its joints
      const mine = sc && new Set(sc.joints.map((x) => `${x.node.key}:${x.joint.id}`));
      this.acts = actuators(nodes).filter((a) => !mine || a.joints.some((j) => mine.has(`${a.node.key}:${j.id}`)));
      this.body.innerHTML = this.acts.map((a) => `<div class="joint servo-row">
          <div class="jhead"><b title="${esc(a.servo)} · ${a.kind}${a.kind === 'rack' ? ` ${a.ratio.toFixed(3)} mm/°` : a.kind === 'gear' || a.kind === 'direct' ? ` ${a.ratio.toFixed(2)}:1` : ''}">${esc(LABEL[a.servo] ?? a.servo)}</b>
            <output data-sout="${esc(a.servo)}"></output></div>
          <input type="range" data-servo="${esc(a.servo)}" min="${a.range[0]}" max="${a.range[1]}" step="0.5" aria-label="${esc(LABEL[a.servo] ?? a.servo)} servo angle">
          <div class="jlive" data-slive="${esc(a.servo)}"></div></div>`).join('') || '<p class="empty">No actuators in this assembly.</p>';
    }
    for (const a of this.acts) {
      const deg = servoAngle(a);
      const inp = this.body.querySelector<HTMLInputElement>(`input[data-servo="${CSS.escape(a.servo)}"]`);
      if (inp && document.activeElement !== inp && deg !== null) inp.value = String(deg);
      const out = this.body.querySelector(`[data-sout="${CSS.escape(a.servo)}"]`);
      if (out) out.textContent = deg === null ? 'out of reach' : `${sgn(deg)}°`;
      const live = this.body.querySelector<HTMLElement>(`[data-slive="${CSS.escape(a.servo)}"]`);
      if (!live) continue;
      const bits = a.joints.map((j) => {
        const v = a.node.pose[j.id] ?? 0;
        const out = v < j.limits.min - 1e-6 || v > j.limits.max + 1e-6;
        const u = j.unit === 'mm' ? ' mm' : '°';
        return `<span class="${out ? 'bad' : ''}" title="${esc(j.name)} ${j.limits.min}…${j.limits.max}${u}">${esc(j.profile_joint ?? j.id)} ${sgn(v)}${u}${out ? ' outside its range' : ''}</span>`;
      });
      if (this.failed.has(a.servo)) bits.push('<span class="bad">the linkage cannot reach that angle</span>');
      const chk = a.joints.map((j) => a.node.asm.checks?.find((c) => c.id === `interference_${j.id}`)).find((c) => c?.value != null);
      if (chk) bits.push(`contact ${sgn(chk.value!)}°`);
      live.innerHTML = bits.join(' · ');
    }
  }
}
