/**
 * One rig, from the mechanical model: wires mechrig/ into the page.
 *
 * - Scene panel "Model" (Visual / Mechanical / X-ray), remembered per page mode, Visual by
 *   default: Mechanical draws the built droid assembly (mechview.ts) in place of the visual
 *   model, driven by the same performer frames; X-ray ghosts its shells.
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
    this.mountSection();
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
    const sec = document.getElementById('sc-model');
    if (sec) {
      sec.hidden = build; // Build draws its own assembly
      sec.querySelectorAll<HTMLButtonElement>('[data-model]').forEach((b) => {
        b.setAttribute('aria-pressed', String(b.dataset.model === this.model));
        b.disabled = this.mode === 'build';
      });
      const note = sec.querySelector<HTMLElement>('.model-note')!;
      const v = this.view;
      note.classList.toggle('err', v.status === 'error');
      // Only what needs attention is shown (loading, an error, a rod out of reach); the model's
      // size is a tooltip (draw calls and triangles live in Frame stats).
      const mechOn = v.status === 'ready' && this.model !== 'visual';
      note.textContent = v.status === 'loading' ? 'Loading mech/out/r3x_droid…'
        : v.status === 'error' ? `Mechanical model unavailable: ${v.error}. Build mech/out/r3x_droid and use the dev server.`
          : mechOn && v.unreachable.length ? `Out of reach: ${v.unreachable.join(', ')}` : '';
      note.hidden = !note.textContent;
      sec.title = mechOn ? `${v.stats.parts} parts, ${v.stats.fasteners} fasteners; rods solved per frame`
        : 'The performer drives either model. Mechanical is the CAD assembly from mech/out.';
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

  private mountSection() {
    const body = document.getElementById('scene-body');
    const after = document.getElementById('sc-view');
    if (!body || document.getElementById('sc-model')) return;
    const sec = document.createElement('section');
    sec.id = 'sc-model';
    sec.innerHTML = `<h2>Model</h2>
      <div class="row seg" role="group" aria-label="Model">
        <button data-model="visual" aria-pressed="true" title="The painted kit model (rig.json)">Visual</button>
        <button data-model="mechanical" aria-pressed="false" title="The CAD assembly: every part, joint and linkage from the mech model">Mechanical</button>
        <button data-model="xray" aria-pressed="false" title="The mechanical assembly with its shells ghosted">X-ray</button>
      </div>
      <p class="model-note"></p>`;
    body.insertBefore(sec, after?.nextSibling ?? null);
    sec.querySelectorAll<HTMLButtonElement>('[data-model]').forEach((b) => {
      b.onclick = () => {
        this.setModel(b.dataset.model as ModelView);
        b.blur();
      };
    });
    const railAfter = document.querySelector('[data-scene-open="sc-view"]');
    if (railAfter) {
      const r = document.createElement('button');
      r.dataset.sceneOpen = 'sc-model';
      r.setAttribute('aria-label', 'Model');
      r.title = 'Model: Visual / Mechanical / X-ray';
      r.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="2.2" /><path d="M8 1.8v2.1M8 12.1v2.1M1.8 8h2.1M12.1 8h2.1M3.6 3.6l1.5 1.5M10.9 10.9l1.5 1.5M3.6 12.4l1.5-1.5M10.9 5.1l1.5-1.5" /></svg>';
      r.onclick = () => {
        (railAfter as HTMLButtonElement).click(); // opens the panel (scenepanel.ts wires the rail at mount)
        sec.scrollIntoView({ block: 'nearest' });
      };
      railAfter.after(r);
    }
  }
}
