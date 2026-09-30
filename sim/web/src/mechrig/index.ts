/**
 * One rig, from the mechanical model: wires mechrig/ into the page.
 *
 * - Scene panel "Model" (Visual / Mechanical / X-ray), remembered per page mode, Visual by
 *   default: Mechanical draws the built droid assembly (mechview.ts) in place of the visual
 *   model, driven by the same performer frames; X-ray ghosts its shells.
 * - Servo load (torque.ts): a meter in Bench, an overlay in Studio, under the pad jog in Build.
 * - Build: the DS3 jogs the workbench (padjog.ts); while it does, the puppeteer gets no pad.
 *
 * main.ts calls `frame()` once per drawn frame and routes the gamepad through `padOwns` and
 * `feedPad`; nothing else here touches the rest of the page.
 */

import type * as THREE from 'three';
import './mechrig.css';
import type { PadFrame } from '../generated/PadFrame';
import type { Workbench } from '../workbench/workbench';
import { LoadMeter } from './meter';
import { MechView, type ModelView } from './mechview';
import { PadJog } from './padjog';
import { TorqueModel } from './torque';

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
  readonly jog: PadJog;
  private mode: PageMode = 'show';
  private pick: Record<string, ModelView> = {};
  private jogTorque = new TorqueModel();

  constructor(private readonly host: MechRigHost) {
    this.view = new MechView(host.scene);
    this.jog = new PadJog(host.workbench);
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

  /** The Build pad layer has the pad: the puppeteer must not read it. */
  get padOwns() {
    return this.jog.owns;
  }

  feedPad(p: PadFrame | null | undefined) {
    this.jog.feed(p);
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
      note.textContent = this.mode === 'build' ? 'Build shows the workbench assembly.'
        : v.status === 'loading' ? 'Loading the mech assembly (mech/out/r3x_droid)…'
          : v.status === 'error' ? `Mechanical model unavailable: ${v.error}. It needs the dev server and a built mech/out/r3x_droid.`
            : v.status === 'ready' && this.model !== 'visual'
              ? `${v.stats.parts} parts, ${v.stats.fasteners} fasteners in ${v.stats.drawCalls} draw calls (${(v.stats.triangles / 1e6).toFixed(1)} M tris); rods solved per frame.${v.unreachable.length ? ` Out of reach: ${v.unreachable.join(', ')}.` : ''}`
              : 'The performer drives either model; Mechanical is the CAD assembly, joints and linkages from mech/out.';
    }
    this.meter.visible = this.mode === 'bench' || this.mode === 'studio' || (this.mode === 'build' && this.jog.owns);
    this.meter.el.classList.toggle('overlay', this.mode === 'studio');
  }

  /** Per drawn frame: pose the mech from the performer's joints, update the loads, run the pad jog. */
  frame(joints: Record<string, number> | null | undefined, t: number) {
    const build = this.host.workbench.active;
    const jogWas = this.jog.owns;
    const jogPose = this.jog.tick();
    if (this.jog.owns !== jogWas) this.sync();
    if (this.jog.owns) this.host.interact(); // full frame rate while a pad can jog
    if (build) {
      if (jogPose && this.meter.visible) this.meter.update(this.jogTorque.update(jogPose, t), 'pad jog');
      return;
    }
    const d = this.host.droid();
    if (d && this.view.showing && d.visible) d.visible = false; // Build's exit re-shows the droid
    if (!joints) return;
    if (this.view.showing) this.view.apply(joints);
    if (this.meter.visible) this.meter.update(this.torque.update(joints, t), this.mode === 'studio' ? 'studio' : 'bench');
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
