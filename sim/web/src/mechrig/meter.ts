/**
 * The servo load meter: each servo's required torque (mechrig/torque.ts) as a share of its
 * stall torque, with the 70 % rule marked, the gravity (holding) part shaded and a decaying
 * peak tick. Bench shows it as a meter, Studio as an overlay, Build under the pad jog.
 */

import './mechrig.css';
import { RULE, type ServoLoad } from './torque';

const LABEL: Record<string, string> = {
  pan_servo: 'neck pan', lift_servo: 'head lift', servo_l: 'gimbal L', servo_r: 'gimbal R', visor_servo: 'visor', visor_servo_l: 'visor L', visor_servo_r: 'visor R',
  lower_servo: 'lower ring', top_servo: 'top ring', hero_shoulder_servo: 'hero shoulder', hero_wrist_servo: 'hero wrist',
  // the central column's (mech/assemblies/column)
  col_pan_servo: 'neck pan', col_lift_servo: 'head lift', col_lower_servo: 'lower ring', col_top_servo: 'top ring',
};

export class LoadMeter {
  readonly el: HTMLDivElement;
  private rows = new Map<string, { row: HTMLDivElement; bar: HTMLElement; st: HTMLElement; pk: HTMLElement; pct: HTMLElement; peak: number }>();
  private title: HTMLElement;
  private foot: HTMLElement;
  private worst = 0;
  private worstServo = '';

  constructor(parent: HTMLElement = document.body) {
    this.el = document.createElement('div');
    this.el.className = 'load-meter';
    this.el.hidden = true;
    this.el.setAttribute('aria-label', 'Servo load, % of stall torque');
    this.el.innerHTML = '<h3><span>Servo load · % stall</span><b></b></h3><div class="rows"></div><footer></footer>';
    this.title = this.el.querySelector('h3 b')!;
    this.foot = this.el.querySelector('footer')!;
    parent.appendChild(this.el);
  }

  set visible(on: boolean) {
    this.el.hidden = !on;
  }

  get visible() {
    return !this.el.hidden;
  }

  /** Clear the run's peak (a new clip). */
  resetPeak() {
    for (const r of this.rows.values()) r.peak = 0;
    this.worst = 0;
    this.worstServo = '';
  }

  update(loads: ServoLoad[], context = '') {
    if (this.el.hidden) return;
    const box = this.el.querySelector('.rows')!;
    const seen = new Set(loads.map((l) => l.servo));
    for (const [servo, r] of this.rows) r.row.hidden = !seen.has(servo);
    for (const l of loads) {
      let r = this.rows.get(l.servo);
      if (!r) {
        const row = document.createElement('div');
        row.className = 'row';
        row.title = `${l.servo} (${l.model ?? '?'}) drives ${l.joints.join(', ')}; stall ${l.stallNm.toFixed(2)} N m`;
        row.innerHTML = `<span class="name">${LABEL[l.servo] ?? l.servo}</span><span class="bar"><s></s><i></i><span class="peak"></span></span><span class="pct"></span>`;
        box.appendChild(row);
        r = { row, bar: row.querySelector('i')!, st: row.querySelector('s')!, pk: row.querySelector('.peak')!, pct: row.querySelector('.pct')!, peak: 0 };
        this.rows.set(l.servo, r);
      }
      const v = Number.isFinite(l.load) ? l.load : 0;
      const st = Number.isFinite(l.staticNm) ? Math.abs(l.staticNm) / l.stallNm : 0;
      r.peak = Math.max(v, r.peak * 0.995);
      if (v > this.worst) {
        this.worst = v;
        this.worstServo = l.servo;
      }
      r.bar.style.width = `${Math.min(100, v * 100)}%`;
      r.st.style.left = `${Math.min(100, st * 100)}%`;
      r.pk.style.left = `${Math.min(99, r.peak * 100)}%`;
      r.pct.textContent = Number.isFinite(l.load) ? `${Math.round(v * 100)}` : 'n/a';
      r.row.classList.toggle('over', v > RULE);
      r.row.classList.toggle('warn', v > 0.5 && v <= RULE);
    }
    this.title.textContent = context;
    // Only an alert takes a line; how it is computed is the tooltip.
    this.foot.innerHTML = this.worst > RULE
      ? `<b>Over the 70 % rule:</b> ${LABEL[this.worstServo] ?? this.worstServo} peaked ${Math.round(this.worst * 100)} %` : '';
    this.foot.hidden = !this.foot.innerHTML;
    this.el.title = `Peak ${Math.round(this.worst * 100)} %${this.worstServo ? ` (${LABEL[this.worstServo] ?? this.worstServo})` : ''}. CAD masses, gravity + inertia; the line is the 70 % rule, the dark tick the holding part.`;
  }
}
