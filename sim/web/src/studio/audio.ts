/**
 * Studio's audio lane: a library track (dev-server `/studio/library`, beat grid from the
 * r3x-beats cache) or a local file, decoded once for a min/max waveform, played through
 * Web Audio in sync with the transport. Only a human's click starts sound.
 */

export interface LibraryTrack { name: string; url: string; bpm?: number; first_beat_s?: number; beats?: number[] }

export interface LoadedAudio {
  name: string;
  buffer: AudioBuffer;
  /** Min/max per bucket, `PEAK_HZ` buckets a second. */
  peaks: Float32Array;
  bpm: number | null;
  firstBeat: number;
  /** Cached beat times (s); empty = a grid from bpm + firstBeat. */
  beats: number[];
}

export const PEAK_HZ = 200;

export async function fetchLibrary(): Promise<LibraryTrack[]> {
  try {
    const r = await fetch('/studio/library');
    return r.ok ? ((await r.json()) as LibraryTrack[]) : [];
  } catch {
    return [];
  }
}

let ctx: AudioContext | null = null;
export const audioCtx = () => (ctx ??= new AudioContext());

export function peaksOf(buf: AudioBuffer): Float32Array {
  const n = Math.ceil(buf.duration * PEAK_HZ);
  const out = new Float32Array(n * 2);
  const per = buf.sampleRate / PEAK_HZ;
  const chans = Array.from({ length: buf.numberOfChannels }, (_, c) => buf.getChannelData(c));
  for (let b = 0; b < n; b++) {
    let lo = 0, hi = 0;
    const end = Math.min(buf.length, Math.floor((b + 1) * per));
    for (let i = Math.floor(b * per); i < end; i++) {
      let x = 0;
      for (const ch of chans) x += ch[i];
      x /= chans.length;
      if (x < lo) lo = x;
      if (x > hi) hi = x;
    }
    out[b * 2] = lo;
    out[b * 2 + 1] = hi;
  }
  return out;
}

export async function loadAudio(name: string, data: ArrayBuffer, meta?: Partial<LibraryTrack>): Promise<LoadedAudio> {
  const buffer = await audioCtx().decodeAudioData(data);
  return { name, buffer, peaks: peaksOf(buffer), bpm: meta?.bpm ?? null, firstBeat: meta?.first_beat_s ?? 0, beats: meta?.beats ?? [] };
}

/** Beat times inside [a, b]: the cached grid, else bpm + first beat. */
export function beatsIn(au: Pick<LoadedAudio, 'bpm' | 'firstBeat' | 'beats'>, a: number, b: number): number[] {
  if (au.beats.length) return au.beats.filter((t) => t >= a && t <= b);
  if (!au.bpm) return [];
  const p = 60 / au.bpm;
  const out: number[] = [];
  for (let t = au.firstBeat + Math.ceil((a - au.firstBeat) / p) * p; t <= b; t += p) out.push(t);
  return out;
}

/** One playing source; `stop()` is idempotent. */
export class AudioPlayer {
  private src: AudioBufferSourceNode | null = null;
  private startedAt = 0;
  private offset = 0;

  play(buf: AudioBuffer, at: number) {
    this.stop();
    const c = audioCtx();
    void c.resume();
    const s = c.createBufferSource();
    s.buffer = buf;
    s.connect(c.destination);
    s.start(0, Math.max(0, at));
    this.src = s;
    this.startedAt = c.currentTime;
    this.offset = at;
  }

  /** Transport time from the audio clock (sample-accurate, no drift against the sound). */
  time(): number | null {
    return this.src ? this.offset + (audioCtx().currentTime - this.startedAt) : null;
  }

  stop() {
    try { this.src?.stop(); } catch { /* already stopped */ }
    this.src?.disconnect();
    this.src = null;
  }
}

/** Voice line: character start times, uniform over `dur` s (or at `cps` chars/s). */
export function charTimings(text: string, opts: { dur?: number; cps?: number; at?: number }): number[] {
  const n = text.length;
  const step = opts.dur ? opts.dur / Math.max(1, n) : 1 / (opts.cps ?? 13);
  return Array.from({ length: n }, (_, i) => (opts.at ?? 0) + i * step);
}
