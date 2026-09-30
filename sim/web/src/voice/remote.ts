/**
 * Remote voice over the r3x gateway (plan §3b `audio`, Phase 2): hold-to-talk streams this
 * browser's mic as 16 kHz mono PCM in 20 ms frames; R3X's speech comes back as 24 kHz PCM and
 * is played gaplessly. Push-to-talk ownership is `state.conversation.ptt_owner` on the runtime.
 *
 * getUserMedia needs a secure context: localhost, or HTTPS (e.g. `tailscale serve`) from
 * another machine - and then the gateway must be reached as wss:// too.
 */

import type { Ack, AudioMeta, GatewayClient } from '../gateway';

const MIC_RATE = 16000;
const FRAME = MIC_RATE / 50; // 20 ms

/** Captures raw Float32 blocks from the audio graph. */
const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('r3x-mic-tap', Tap);
`;

/** Streaming linear resampler to 16 kHz with a box pre-filter when downsampling. */
export class Downsampler {
  private pos = 0;
  private buf: number[] = [];
  constructor(private readonly from: number, private readonly to = MIC_RATE) {}

  push(input: Float32Array): Float32Array {
    if (this.from === this.to) return input;
    const ratio = this.from / this.to;
    const w = Math.max(1, Math.floor(ratio));
    for (let i = 0; i < input.length; i++) this.buf.push(input[i]);
    const out: number[] = [];
    while (this.pos + w < this.buf.length) {
      const i = Math.floor(this.pos);
      let acc = 0;
      for (let k = 0; k < w; k++) acc += this.buf[i + k];
      out.push(acc / w);
      this.pos += ratio;
    }
    const used = Math.floor(this.pos);
    this.buf = this.buf.slice(used);
    this.pos -= used;
    return Float32Array.from(out);
  }
}

export function toInt16(f: Float32Array): Int16Array<ArrayBuffer> {
  const o = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) o[i] = Math.max(-32768, Math.min(32767, Math.round(f[i] * 32767)));
  return o;
}

export interface RemoteVoiceEvents {
  onState?(s: 'idle' | 'starting' | 'listening' | 'refused', detail?: string): void;
  onLevel?(rms: number): void;
}

export class RemoteVoice {
  private mic?: { ctx: AudioContext; stream: MediaStream; ds: Downsampler };
  private out?: AudioContext;
  private nextAt = 0;
  private playing: AudioBufferSourceNode[] = [];
  private held = false;
  private live = false;
  private pending: Int16Array<ArrayBuffer>[] = [];
  private rest: number[] = [];

  constructor(private readonly gw: GatewayClient, private readonly ev: RemoteVoiceEvents = {}) {
    gw.subscribe({ onAudio: (meta, pcm) => this.play(meta, pcm) });
  }

  /** Ask for the mic once (needs a user gesture); later presses start instantly. */
  private async ensureMic() {
    if (this.mic) return this.mic;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    let ctx: AudioContext;
    try {
      ctx = new AudioContext({ sampleRate: MIC_RATE }); // the browser resamples the mic
    } catch {
      ctx = new AudioContext();
    }
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
    await ctx.audioWorklet.addModule(url);
    const src = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, 'r3x-mic-tap');
    const ds = new Downsampler(ctx.sampleRate);
    node.port.onmessage = (m) => this.onBlock(ds.push(m.data as Float32Array));
    src.connect(node);
    this.mic = { ctx, stream, ds };
    return this.mic;
  }

  private onBlock(f: Float32Array) {
    if (!this.held) return;
    let sum = 0;
    for (let i = 0; i < f.length; i++) {
      this.rest.push(f[i]);
      sum += f[i] * f[i];
    }
    this.ev.onLevel?.(Math.sqrt(sum / Math.max(1, f.length)));
    while (this.rest.length >= FRAME) {
      const frame = toInt16(Float32Array.from(this.rest.splice(0, FRAME)));
      if (this.live) this.gw.sendAudio(frame);
      else this.pending.push(frame); // until the runtime accepts the turn
    }
  }

  async press(): Promise<Ack> {
    if (this.held) return { status: 'accepted' };
    this.held = true;
    this.live = false;
    this.pending = [];
    this.rest = [];
    this.ev.onState?.('starting');
    this.unlockOutput();
    try {
      const mic = await this.ensureMic();
      await mic.ctx.resume();
    } catch (e) {
      this.held = false;
      this.ev.onState?.('refused', `microphone unavailable: ${e}`);
      return { status: 'rejected', reason: String(e) };
    }
    const ack = await this.gw.send({ class: 'intent', type: 'ptt_start' });
    if (ack.status !== 'accepted' || !this.held) {
      this.held = false;
      this.ev.onState?.('refused', ack.status === 'rejected' ? ack.reason : 'released');
      if (ack.status === 'accepted') void this.gw.send({ class: 'intent', type: 'ptt_stop' });
      return ack;
    }
    const meta: AudioMeta = { direction: 'in', sample_rate: MIC_RATE, channels: 1 };
    this.gw.sendAudioMeta(meta);
    for (const f of this.pending) this.gw.sendAudio(f);
    this.pending = [];
    this.live = true;
    this.ev.onState?.('listening');
    return ack;
  }

  async release(): Promise<Ack> {
    if (!this.held) return { status: 'accepted' };
    const wasLive = this.live;
    this.held = false;
    this.live = false;
    if (wasLive && this.rest.length) this.gw.sendAudio(toInt16(Float32Array.from(this.rest)));
    this.rest = [];
    this.ev.onState?.('idle');
    return wasLive ? this.gw.send({ class: 'intent', type: 'ptt_stop' }) : { status: 'accepted' };
  }

  /** Browsers only start audio output from a user gesture. */
  private unlockOutput() {
    this.out ??= new AudioContext();
    void this.out.resume();
  }

  private play(meta: AudioMeta | null, pcm: ArrayBuffer) {
    if (!meta || meta.direction !== 'out') return;
    const ctx = (this.out ??= new AudioContext());
    if (meta.channels === 0) {
      // Abort: stop what is queued.
      for (const s of this.playing) s.stop();
      this.playing = [];
      this.nextAt = 0;
      return;
    }
    if (pcm.byteLength < 2) return; // end of line
    const i16 = new Int16Array(pcm);
    const buf = ctx.createBuffer(1, i16.length, meta.sample_rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    // The runtime paces frames ~150 ms ahead; keep a small jitter buffer at line starts.
    const now = ctx.currentTime;
    if (this.nextAt < now) this.nextAt = now + 0.08;
    src.start(this.nextAt);
    this.nextAt += buf.duration;
    this.playing.push(src);
    src.onended = () => {
      this.playing = this.playing.filter((s) => s !== src);
    };
  }
}
