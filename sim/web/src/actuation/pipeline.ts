/**
 * The actuation pipeline - the part of the sim that is also the hardware contract.
 *
 *   behaviour targets (joint deg, or mm for the head lift)
 *     -> JerkLimitedFollower per channel @ 200 Hz  (v/a/j limits, soft-limit braking)
 *        ...or a direct target (Maestro script / raw command) through the Maestro's own
 *        speed/acceleration ramp, exactly as the board would apply it
 *     -> output stage @ 50 Hz: joint value -> servo deg (gear/pinion, invert)
 *        -> calibrated pulse -> controller resolution       <- Frame {frame, t, targets[]}
 *           (custom controller: whole microseconds; Maestro: quarter-us; PCA9685: counts)
 *     -> servo plant @ 1 kHz (sim only): frame latency, deadband, saturating P loop,
 *        load-derated speed, torque/inertia accel limit, gear backlash
 *     -> joint values on the 3D rig
 *
 * Everything above the Frame line is the reference implementation for our custom motion
 * controller's firmware (or a CantinaOS sink); the plant stands in for physical servos.
 * So the sim can only show motion the frames encode.
 */

import { JerkLimitedFollower } from './trajectory';
import { noLoadSpeed, SERVOS, ServoModel, stallTorque, usPerDeg } from './servos';
import servoMap from './servo_map.json';
import type { JointSpec } from '../rig';

export interface ChannelConfig {
  ch: number;
  name: string;
  servo: string;
  gear: number;
  joints: Record<string, number>;
  margin: number;
  vMax: number;
  aMax: number;
  jMax: number;
  note?: string;
  calibration?: string;
  /** Pulse at which the joint sits at centerValue (the model's rest pose by default). */
  centerUs?: number;
  centerValue?: number;
  trimUs?: number;
  invert?: boolean;
  /** Prismatic joints: mm of travel per servo degree (rack and pinion). */
  mmPerDeg?: number;
  /** Maestro channel settings (0 = unlimited): speed in 0.25us/10ms, accel in 0.25us/10ms/80ms. */
  maestroSpeed?: number;
  maestroAccel?: number;
}

interface ProfileDoc {
  label: string;
  /** 'custom': our own controller (1 us, frameHz); 'maestro': Pololu (0.25 us); 'pca9685': 12-bit. */
  controller: { type: 'custom' | 'maestro' | 'pca9685'; channels: number; frameHz?: number; resolutionUs?: number;
    periodMs?: number; oscillatorHz?: number };
  supplyVolts: number;
  inherit?: string;
  channels: ChannelConfig[];
}

export interface JointDynamics {
  mass_kg: number;
  gravity_torque_worst_kgcm: number;
  inertia_kgm2: number;
}

export interface Frame {
  frame: number;
  t: number; // s
  /** Per controller channel: Maestro quarter-us, or PCA9685 counts; 0 = channel off. */
  targets: number[];
}

const DOC = servoMap as unknown as { default: string; profiles: Record<string, ProfileDoc> };
export const PROFILES = Object.fromEntries(Object.entries(DOC.profiles).map(([k, p]) => [k, p.label]));
export const DEFAULT_PROFILE = DOC.default;

function resolveProfile(name: string): ProfileDoc {
  const p = DOC.profiles[name];
  if (!p) throw new Error(`unknown actuation profile ${name}`);
  if (!p.inherit) return p;
  const base = resolveProfile(p.inherit);
  return { ...p, channels: [...base.channels, ...p.channels] };
}

const KGCM_TO_NM = 0.0980665;
const PLANT_HZ = 1000;
const TRAJ_EVERY = 5; // 200 Hz
const clamp = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x);

export class Channel {
  readonly model: ServoModel;
  readonly follower: JerkLimitedFollower;
  readonly usPerDeg: number;
  readonly centerUs: number;
  readonly centerValue: number;
  readonly dir: number;
  readonly prismatic: boolean;
  readonly unit: string;
  readonly primaryJoint: string;
  /** Load at the servo horn, kg.cm (worst-case gravity over driven joints, through the gearing). */
  readonly loadKgcm: number;
  readonly stallKgcm: number;
  readonly utilisation: number;
  readonly speedNoLoad: number; // servo deg/s
  readonly speedLoaded: number; // servo deg/s
  readonly accelMax: number; // servo deg/s^2 from spare torque / reflected inertia
  readonly warnings: string[] = [];

  /** Direct pulse target (script/raw command) in us, or null when the follower drives. */
  directUs: number | null = null;
  private rampUs = 1500;
  private rampV = 0;

  us = 1500;
  target = 0; // controller units
  private cmd = 0; // servo deg the servo is being told
  private motorV = 0;
  motor = 0;
  out = 0;

  constructor(readonly cfg: ChannelConfig, joints: Map<string, JointSpec>, dyn: Record<string, JointDynamics>, volts: number) {
    const model = SERVOS[cfg.servo];
    if (!model) throw new Error(`unknown servo ${cfg.servo}`);
    this.model = model;
    this.usPerDeg = usPerDeg(model);
    this.centerUs = cfg.centerUs ?? (model.minUs + model.maxUs) / 2;
    this.centerValue = cfg.centerValue ?? 0;
    this.dir = cfg.invert ? -1 : 1;
    this.primaryJoint = Object.keys(cfg.joints)[0];
    const spec = joints.get(this.primaryJoint);
    if (!spec) throw new Error(`channel ${cfg.ch} drives unknown joint ${this.primaryJoint}`);
    this.prismatic = spec.type === 'prismatic';
    this.unit = this.prismatic ? 'mm' : 'deg';

    // Reach of the servo, in joint units, around the centre pulse.
    const loServo = (model.minUs - this.centerUs) / this.usPerDeg;
    const hiServo = (model.maxUs - this.centerUs) / this.usPerDeg;
    const reach = [this.servoToValue(loServo), this.servoToValue(hiServo)].sort((a, b) => a - b);
    const lo = Math.max(spec.min, reach[0]) + cfg.margin;
    const hi = Math.min(spec.max, reach[1]) - cfg.margin;
    if (spec.min < reach[0] - 0.5 || spec.max > reach[1] + 0.5) {
      this.warnings.push(`joint range ${spec.min}..${spec.max} ${this.unit} exceeds servo reach ` +
        `${reach[0].toFixed(0)}..${reach[1].toFixed(0)}`);
    }
    const rest = clamp(0, lo, hi);
    this.follower = new JerkLimitedFollower({ vMax: cfg.vMax, aMax: cfg.aMax, jMax: cfg.jMax }, lo, hi, rest);
    this.motor = this.out = this.cmd = this.valueToServo(rest);
    this.rampUs = this.us = this.valueToUs(rest);

    let load = 0;
    let inertia = 0;
    for (const j of Object.keys(cfg.joints)) {
      const d = dyn[j];
      if (!d) continue;
      if (this.prismatic) {
        // Lifting force m*g through a pinion of radius r = mmPerDeg * 180/pi.
        const rCm = ((cfg.mmPerDeg ?? 0.2) * 180) / Math.PI / 10;
        load += d.mass_kg * rCm;
      } else {
        load += d.gravity_torque_worst_kgcm / cfg.gear;
        inertia += d.inertia_kgm2 / (cfg.gear * cfg.gear);
      }
    }
    this.loadKgcm = load;
    this.stallKgcm = stallTorque(model, volts);
    this.utilisation = this.loadKgcm / this.stallKgcm;
    this.speedNoLoad = noLoadSpeed(model, volts);
    this.speedLoaded = this.speedNoLoad * Math.max(0.05, 1 - this.utilisation);
    const spareNm = Math.max(0.05, this.stallKgcm - this.loadKgcm) * KGCM_TO_NM;
    this.accelMax = (spareNm / (inertia + 1e-5)) * (180 / Math.PI);
    if (this.utilisation > 0.5) {
      this.warnings.push(`worst-case load ${(this.utilisation * 100).toFixed(0)}% of stall (keep under 30-50%)`);
    }
    const cap = Math.abs(this.servoToValue(this.speedLoaded * 0.7) - this.servoToValue(0));
    if (cfg.vMax > cap) {
      this.warnings.push(`vMax ${cfg.vMax} ${this.unit}/s above 70% of loaded speed (${cap.toFixed(0)})`);
    }
  }

  /** Joint value (deg or mm) -> servo degrees from the centre pulse. */
  valueToServo(v: number) {
    const d = v - this.centerValue;
    return this.dir * (this.prismatic ? d / (this.cfg.mmPerDeg ?? 0.2) : d * this.cfg.gear);
  }

  servoToValue(servoDeg: number) {
    const s = servoDeg * this.dir;
    return this.centerValue + (this.prismatic ? s * (this.cfg.mmPerDeg ?? 0.2) : s / this.cfg.gear);
  }

  /** Joint value -> pulse width, as the servo sink will compute it. */
  valueToUs(v: number) {
    const us = this.centerUs + (this.cfg.trimUs ?? 0) + this.valueToServo(v) * this.usPerDeg;
    return clamp(us, this.model.minUs, this.model.maxUs);
  }

  usToServo(us: number) {
    return (us - this.centerUs - (this.cfg.trimUs ?? 0)) / this.usPerDeg;
  }

  /** Where the pulse is heading this tick: the follower, or a direct target through the
   *  Maestro's speed/accel ramp (speed in 0.25us/10ms, accel in 0.25us/10ms/80ms). */
  pulseNow(dt: number): number {
    if (this.directUs === null) return this.valueToUs(this.follower.x);
    const target = clamp(this.directUs, this.model.minUs, this.model.maxUs);
    const vMax = (this.cfg.maestroSpeed ?? 0) * 25; // us/s
    const aMax = (this.cfg.maestroAccel ?? 0) * 312.5; // us/s^2
    if (!vMax && !aMax) {
      this.rampUs = target;
      this.rampV = 0;
      return target;
    }
    const e = target - this.rampUs;
    let v = aMax ? this.rampV + clamp(Math.sign(e) * aMax * dt, -aMax * dt, aMax * dt) : Math.sign(e) * vMax;
    if (vMax) v = clamp(v, -vMax, vMax);
    if (aMax) {
      const brake = Math.sqrt(2 * aMax * Math.abs(e));
      v = clamp(v, -brake, brake);
    }
    this.rampV = v;
    const step = v * dt;
    this.rampUs = Math.abs(step) >= Math.abs(e) ? target : this.rampUs + step;
    return this.rampUs;
  }

  /** Hand control back to the follower without a jump. */
  releaseDirect() {
    if (this.directUs === null) return;
    this.directUs = null;
    this.follower.reset(this.servoToValue(this.usToServo(this.rampUs)));
  }

  applyPulse(us: number) {
    this.cmd = this.usToServo(us);
  }

  plantStep(dt: number) {
    const err = this.cmd - this.motor;
    const db = this.model.deadbandUs / this.usPerDeg;
    let wDes = 0;
    if (Math.abs(err) > db / 2) wDes = clamp(err / this.model.tauS, -this.speedLoaded, this.speedLoaded);
    const dw = clamp(wDes - this.motorV, -this.accelMax * dt, this.accelMax * dt);
    this.motorV += dw;
    this.motor += this.motorV * dt;
    const b = this.model.backlashDeg / 2;
    if (this.motor - this.out > b) this.out = this.motor - b;
    else if (this.out - this.motor > b) this.out = this.motor + b;
  }

  snap() {
    this.motor = this.out = this.cmd;
    this.motorV = 0;
  }

  /** The joint value the rig shows (deg or mm). */
  get value() {
    return this.servoToValue(this.out);
  }
}

export class Actuation {
  readonly profileName: string;
  readonly label: string;
  readonly controller: ProfileDoc['controller'];
  readonly channels: Channel[];
  readonly byJoint = new Map<string, Channel>();
  readonly byNumber = new Map<number, Channel>();
  readonly frequencyHz: number;
  /** Controller resolution in us per unit of Frame.targets. */
  readonly usPerUnit: number;
  plantEnabled = true;
  frameNo = 0;
  simTime = 0;
  lastFrame: Frame = { frame: 0, t: 0, targets: [] };
  private readonly log: Frame[] = [];
  private pending: { at: number; targets: number[] }[] = [];
  private acc = 0;
  private tick = 0;

  constructor(joints: JointSpec[], dynamics: Record<string, JointDynamics>, profile = DEFAULT_PROFILE) {
    const p = resolveProfile(profile);
    this.profileName = profile;
    this.label = p.label;
    this.controller = p.controller;
    if (p.controller.type === 'pca9685') {
      const osc = p.controller.oscillatorHz ?? 25_000_000;
      const prescale = Math.round(osc / (4096 * 50)) - 1;
      this.frequencyHz = osc / (4096 * (prescale + 1));
      this.usPerUnit = 1e6 / this.frequencyHz / 4096;
    } else if (p.controller.type === 'maestro') {
      this.frequencyHz = 1000 / (p.controller.periodMs ?? 20);
      this.usPerUnit = 0.25; // Maestro targets are quarter-microseconds
    } else {
      this.frequencyHz = p.controller.frameHz ?? 50;
      this.usPerUnit = p.controller.resolutionUs ?? 1;
    }
    const specs = new Map(joints.map((j) => [j.name, j]));
    this.channels = p.channels.map((c) => new Channel(c, specs, dynamics, p.supplyVolts));
    for (const ch of this.channels) {
      this.byNumber.set(ch.cfg.ch, ch);
      for (const j of Object.keys(ch.cfg.joints)) this.byJoint.set(j, ch);
    }
  }

  /** Behaviour/manual input: desired joint value. Coupled joints follow their primary. */
  command(joint: string, value: number) {
    const ch = this.byJoint.get(joint);
    if (ch && ch.primaryJoint === joint && ch.directUs === null) ch.follower.setTarget(value);
  }

  /** Direct target in quarter-microseconds (Maestro script units; 0 = off). */
  setTarget(channel: number, quarterUs: number) {
    const ch = this.byNumber.get(channel);
    if (!ch) return false;
    ch.directUs = quarterUs / 4;
    return true;
  }

  setMaestroLimits(channel: number, speed?: number, accel?: number) {
    const ch = this.byNumber.get(channel);
    if (!ch) return;
    if (speed !== undefined) ch.cfg.maestroSpeed = speed;
    if (accel !== undefined) ch.cfg.maestroAccel = accel;
  }

  releaseAll() {
    for (const ch of this.channels) ch.releaseDirect();
  }

  update(dt: number) {
    this.acc += Math.min(dt, 0.1);
    const step = 1 / PLANT_HZ;
    const frameEvery = Math.round(PLANT_HZ / this.frequencyHz);
    while (this.acc >= step) {
      this.acc -= step;
      this.simTime += step;
      this.tick++;
      if (this.tick % TRAJ_EVERY === 0) {
        for (const ch of this.channels) ch.follower.step(TRAJ_EVERY * step);
      }
      for (const ch of this.channels) ch.us = ch.pulseNow(step);
      if (this.tick % frameEvery === 0) this.emitFrame();
      while (this.pending.length && this.pending[0].at <= this.simTime) {
        const f = this.pending.shift()!;
        for (const ch of this.channels) {
          const t = f.targets[ch.cfg.ch];
          if (t) ch.applyPulse(t * this.usPerUnit);
        }
      }
      for (const ch of this.channels) {
        if (this.plantEnabled) ch.plantStep(step);
        else ch.snap();
      }
    }
  }

  private emitFrame() {
    const targets = new Array(this.controller.channels).fill(0);
    for (const ch of this.channels) {
      ch.target = Math.round(ch.us / this.usPerUnit);
      targets[ch.cfg.ch] = ch.target;
    }
    this.frameNo++;
    this.lastFrame = { frame: this.frameNo, t: +this.simTime.toFixed(3), targets };
    this.log.push(this.lastFrame);
    if (this.log.length > 3000) this.log.shift(); // last 60 s
    // Servo PWM free-runs: a new target reaches the servo 0-20 ms later.
    const latency = this.plantEnabled ? Math.random() / this.frequencyHz : 0;
    this.pending.push({ at: this.simTime + latency, targets });
  }

  /** Joint values to pose the rig with (coupled joints scaled by their factor). */
  jointValues(): Map<string, number> {
    const out = new Map<string, number>();
    for (const ch of this.channels) {
      for (const [j, k] of Object.entries(ch.cfg.joints)) out.set(j, ch.value * k);
    }
    return out;
  }

  /** The frame log as JSONL: what the hardware sink would have sent, for replay. */
  exportLog(): string {
    const header = {
      profile: this.profileName, controller: this.controller.type, hz: +this.frequencyHz.toFixed(3),
      usPerUnit: +this.usPerUnit.toFixed(4),
      channels: Object.fromEntries(this.channels.map((c) => [c.cfg.ch, c.cfg.name])),
    };
    return [JSON.stringify(header), ...this.log.map((f) => JSON.stringify(f))].join('\n');
  }
}
