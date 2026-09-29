/**
 * Speech audio for the sim: plays a clip (or listens to the mic) and reports RMS on the
 * int16 scale, the quantity ElevenLabsService feeds into its AGC. The analyser window is
 * 1024 samples (~21 ms at 48 kHz), close to the service's ~16.7 ms PCM chunks.
 */
export class SpeechAudio {
  private ctx?: AudioContext;
  private analyser?: AnalyserNode;
  private buf = new Float32Array(1024);
  private el?: HTMLAudioElement;
  private micStream?: MediaStream;
  private micSource?: MediaStreamAudioSourceNode;
  active = false;

  private ensure() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.buf = new Float32Array(this.analyser.fftSize);
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume();
    return { ctx: this.ctx, analyser: this.analyser! };
  }

  /** Play a clip; resolves when it ends (or is stopped). */
  play(url: string, onStart?: () => void): Promise<void> {
    this.stop();
    const { ctx, analyser } = this.ensure();
    const el = new Audio(url);
    el.crossOrigin = 'anonymous';
    this.el = el;
    const src = ctx.createMediaElementSource(el);
    src.connect(analyser);
    analyser.connect(ctx.destination);
    return new Promise((resolve) => {
      const done = () => {
        if (this.el === el) {
          this.active = false;
          this.el = undefined;
        }
        src.disconnect();
        resolve();
      };
      el.addEventListener('ended', done, { once: true });
      el.addEventListener('pause', done, { once: true });
      el.addEventListener('error', done, { once: true });
      el.play().then(() => {
        this.active = true;
        onStart?.();
      }, done);
    });
  }

  async startMic(): Promise<void> {
    this.stop();
    const { ctx, analyser } = this.ensure();
    this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.micSource = ctx.createMediaStreamSource(this.micStream);
    // Analyse only - do not route the mic to the speakers.
    analyser.disconnect();
    this.micSource.connect(analyser);
    this.active = true;
  }

  stop() {
    if (this.el) {
      const el = this.el;
      this.el = undefined;
      el.pause();
    }
    if (this.micStream) {
      this.micSource?.disconnect();
      this.micStream.getTracks().forEach((t) => t.stop());
      this.micStream = undefined;
      this.micSource = undefined;
    }
    this.active = false;
  }

  get micOn() {
    return !!this.micStream;
  }

  /** Current RMS on the int16 scale (0..32768). */
  rmsInt16(): number {
    if (!this.analyser || !this.active) return 0;
    this.analyser.getFloatTimeDomainData(this.buf);
    let s = 0;
    for (let i = 0; i < this.buf.length; i++) s += this.buf[i] * this.buf[i];
    return Math.sqrt(s / this.buf.length) * 32768;
  }
}

/**
 * ElevenLabsService's per-chunk amplitude: RMS -> dBFS -> AGC-normalised 0..1 (the
 * performer's `amplitude` command). Feed it int16-scale RMS values, one per ~16.7 ms chunk.
 */
export class TtsAmplitudeAgc {
  private recent: number[] = [];
  private readonly window = 30;
  private readonly minRangeDb = 12;

  next(rmsInt16: number): number {
    if (rmsInt16 <= 0) return 0;
    const db = 20 * Math.log10(rmsInt16 / 32768);
    this.recent.push(db);
    if (this.recent.length > this.window) this.recent.shift();
    let a: number;
    if (this.recent.length >= 10) {
      const lo = Math.min(...this.recent);
      const hi = Math.max(...this.recent);
      const range = Math.max(this.minRangeDb, hi - lo);
      a = Math.max(0, Math.min(1, (db - lo) / range));
    } else {
      a = Math.max(0, Math.min(1, (db + 50) / 40));
    }
    return Math.min(1, a * 2);
  }

  reset() {
    this.recent = [];
  }
}
