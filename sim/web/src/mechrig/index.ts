/**
 * One rig, from the mechanical model: wires mechrig/ into the page.
 *
 * - The viewport bar's look (Exterior / Mechanism / X-ray; index.html), remembered per page
 *   mode, Exterior by default: Mechanism draws the built droid assembly (mechview.ts) in place
 *   of the visual model, driven by the same performer frames; X-ray ghosts its shells. In Build
 *   the same three buttons are the workbench's look (buildpanel.ts).
 * - Servo load (torque.ts): a meter in Bench and Build, an overlay in Studio.
 * - Build, three layers: the pad is the same puppet as everywhere (intent); the performer's
 *   rig layer turns it into joints (gaze, expressive roll); while the pad is in use the
 *   workbench mechanism follows those joints, linkages solved. Joints | Servos (servos.ts) poses
 *   joints or real actuators directly when the pad is idle.
 *
 * main.ts calls `frame()` once per drawn frame and `feedPad` with each pad frame.
 */

import type * as THREE from 'three';
import './mechrig.css';
import type { PadFrame } from '../generated/PadFrame';
import type { Workbench } from '../workbench/workbench';
import { LoadMeter } from './meter';
import { MechView, type ModelView } from './mechview';
import { fittedNodes, ServoView } from './servos';
import { ServoFollower, TorqueModel } from './torque';

const KEY = 'r3x.model';
/** The viewport bar's look buttons (shared with Build) <-> this rig's model view. */
const LOOK_OF: Record<ModelView, string> = { visual: 'exterior', mechanical: 'mechanism', xray: 'inspect' };
const MODEL_OF: Record<string, ModelView> = { exterior: 'visual', mechanism: 'mechanical', inspect: 'xray' };
type PageMode = 'show' | 'bench' | 'studio' | 'build';

export interface MechRigHost {
  scene: THREE.Scene;
  workbench: Workbench;
  /** The visual droid (null until loaded). */
  droid(): THREE.Object3D | null;
  interact(): void;
}

export class MechRig {
  readonly view: MechView;
  readonly meter = new LoadMeter();
  readonly torque = new TorqueModel();
  readonly servos: ServoView;
  /** Last time the pad did anything (performance.now ms): Build follows the puppet until 1.5 s after. */
  private padActiveAt = -Infinity;
  private mode: PageMode = 'show';
  private pick: Record<string, ModelView> = {};
  private buildTorque = new TorqueModel();
  /** Build's poses jump (sliders): the load is of a servo chasing them within its limits. */
  private follower = new ServoFollower();
  private scopeKey = '';

  constructor(private readonly host: MechRigHost) {
    this.view = new MechView(host.scene);
    this.servos = new ServoView(host.workbench);
    try {
      this.pick = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, ModelView>;
    } catch {
      /* storage blocked: Visual everywhere */
    }
    // Outside Build the bar's look buttons pick the model (buildpanel.ts forwards them as `r3x:look`).
    addEventListener('r3x:look', (e) => {
      const m = MODEL_OF[String((e as CustomEvent).detail)];
      if (m && this.mode !== 'build') this.setModel(m);
    });
    addEventListener('r3x:mode', (e) => this.setMode(String((e as CustomEvent).detail) as PageMode));
    this.view.onChange(() => {
      this.sync();
      host.interact();
    });
    this.setMode(this.mode);
  }

  /** True while the pad is being used (Build: the mechanism follows the puppet). */
  get puppeting() {
    return performance.now() - this.padActiveAt < 1500;
  }

  feedPad(p: PadFrame | null | undefined) {
    if (!p) return;
    const live = Object.values(p.intents ?? {}).some((v) => Math.abs(v) > 0.02) || p.buttons.some((b) => b > 0.05);
    if (live) this.padActiveAt = performance.now();
  }

  private get model(): ModelView {
    return this.pick[this.mode] ?? 'visual';
  }

  private setMode(m: PageMode) {
    this.mode = m;
    this.meter.resetPeak();
    this.view.setView(m === 'build' ? 'visual' : this.model);
    this.sync();
  }

  setModel(v: ModelView) {
    this.pick[this.mode] = v;
    try {
      localStorage.setItem(KEY, JSON.stringify(this.pick));
    } catch {
      /* storage blocked */
    }
    this.view.setView(v);
    this.sync();
  }

  /** Droid vs mech visibility, the section's state, the meter's placement. */
  private sync() {
    const build = this.mode === 'build';
    const mech = this.view.showing && !build;
    this.view.root.visible = mech;
    const d = this.host.droid();
    if (d && !build) d.visible = !mech;
    if (!build) {
      document.querySelectorAll<HTMLButtonElement>('#view-bar [data-look]').forEach((b) => {
        const on = b.dataset.look === LOOK_OF[this.model];
        b.setAttribute('aria-checked', String(on));
        b.tabIndex = on ? 0 : -1;
      });
    }
    const note = document.getElementById('model-note');
    if (note) {
      const v = this.view;
      note.classList.toggle('err', v.status === 'error');
      // Only what needs attention (loading, an error, a rod out of reach), and never in Build.
      const mechOn = v.status === 'ready' && this.model !== 'visual';
      note.textContent = build || this.model === 'visual' ? ''
        : v.status === 'loading' ? 'Loading the mechanism…'
          : v.status === 'error' ? `Mechanism unavailable: ${v.error}`
            : mechOn && v.unreachable.length ? `Out of reach: ${v.unreachable.join(', ')}` : '';
      note.title = v.status === 'error' ? 'Build mech/out/r3x_droid and use the dev server' : mechOn ? `${v.stats.parts} parts, ${v.stats.fasteners} fasteners; rods solved per frame` : '';
      note.hidden = !note.textContent;
    }
    this.meter.visible = this.mode === 'bench' || this.mode === 'studio' || this.mode === 'build';
    this.meter.el.classList.toggle('overlay', this.mode === 'studio');
    // Build: docked in the Joints tab (a collapsible strip there), never over the model
    const dock = build ? document.getElementById('bp-load') : null;
    const home = dock ?? document.body;
    if (this.meter.el.parentElement !== home) home.appendChild(this.meter.el);
    this.meter.el.classList.toggle('docked', !!dock);
  }

  /** Per drawn frame: pose the mech from the performer's joints and update the loads. In Build,
   *  the workbench follows the performer while the pad is in use. */
  frame(joints: Record<string, number> | null | undefined, t: number) {
    const wb = this.host.workbench;
    if (wb.active) {
      const live = this.puppeting && !!joints && !this.servos.dragging;
      if (live) {
        this.followPuppet(joints!);
        this.host.interact();
      }
      this.servos.render();
      if (this.meter.visible && wb.top) {
        const pose: Record<string, number> = {};
        for (const n of fittedNodes(wb)) for (const j of n.asm.joints) if (j.profile_joint) pose[j.profile_joint] = n.pose[j.id] ?? 0;
        const loads = this.buildTorque.update(this.follower.step(pose, t), t);
        // in a scope: only the servos that drive its joints
        const sc = wb.scope;
        const scKey = sc ? `${sc.kind}:${sc.id}` : '';
        if (scKey !== this.scopeKey) {
          this.scopeKey = scKey; // a new focus starts its own peak
          this.meter.resetPeak();
        }
        const mine = sc && new Set(sc.joints.map((x) => x.joint.profile_joint ?? x.joint.id));
        this.meter.update(mine ? loads.filter((l) => l.joints.some((j) => mine.has(j))) : loads, live ? 'puppet' : 'build');
      }
      return;
    }
    const d = this.host.droid();
    if (d && this.view.showing && d.visible) d.visible = false; // Build's exit re-shows the droid
    if (!joints) return;
    if (this.view.showing) this.view.apply(joints);
    if (this.meter.visible) this.meter.update(this.torque.update(joints, t), this.mode === 'studio' ? 'studio' : 'bench');
  }

  /** The workbench mechanism takes the performer's joints (one re-pose per frame). */
  private followPuppet(joints: Record<string, number>) {
    const wb = this.host.workbench;
    let first: { n: (typeof wb.top & object); id: string } | null = null;
    for (const n of fittedNodes(wb)) {
      for (const j of n.asm.joints) {
        if (!j.profile_joint || !(j.profile_joint in joints)) continue;
        n.pose[j.id] = joints[j.profile_joint];
        first ??= { n, id: j.id };
      }
    }
    if (first) wb.setJoint(first.n, first.id, first.n.pose[first.id]);
  }
}
