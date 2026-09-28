/**
 * Hobby servo datasheet values. Sources in sim/docs/motion-control.md; numbers marked
 * "measure" in that doc (backlash, tau) are estimates until bench-tested.
 */
export interface ServoModel {
  label: string;
  /** Mechanical travel for the full pulse range. */
  rangeDeg: number;
  minUs: number;
  maxUs: number;
  /** s per 60 deg, no load, at the two rated voltages. */
  speed: [volts: number, sPer60: number][];
  /** Stall torque, kg.cm, at the same voltages. */
  stall: [volts: number, kgcm: number][];
  deadbandUs: number;
  /** Gear-train play at the horn, deg (measure). */
  backlashDeg: number;
  /** Small-signal time constant of the internal P loop, s (measure). */
  tauS: number;
}

export const SERVOS: Record<string, ServoModel> = {
  MG996R: {
    label: 'MG996R', rangeDeg: 180, minUs: 500, maxUs: 2500,
    speed: [[4.8, 0.17], [6.0, 0.14]], stall: [[4.8, 9.4], [6.0, 11]],
    deadbandUs: 5, backlashDeg: 1.0, tauS: 0.05,
  },
  DS3218: {
    label: 'DS3218 (180)', rangeDeg: 180, minUs: 500, maxUs: 2500,
    speed: [[5.0, 0.16], [6.8, 0.14]], stall: [[5.0, 19], [6.8, 21.5]],
    deadbandUs: 3, backlashDeg: 0.5, tauS: 0.04,
  },
  DS3218_270: {
    label: 'DS3218 (270)', rangeDeg: 270, minUs: 500, maxUs: 2500,
    speed: [[5.0, 0.16], [6.8, 0.14]], stall: [[5.0, 19], [6.8, 21.5]],
    deadbandUs: 3, backlashDeg: 0.5, tauS: 0.04,
  },
  MG90S: {
    label: 'MG90S', rangeDeg: 180, minUs: 500, maxUs: 2400,
    speed: [[4.8, 0.1], [6.0, 0.08]], stall: [[4.8, 1.8], [6.0, 2.2]],
    deadbandUs: 5, backlashDeg: 1.0, tauS: 0.03,
  },
  // Classes from the R-3X Animation parts list (Amazon listings, exact models unconfirmed).
  // Typical figures for the class; replace with the datasheet of the servo actually fitted.
  SERVO_35KG_270: {
    label: '35 kg 270deg', rangeDeg: 270, minUs: 500, maxUs: 2500,
    speed: [[5.0, 0.16], [7.4, 0.11]], stall: [[5.0, 25], [7.4, 35]],
    deadbandUs: 3, backlashDeg: 0.6, tauS: 0.045,
  },
  SERVO_60KG_270: {
    label: '60 kg 270deg', rangeDeg: 270, minUs: 500, maxUs: 2500,
    speed: [[6.0, 0.2], [8.4, 0.15]], stall: [[6.0, 50], [8.4, 60]],
    deadbandUs: 3, backlashDeg: 0.8, tauS: 0.06,
  },
  DS3218_DUAL: {
    label: '20 kg dual-shaft (DS3218-class)', rangeDeg: 180, minUs: 500, maxUs: 2500,
    speed: [[5.0, 0.16], [6.8, 0.14]], stall: [[5.0, 19], [6.8, 21.5]],
    deadbandUs: 3, backlashDeg: 0.5, tauS: 0.04,
  },
  SERVO_7KG: {
    label: '7 kg', rangeDeg: 180, minUs: 500, maxUs: 2500,
    speed: [[4.8, 0.12], [6.0, 0.1]], stall: [[4.8, 6], [6.0, 7]],
    deadbandUs: 4, backlashDeg: 1.0, tauS: 0.035,
  },
  SG90: {
    label: 'SG90', rangeDeg: 180, minUs: 500, maxUs: 2400,
    // Claimed 0.1 s/60; ServoEasing measured ~450 deg/s.
    speed: [[4.8, 0.133], [6.0, 0.12]], stall: [[4.8, 1.8], [6.0, 1.8]],
    deadbandUs: 10, backlashDeg: 1.5, tauS: 0.03,
  },
};

function interp(table: [number, number][], v: number) {
  const [a, b] = table;
  if (!b) return a[1];
  const t = Math.min(1, Math.max(0, (v - a[0]) / (b[0] - a[0])));
  return a[1] + (b[1] - a[1]) * t;
}

/** No-load speed, deg/s, at supply volts. */
export const noLoadSpeed = (m: ServoModel, volts: number) => 60 / interp(m.speed, volts);
/** Stall torque, kg.cm, at supply volts. */
export const stallTorque = (m: ServoModel, volts: number) => interp(m.stall, volts);
export const usPerDeg = (m: ServoModel) => (m.maxUs - m.minUs) / m.rangeDeg;
