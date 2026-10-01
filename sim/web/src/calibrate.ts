/**
 * Bench calibration wizard (plan Phase 8): jog one `r3x_servo` actuator by raw pulse, mark
 * its centre, direction and soft-limit ends, and save them into the Robot Profile as
 * `calibrated: measured`. The runtime checks and writes (`perf cal_jog` / `cal_save`, Bench
 * mode only); a saved calibration reaches the controller on the next runtime start.
 *
 * `CalSession` is the pure part (tested); `mountCalibrate` is the Drive-tab section.
 */

import type { Ack, Command, Event, GatewayClient, Hello, RetainedState } from './gateway';
import type { Actuator } from './generated/Actuator';

export class CalSession {
  us: number;
  centre: number | null = null;
  invert: boolean;
  limits: [number | null, number | null] = [null, null];

  constructor(readonly actuator: Actuator) {
    const c = actuator.calibration;
    this.us = Math.round(c.center_us + c.trim_us);
    this.invert = c.invert;
  }

  /** Move by `delta` us (clamped to the pulse range); the command to send. */
  jog(delta: number): Command {
    return this.jogTo(this.us + delta);
  }

  jogTo(us: number): Command {
    const c = this.actuator.calibration;
    this.us = Math.round(Math.min(c.pulse_max_us, Math.max(c.pulse_min_us, us)));
    return { class: 'perf', type: 'cal_jog', actuator: this.actuator.name, us: this.us };
  }

  markCentre() {
    this.centre = this.us;
  }

  markLimit(end: 0 | 1) {
    this.limits[end] = this.us;
  }

  /** The save command, or why it cannot be sent yet. */
  save(): Command | string {
    if (this.centre === null) return 'mark the centre first';
    const [a, b] = this.limits;
    if ((a === null) !== (b === null)) return 'mark both limit ends, or neither';
    if (a !== null && b !== null && !(Math.min(a, b) <= this.centre && this.centre <= Math.max(a, b)))
      return 'the centre must lie between the limit ends';
    return {
      class: 'perf',
      type: 'cal_save',
      actuator: this.actuator.name,
      center_us: this.centre,
      invert: this.invert,
      ...(a !== null && b !== null ? { limits_us: [a, b] as [number, number] } : {}),
    };
  }
}

const STEPS = [-50, -10, -1, 1, 10, 50];

/** The wizard as a section of the Drive tab. */
export function mountCalibrate(gw: GatewayClient, parent: HTMLElement, toast: (msg: string) => void) {
  // A disclosure, live only (the runtime owns the servos): one servo at a time, in Bench.
  const el = document.createElement('section');
  el.className = 'cal';
  el.dataset.live = '';
  el.innerHTML = `<details>
    <summary title="Bench only, one servo at a time: jog until the joint sits at its centre pose">Calibrate a servo</summary>
    <p class="hint" data-cal="note" hidden></p>
    <div data-cal="ctl">
    <div class="row"><select data-cal="pick" aria-label="Servo"></select><button data-cal="enable">Enable output</button></div>
    <p class="now"><span data-cal="pulse">-</span> <small data-cal="tele"></small></p>
    <div class="row wrap" data-cal="steps">${STEPS.map((s) => `<button data-step="${s}">${s > 0 ? '+' : ''}${s}</button>`).join('')}</div>
    <div class="row wrap">
      <button data-cal="centre">Centre here</button>
      <label class="check" title="+us moves the joint negative"><input type="checkbox" data-cal="invert" /> Inverted</label>
    </div>
    <div class="row wrap">
      <button data-cal="lim0">Limit end A here</button><button data-cal="lim1">Limit end B here</button>
      <button data-cal="clear">Clear limits</button>
    </div>
    <p class="hint" data-cal="marks"></p>
    <div class="row"><button class="primary" data-cal="save">Save to profile</button></div>
    </div></details>`;
  parent.appendChild(el);
  const q = <T extends HTMLElement>(k: string) => el.querySelector(`[data-cal="${k}"]`) as T;
  const pick = q<HTMLSelectElement>('pick');
  let servos: Actuator[] = [];
  let s: CalSession | null = null;
  let bench = false;

  const send = async (c: Command) => {
    const a: Ack = await gw.send(c);
    if (a.status === 'rejected') toast(a.reason);
    return a.status === 'accepted';
  };
  const render = () => {
    el.querySelectorAll('button, input, select').forEach((b) => ((b as HTMLInputElement).disabled = !bench || !s));
    pick.disabled = !servos.length;
    // Nothing to calibrate (or not Bench): one line, no dead controls.
    const note = !servos.length ? 'No servos to calibrate.' : bench ? '' : 'Switch to Bench to calibrate.';
    q('note').textContent = note;
    q('note').hidden = !note;
    q('ctl').hidden = !servos.length || !bench;
    if (!s) return;
    q('pulse').textContent = `${s.us} us`;
    q<HTMLInputElement>('invert').checked = s.invert;
    const [a, b] = s.limits;
    q('marks').textContent = `centre ${s.centre ?? '-'} us, limits ${a ?? '-'} / ${b ?? '-'} us (${s.actuator.calibration.status})`;
  };
  const select = (name: string) => {
    const a = servos.find((x) => x.name === name);
    s = a ? new CalSession(a) : null;
    render();
  };

  pick.onchange = () => select(pick.value);
  q('enable').onclick = () => s && void send({ class: 'stage', type: 'set_output', output: s.actuator.name, enabled: true });
  el.querySelectorAll<HTMLButtonElement>('[data-step]').forEach((b) => {
    b.onclick = () => {
      if (!s) return;
      void send(s.jog(Number(b.dataset.step)));
      render();
    };
  });
  q('centre').onclick = () => (s?.markCentre(), render());
  q<HTMLInputElement>('invert').onchange = (e) => s && ((s.invert = (e.target as HTMLInputElement).checked), render());
  q('lim0').onclick = () => (s?.markLimit(0), render());
  q('lim1').onclick = () => (s?.markLimit(1), render());
  q('clear').onclick = () => s && ((s.limits = [null, null]), render());
  q('save').onclick = async () => {
    if (!s) return;
    const c = s.save();
    if (typeof c === 'string') return toast(c);
    if (await send(c)) toast(`${s.actuator.name} saved as measured; restart the runtime to apply it`);
  };

  gw.subscribe({
    onHello: (h: Hello) => {
      servos = (h.profile?.actuators ?? []).filter((a) => a.driver === 'r3x_servo');
      pick.innerHTML = servos.map((a) => `<option value="${a.name}">${a.channel}: ${a.name}</option>`).join('');
      if (servos.length) select(s && servos.some((a) => a.name === s!.actuator.name) ? s.actuator.name : servos[0].name);
      bench = h.state.stage.mode === 'bench';
      render();
    },
    onState: (st: RetainedState) => {
      bench = st.stage.mode === 'bench';
      render();
    },
    onEvent: (e: Event) => {
      if (!s || e.domain !== 'ops' || e.type !== 'servo_telemetry') return;
      const joint = Object.keys(s.actuator.joints)[0];
      const ch = joint ? e.channels[joint] : undefined;
      q('tele').textContent = ch ? `controller: ${ch.us} us, ${ch.x.toFixed(1)} (${e.rail_ma} mA rail)` : '';
    },
  });
  render();
}
