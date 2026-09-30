/**
 * Servo/gear whine (as Disney's BD-X sim does): a quiet synthesized hum whose loudness and
 * pitch follow the summed joint speed in the performer's frames. It sells the
 * physicality of a move - and tells you by ear when a clip is working the servos hard.
 * Off by default; the audio graph is only built on the first enable (a user gesture).
 */
export class ServoWhine {
  private ctx?: AudioContext;
  private gain?: GainNode;
  private oscA?: OscillatorNode;
  private oscB?: OscillatorNode;
  private filter?: BiquadFilterNode;
  private level = 0;
  enabled = false;
  /** Summed joint speed that counts as "working hard" (deg/s). */
  fullScale = 450;

  setEnabled(on: boolean) {
    this.enabled = on;
    if (on && !this.ctx) this.build();
    if (this.ctx?.state === 'suspended') void this.ctx.resume();
    if (!on && this.gain && this.ctx) this.gain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.05);
  }

  private build() {
    const ctx = new AudioContext();
    this.ctx = ctx;
    this.gain = ctx.createGain();
    this.gain.gain.value = 0;
    this.filter = ctx.createBiquadFilter();
    this.filter.type = 'bandpass';
    this.filter.Q.value = 3;
    this.oscA = ctx.createOscillator();
    this.oscA.type = 'sawtooth';
    this.oscB = ctx.createOscillator();
    this.oscB.type = 'square';
    const mixB = ctx.createGain();
    mixB.gain.value = 0.35;
    this.oscA.connect(this.filter);
    this.oscB.connect(mixB).connect(this.filter);
    this.filter.connect(this.gain).connect(ctx.destination);
    this.oscA.start();
    this.oscB.start();
  }

  /** `speed`: summed |joint speed| (deg/s) from successive frames. */
  update(speed: number, dt: number) {
    if (!this.enabled || !this.ctx || !(dt > 0)) return;
    const x = Math.min(1, speed / this.fullScale);
    this.level += (x - this.level) * (1 - Math.exp(-dt / 0.04));
    const t = this.ctx.currentTime;
    // Quiet: at most ~-24 dBFS, and silent below a small deadband (holding still hums nothing).
    const g = this.level < 0.02 ? 0 : 0.06 * Math.pow(this.level, 0.8);
    this.gain!.gain.setTargetAtTime(g, t, 0.03);
    const f = 160 + 560 * this.level;
    this.oscA!.frequency.setTargetAtTime(f, t, 0.03);
    this.oscB!.frequency.setTargetAtTime(f * 1.498, t, 0.03);
    this.filter!.frequency.setTargetAtTime(700 + 2200 * this.level, t, 0.05);
  }
}
