import * as THREE from 'three';
import type { PostPipeline, RenderProbe } from './post';

/**
 * Frame diagnostics (Scene -> Overlays -> Frame stats): what a frame costs and why frames come
 * as often as they do.
 *
 *   fps, pacing   drawn frames per second, and the pacer's state (pacer.ts): a quiet 15 fps is
 *                 the pacing working, not the renderer struggling
 *   frame         interval between drawn frames over the last 5 s: avg / p95 / worst, sparkline
 *                 (the dashed line is the pacer's target interval)
 *   cpu / gpu     JS time in post.render(); GPU time from EXT_disjoint_timer_query_webgl2 when
 *                 the browser exposes it (one query per post pass, read back a few frames late).
 *                 Some drivers answer with wall time (Chrome on ANGLE/Metal: every pass ~7 ms,
 *                 summing to more than the frame interval at a steady 60 fps). A GPU sum over
 *                 the frame interval while drawing flat out is impossible, so it is marked
 *                 unreliable for the session and the passes fall back to CPU (submit) time.
 *   counters      draw calls and triangles across the whole frame (every pass), textures and
 *                 geometries in memory
 *   scale         render scale, device pixel ratio, drawing buffer, quality
 *   passes        the composer's passes in order, each with its GPU (else CPU) cost
 *   heap          JS heap, where the browser reports it (Chrome)
 *
 * "Profile 10 s" draws every animation frame for ten seconds and reports avg / p95 fps and
 * the biggest cost; the summary is also logged to the console, for sharing.
 *
 * Nothing is hooked while the overlay is closed: the post passes are wrapped and the renderer's
 * counters switched to per-frame only while it is open.
 */

const WINDOW_MS = 5000;

interface TimerExt {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
}

interface PassWrap {
  name: string;
  pass: { render: (...a: unknown[]) => void };
  orig: (...a: unknown[]) => void;
}

interface Sample {
  at: number;
  interval: number;
  cpu: number;
}

/**
 * A profiling run: the whole picture first, then (when they are on) a second and a half each
 * with AO and with bloom switched off, so their cost shows as frame time saved even where the
 * GPU timer is missing or unreliable. The first 0.3 s of each phase is not counted (shader
 * switch, settling).
 */
interface Phase {
  name: string;
  ms: number;
  set?: (on: boolean) => void;
  from?: number;
  intervals: number[];
}

interface Profile {
  phases: Phase[];
  i: number;
  phaseEnd: number;
  samples: Sample[];
  gpu: Map<string, number[]>;
  cpu: Map<string, number[]>;
}

const pct = (sorted: number[], p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] ?? 0;
const avg = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
const fmtN = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(0)}k` : String(n));

export class FrameDiag implements RenderProbe {
  private readonly el: HTMLElement;
  private readonly spark: HTMLCanvasElement;
  private readonly lines: Record<string, HTMLElement> = {};
  private samples: Sample[] = [];
  private lastStart = -1;
  private t0 = 0;
  private calls = 0;
  private tris = 0;
  private wraps: PassWrap[] = [];
  private passCpu = new Map<string, number[]>();
  private passGpu = new Map<string, number[]>();
  private ext: TimerExt | null = null;
  private gl2: WebGL2RenderingContext | null = null;
  private pending: { q: WebGLQuery; name: string }[] = [];
  private pool: WebGLQuery[] = [];
  private active = false;
  private timer = 0;
  private profile: Profile | null = null;
  private autoReset = true;
  /** GPU timer answers that cannot be true (see the header); sticky for the session. */
  private gpuBogus = false;
  private readonly buf = new THREE.Vector2();

  constructor(private readonly post: PostPipeline, el: HTMLElement) {
    this.el = el;
    el.className = 'frame-diag';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-label', 'Frame diagnostics');
    el.innerHTML = `<div class="fd-head"><span class="fd-fps">…</span><span class="fd-pace"></span>
<button type="button" title="Draw every frame for 10 s and summarise (also logged to the console)">Profile 10 s</button></div>
<canvas width="280" height="28" aria-hidden="true"></canvas>
<p data-l="frame"></p><p data-l="time"></p><p data-l="count"></p><p data-l="scale"></p>
<p data-l="passes" class="fd-passes"></p><p data-l="heap"></p><p class="fd-summary" hidden></p>`;
    this.spark = el.querySelector('canvas')!;
    el.querySelectorAll<HTMLElement>('[data-l]').forEach((p) => (this.lines[p.dataset.l!] = p));
    this.lines.summary = el.querySelector('.fd-summary')!;
    el.querySelector('button')!.onclick = (e) => {
      this.runProfile();
      (e.currentTarget as HTMLElement).blur();
    };
  }

  private get renderer(): THREE.WebGLRenderer {
    return this.post.gl;
  }

  setEnabled(on: boolean) {
    if (on === this.active) return;
    this.active = on;
    this.el.hidden = !on;
    const info = this.renderer.info;
    clearInterval(this.timer);
    if (on) {
      this.autoReset = info.autoReset;
      info.autoReset = false; // count the whole frame (every pass), reset at frameStart
      this.samples = [];
      this.lastStart = -1;
      const gl = this.renderer.getContext();
      if (typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext) {
        this.gl2 = gl;
        this.ext = gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerExt | null;
      }
      this.wrapPasses();
      this.post.probe = this;
      this.timer = window.setInterval(() => this.draw(), 500);
      this.draw();
    } else {
      this.post.probe = null;
      info.autoReset = this.autoReset;
      this.unwrapPasses();
      for (const { q } of this.pending) this.gl2?.deleteQuery(q);
      for (const q of this.pool) this.gl2?.deleteQuery(q);
      this.pending = [];
      this.pool = [];
      this.post.pacer.continuous(0);
      if (this.profile) this.profile.phases[this.profile.i].set?.(true);
      this.profile = null;
    }
  }

  // ---------------------------------------------------------------- probes

  /** Pass labels by pipeline field (class names do not survive bundling). */
  private static readonly LABELS: Record<string, string> = {
    ao: 'AO+scene', renderPass: 'scene', bloom: 'bloom', output: 'output', smaa: 'SMAA', lut: 'LUT', film: 'film', hot: 'hot',
  };

  private passName(p: object): string {
    const post = this.post as unknown as Record<string, unknown>;
    for (const [k, label] of Object.entries(FrameDiag.LABELS)) if (post[k] === p) return label;
    return 'other';
  }

  /** Every pass the pipeline may use (not only the current list: quality and toggles rebuild it). */
  private wrapPasses() {
    const post = this.post as unknown as Record<string, unknown>;
    const all = new Set<object>([
      ...this.post.composer.passes,
      ...Object.keys(FrameDiag.LABELS).map((k) => post[k]).filter(Boolean) as object[],
    ]);
    for (const pass of all as Set<PassWrap['pass']>) {
      const name = this.passName(pass);
      const orig = pass.render;
      pass.render = (...a: unknown[]) => {
        const q = this.beginQuery();
        const t = performance.now();
        orig.apply(pass, a);
        this.push(this.passCpu, name, performance.now() - t);
        if (q) this.endQuery(q, name);
      };
      this.wraps.push({ name, pass, orig });
    }
  }

  private unwrapPasses() {
    for (const w of this.wraps) delete (w.pass as Partial<PassWrap['pass']>).render;
    // Passes whose render was an own property (none today) get it back as it was.
    for (const w of this.wraps) if (w.pass.render !== w.orig) w.pass.render = w.orig;
    this.wraps = [];
  }

  private beginQuery(): WebGLQuery | null {
    const gl = this.gl2;
    if (!gl || !this.ext) return null;
    const q = this.pool.pop() ?? gl.createQuery();
    if (!q) return null;
    gl.beginQuery(this.ext.TIME_ELAPSED_EXT, q);
    return q;
  }

  private endQuery(q: WebGLQuery, name: string) {
    this.gl2!.endQuery(this.ext!.TIME_ELAPSED_EXT);
    this.pending.push({ q, name });
  }

  /** Read back finished queries (a few frames late; all dropped on a disjoint event). */
  private poll() {
    const gl = this.gl2;
    if (!gl || !this.ext || !this.pending.length) return;
    const disjoint = gl.getParameter(this.ext.GPU_DISJOINT_EXT) as boolean;
    let i = 0;
    for (; i < this.pending.length; i++) {
      const { q, name } = this.pending[i];
      if (!gl.getQueryParameter(q, gl.QUERY_RESULT_AVAILABLE)) break;
      if (!disjoint) this.push(this.passGpu, name, (gl.getQueryParameter(q, gl.QUERY_RESULT) as number) / 1e6);
      this.pool.push(q);
    }
    this.pending.splice(0, i);
    // A context that never answers: don't grow without bound.
    if (this.pending.length > 400) this.pending.splice(0, this.pending.length - 400).forEach(({ q }) => gl.deleteQuery(q));
  }

  private push(into: Map<string, number[]>, name: string, v: number) {
    (into.get(name) ?? into.set(name, []).get(name)!).push(v);
    const pro = this.profile;
    if (pro) {
      const m = into === this.passGpu ? pro.gpu : pro.cpu;
      (m.get(name) ?? m.set(name, []).get(name)!).push(v);
    }
  }

  frameStart() {
    const now = performance.now();
    this.renderer.info.reset();
    this.poll();
    this.t0 = now;
    this.intervalNow = this.lastStart < 0 ? 0 : now - this.lastStart;
    this.lastStart = now;
  }

  private intervalNow = 0;

  frameEnd() {
    const now = performance.now();
    const r = this.renderer.info.render;
    this.calls = r.calls;
    this.tris = r.triangles;
    if (this.intervalNow > 0 && this.intervalNow < 1000) {
      const s = { at: now, interval: this.intervalNow, cpu: now - this.t0 };
      this.samples.push(s);
      if (this.profile?.i === 0) this.profile.samples.push(s);
    }
    const cut = now - WINDOW_MS;
    let k = 0;
    while (k < this.samples.length && this.samples[k].at < cut) k++;
    if (k) this.samples.splice(0, k);
    // Per-pass costs: keep about the same window (~300 per pass at 60 fps).
    for (const m of [this.passCpu, this.passGpu]) for (const a of m.values()) if (a.length > 300) a.splice(0, a.length - 300);
    const pro = this.profile;
    if (pro) {
      const ph = pro.phases[pro.i];
      if (now >= (ph.from ?? 0) && this.intervalNow > 0 && this.intervalNow < 1000) ph.intervals.push(this.intervalNow);
      if (now >= pro.phaseEnd) this.nextPhase(now);
    }
  }

  // ---------------------------------------------------------------- display

  private paceLabel(): { state: string; text: string } {
    if (document.hidden) return { state: 'hidden', text: 'hidden (paused)' };
    const p = this.post.pacer;
    const st = p.state();
    const r = p.rates;
    return {
      continuous: { state: st, text: 'continuous' },
      interacting: { state: st, text: 'interacting · 60' },
      active: { state: st, text: `on-demand · ${r.active}` },
      quiet: { state: st, text: `idle · ${r.quiet} fps cap` },
    }[st];
  }

  /** Check the GPU timer against the frame interval while frames come flat out. */
  private checkGpu(names: string[]) {
    if (this.gpuBogus || !this.ext) return;
    const st = this.post.pacer.state();
    if (st !== 'continuous' && st !== 'interacting') return;
    const recent = this.samples.slice(-60).map((x) => x.interval).sort((a, b) => a - b);
    if (recent.length < 30) return;
    const sum = names.reduce((a, n) => a + avg((this.passGpu.get(n) ?? []).slice(-60)), 0);
    if (sum > 1.15 * pct(recent, 0.5)) {
      this.gpuBogus = true;
      console.info(`[r3x frame stats] GPU timer reports ${sum.toFixed(1)} ms per frame at a ${pct(recent, 0.5).toFixed(1)} ms interval: not GPU time on this driver; showing CPU per pass`);
    }
  }

  private passCosts(): { name: string; ms: number; gpu: boolean }[] {
    const names = [...new Set(this.post.composer.passes.map((p) => this.passName(p)))];
    this.checkGpu(names);
    return names.map((name) => {
      const g = this.gpuBogus ? undefined : this.passGpu.get(name);
      const c = this.passCpu.get(name);
      return g?.length ? { name, ms: avg(g.slice(-120)), gpu: true } : { name, ms: avg((c ?? []).slice(-120)), gpu: false };
    });
  }

  private draw() {
    const s = this.samples;
    const iv = s.map((x) => x.interval);
    const sorted = [...iv].sort((a, b) => a - b);
    const mean = avg(iv);
    const fps = mean ? 1000 / mean : 0;
    const pace = this.paceLabel();
    (this.el.querySelector('.fd-fps') as HTMLElement).textContent = s.length ? `${fps.toFixed(0)} fps` : '… fps';
    const paceEl = this.el.querySelector('.fd-pace') as HTMLElement;
    paceEl.textContent = pace.text;
    paceEl.dataset.state = pace.state;
    this.lines.frame.innerHTML = s.length
      ? `frame avg <b>${mean.toFixed(1)}</b> p95 <b>${pct(sorted, 0.95).toFixed(1)}</b> worst <b>${(sorted[sorted.length - 1] ?? 0).toFixed(1)}</b> ms`
      : 'frame: no frames drawn yet';
    const costs = this.passCosts();
    const gpuTotal = costs.every((c) => c.gpu) && costs.length ? costs.reduce((a, c) => a + c.ms, 0) : null;
    this.lines.time.innerHTML = `cpu <b>${avg(s.map((x) => x.cpu)).toFixed(1)}</b> ms · gpu ${gpuTotal !== null
      ? `<b>${gpuTotal.toFixed(1)}</b> ms` : !this.ext ? 'n/a (no timer query)' : this.gpuBogus ? 'n/a (unreliable timer)' : 'measuring…'}`;
    const info = this.renderer.info;
    this.lines.count.innerHTML = `<b>${fmtN(this.calls)}</b> draws · <b>${fmtN(this.tris)}</b> tris · <b>${info.memory.textures}</b> tex · <b>${info.memory.geometries}</b> geo`;
    const buf = this.renderer.getDrawingBufferSize(this.buf);
    this.lines.scale.innerHTML = `scale <b>${this.post.pixelRatio.toFixed(2)}x</b> · dpr ${window.devicePixelRatio} · ${buf.x}×${buf.y} · ${this.post.currentQuality}`;
    this.lines.passes.innerHTML = costs.map((c) => `${c.name} <b>${c.ms.toFixed(2)}</b>`).join(' · ') + ` ms ${costs.some((c) => c.gpu) ? 'gpu' : 'cpu submit'}`;
    const mem = (performance as unknown as { memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number } }).memory;
    this.lines.heap.hidden = !mem;
    if (mem) this.lines.heap.innerHTML = `heap <b>${(mem.usedJSHeapSize / 2 ** 20).toFixed(0)}</b> / ${(mem.jsHeapSizeLimit / 2 ** 20).toFixed(0)} MB`;
    this.drawSpark(iv);
  }

  private drawSpark(iv: number[]) {
    const c = this.spark;
    const g = c.getContext('2d');
    if (!g) return;
    const w = c.width;
    const h = c.height;
    g.clearRect(0, 0, w, h);
    const fps = this.post.pacer.fps(performance.now());
    const target = Number.isFinite(fps) ? 1000 / fps : 1000 / 60;
    const top = Math.max(40, target * 1.5, ...iv);
    const y = (ms: number) => h - 1 - (Math.min(ms, top) / top) * (h - 2);
    g.strokeStyle = 'rgba(138,144,156,.55)';
    g.setLineDash([3, 3]);
    g.beginPath();
    g.moveTo(0, y(target));
    g.lineTo(w, y(target));
    g.stroke();
    g.setLineDash([]);
    if (!iv.length) return;
    const n = Math.min(iv.length, w);
    const tail = iv.slice(-n);
    g.strokeStyle = '#e8762a';
    g.lineWidth = 1.25;
    g.beginPath();
    tail.forEach((ms, i) => {
      const x = w - n + i;
      if (i) g.lineTo(x, y(ms));
      else g.moveTo(x, y(ms));
    });
    g.stroke();
  }

  // ---------------------------------------------------------------- profile

  runProfile(ms = 10000) {
    if (!this.active) this.setEnabled(true);
    if (this.profile) return;
    const post = this.post;
    const ao = post.ao.configuration.intensity;
    const b = post.bloom;
    const bloom = [b.strength, b.threshold, b.radius] as const;
    const ablate: Phase[] = [];
    if (post.aoEnabled) ablate.push({ name: 'AO', ms: 1500, intervals: [], set: (on) => post.setAO(on, ao) });
    if (post.bloomEnabled) ablate.push({ name: 'bloom', ms: 1500, intervals: [], set: (on) => post.setBloom(on, ...bloom) });
    const base = ms - ablate.reduce((a, p) => a + p.ms, 0);
    const now = performance.now();
    this.profile = {
      phases: [{ name: 'all', ms: base, intervals: [], from: now }, ...ablate], i: 0, phaseEnd: now + base,
      samples: [], gpu: new Map(), cpu: new Map(),
    };
    post.pacer.continuous(ms + 500);
    const sum = this.lines.summary;
    sum.hidden = false;
    sum.textContent = `Profiling ${ms / 1000} s, drawing every frame${ablate.length ? `; ${ablate.map((p) => p.name).join(' and ')} switch off briefly at the end to time them` : ''}…`;
  }

  private nextPhase(now: number) {
    const pro = this.profile!;
    pro.phases[pro.i].set?.(true);
    if (++pro.i >= pro.phases.length) {
      this.finishProfile();
      return;
    }
    const ph = pro.phases[pro.i];
    ph.set?.(false);
    ph.from = now + 300;
    pro.phaseEnd = now + ph.ms;
  }

  private finishProfile() {
    const pro = this.profile!;
    this.profile = null;
    this.post.pacer.continuous(0);
    const iv = pro.samples.map((s) => s.interval);
    const sorted = [...iv].sort((a, b) => a - b);
    const mean = avg(iv);
    const p95 = pct(sorted, 0.95);
    // The profile draws flat out, which is when a wall-clock GPU timer gives itself away.
    const gpuOk = pro.gpu.size > 0 && !this.gpuBogus
      && [...pro.gpu.values()].reduce((a, v) => a + avg(v), 0) <= 1.15 * pct(sorted, 0.5);
    if (pro.gpu.size && !gpuOk) this.gpuBogus = true;
    if (!gpuOk) pro.gpu.clear();
    const src = pro.gpu.size ? pro.gpu : pro.cpu;
    const passes = [...src].map(([name, v]) => ({ name, ms: avg(v) })).sort((a, b) => b.ms - a.ms);
    const cpu = avg(pro.samples.map((s) => s.cpu));
    const gpu = pro.gpu.size ? passes.reduce((a, p) => a + p.ms, 0) : null;
    // Frame time saved with each effect off (median interval, the whole picture minus without it).
    const med = (a: number[]) => pct([...a].sort((x, y) => x - y), 0.5);
    const baseMed = med(pro.phases[0].intervals);
    const saved = pro.phases.slice(1).filter((p) => p.intervals.length >= 10)
      .map((p) => ({ name: p.name, ms: baseMed - med(p.intervals) }));
    const top = pro.gpu.size ? { ...passes[0], how: 'gpu' } : [...saved].sort((a, b) => b.ms - a.ms).map((p) => ({ ...p, how: 'saved when off' }))[0];
    const bound = gpu === null ? '' : gpu > cpu ? ' (GPU-bound)' : ' (CPU-bound)';
    const atRate = baseMed < 17.5 && baseMed > 15.9;
    const vsync = atRate ? ' (holding the display rate: costs under the frame budget do not show)' : '';
    const text = `avg ${(1000 / (mean || 1)).toFixed(1)} fps, p95 ${p95.toFixed(1)} ms (${(1000 / (p95 || 1)).toFixed(0)} fps) over ${iv.length} frames; ` +
      `cpu ${cpu.toFixed(1)} ms${gpu === null ? '' : `, gpu ${gpu.toFixed(1)} ms`}${bound}; ` +
      `biggest cost: ${top && !(atRate && top.how !== 'gpu' && top.ms < 1) ? `${top.name} ${top.ms.toFixed(2)} ms ${top.how}` : atRate ? 'none visible' : 'n/a'}` +
      `${saved.length ? `; off saves ${saved.map((p) => `${p.name} ${p.ms.toFixed(1)} ms`).join(', ')}${vsync}` : ''}`;
    this.lines.summary.textContent = `Profile: ${text}`;
    const buf = this.renderer.getDrawingBufferSize(this.buf);
    console.log(`[r3x profile] ${text}\n  quality ${this.post.currentQuality}, scale ${this.post.pixelRatio.toFixed(2)}x, buffer ${buf.x}x${buf.y}, ` +
      `passes ${passes.map((p) => `${p.name} ${p.ms.toFixed(2)}`).join(', ')} ms, ${navigator.userAgent}`);
  }
}
