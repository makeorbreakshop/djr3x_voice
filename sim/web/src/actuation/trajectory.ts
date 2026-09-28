/**
 * Online jerk-limited trajectory following with soft-limit braking.
 *
 * Trapezoidal profiles have infinite jerk at every corner - the visible "clunk" that
 * excites wobble in printed arms on thin rods. This follower limits velocity,
 * acceleration AND jerk, retargets every tick (gaze and speech layers move the target
 * continuously), and brakes for soft limits like a wall seen in advance.
 *
 * Construction (chosen because every limit is provable, not approximate):
 *   1. an exact trapezoidal follower (v <= vMax, |a| <= aMax, braking v = sqrt(2 a d)
 *      toward the target and toward each soft limit), then
 *   2. a critically damped second-order filter on its output with
 *      omega = e * jMax / (2 aMax).
 * A critically damped filter is monotone, so it never overshoots and never exceeds the
 * input's velocity or acceleration. Its peak jerk for an acceleration step S is
 * S * omega / e; the trapezoid's largest possible step is 2 aMax (accelerate straight
 * into braking), so this omega bounds jerk at jMax in every case. Cost: a lag of about
 * 2/omega (~0.2 s at the default limits) - the price of jerk-free starts and stops.
 *
 * (An earlier single-stage "brake-speed" follower from the research brief overshot a
 * 60 deg move by 5 deg because its braking distance ignores acceleration already in
 * progress - see test/actuation.test.ts.)
 */

export interface MotionLimits {
  vMax: number; // units/s (deg or mm)
  aMax: number; // units/s^2
  jMax: number; // units/s^3
}

const clamp = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x);

/** Speed from which you can still stop within distance d at deceleration a. */
export function brakeSpeed(d: number, a: number) {
  return Math.sqrt(2 * a * Math.max(0, d));
}

export class JerkLimitedFollower {
  /** Filtered output: what goes to the output stage. */
  x = 0;
  v = 0;
  a = 0;
  target = 0;
  // stage 1: trapezoid
  private x1 = 0;
  private v1 = 0;

  constructor(public limits: MotionLimits, public softMin: number, public softMax: number, x0 = 0) {
    this.x = this.x1 = this.target = clamp(x0, softMin, softMax);
  }

  setTarget(t: number) {
    this.target = clamp(t, this.softMin, this.softMax);
  }

  /** Jump the whole state to a position at rest (e.g. when a script hands back control). */
  reset(x: number) {
    this.x = this.x1 = this.target = clamp(x, this.softMin, this.softMax);
    this.v = this.v1 = this.a = 0;
  }

  step(dt: number) {
    const { vMax, aMax, jMax } = this.limits;

    // Stage 1: exact trapezoid, braking for the target and for both soft limits.
    const e = this.target - this.x1;
    let vDes = Math.sign(e) * Math.min(vMax, brakeSpeed(Math.abs(e), aMax));
    vDes = Math.min(vDes, brakeSpeed(this.softMax - this.x1, aMax));
    vDes = Math.max(vDes, -brakeSpeed(this.x1 - this.softMin, aMax));
    this.v1 += clamp(vDes - this.v1, -aMax * dt, aMax * dt);
    this.x1 += this.v1 * dt;
    if (Math.abs(this.target - this.x1) < 1e-3 && Math.abs(this.v1) < aMax * dt) {
      this.x1 = this.target;
      this.v1 = 0;
    }
    this.x1 = clamp(this.x1, this.softMin, this.softMax);

    // Stage 2: critically damped filter (exact update, stable at any dt).
    const omega = (Math.E * jMax) / (2 * aMax);
    const vPrev = this.v;
    const s = { x: this.x, v: this.v };
    springStep(s, this.x1, omega, dt);
    this.x = clamp(s.x, this.softMin, this.softMax);
    this.v = s.v;
    this.a = (this.v - vPrev) / dt;
  }
}

/**
 * Critically damped spring, exact for any dt (Holden, "Spring-It-On").
 * omega = 5.8 / settleTime for 2% settle.
 */
export function springStep(state: { x: number; v: number }, goal: number, omega: number, dt: number) {
  const j0 = state.x - goal;
  const j1 = state.v + j0 * omega;
  const e = Math.exp(-omega * dt);
  state.x = e * (j0 + j1 * dt) + goal;
  state.v = e * (state.v - j1 * omega * dt);
}

/** Minimum-jerk (Flash & Hogan) duration that respects all three limits for distance D. */
export function minJerkDuration(D: number, l: MotionLimits) {
  const d = Math.abs(D);
  return Math.max((1.875 * d) / l.vMax, Math.sqrt((5.77 * d) / l.aMax), Math.cbrt((60 * d) / l.jMax));
}
