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

const UI_KEY = 'r3x.frameDiag';
function loadUi(): { more: boolean; breakdown: boolean } {
  try {
    return { more: false, breakdown: false, ...(JSON.parse(localStorage.getItem(UI_KEY) ?? '{}') as object) };
  } catch {
    return { more: false, breakdown: false };
  }
}
function saveUi(v: { more: boolean; breakdown: boolean }) {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify(v));
  } catch {
    /* storage blocked */
  }
}

const pct = (sorted: number[], p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] ?? 0;
const avg = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
const fmtN = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(0)}k` : String(n));

export class FrameDiag implements RenderProbe {
  private readonly el: HTMLElement;
  private readonly spark: HTMLCanvasElement;
  private readonly q: Record<string, HTMLElement> = {};
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
  /** The GPU timer has passed that check once (drawing flat out); only then is GPU time shown. */
  private gpuTrusted = false;
  private readonly buf = new THREE.Vector2();

  constructor(private readonly post: PostPipeline, el: HTMLElement) {
    this.el = el;
    el.className = 'frame-diag';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-label', 'Frame diagnostics');
    const cell = (k: string, label: string, tip = '') => `<div data-k="${k}"${tip ? ` title="${tip}"` : ''}><dt>${label}</dt><dd></dd></div>`;
    el.innerHTML = `<div class="fd-head">
<span class="fd-big"><b data-q="fps">…</b><small>fps</small></span><span class="fd-ms"><b data-q="ms"></b><small>ms</small></span>
<span class="fd-chip" data-q="pace"></span></div>
<canvas width="440" height="56" aria-hidden="true" title="Frame time, last 5 s (guides: 16.7 ms = 60 fps, 33 ms = 30 fps)"></canvas>
<dl class="fd-grid">${cell('p95', 'p95', 'Frame time, 95th percentile over the last 5 s')}${cell('worst', 'worst', 'Longest frame in the last 5 s')}
${cell('cpu', 'CPU', 'JS time per frame (submitting the passes)')}${cell('gpu', 'GPU', 'GPU time per frame (timer query)')}
${cell('draws', 'draws', 'Draw calls per frame, every pass')}${cell('tris', 'tris', 'Triangles per frame, every pass')}
${cell('scale', 'scale', 'Render scale (x CSS pixels); adapts while you interact')}</dl>
<ol class="fd-passes" data-q="passes"></ol>
<p class="fd-result" data-q="result" hidden></p>
<div class="fd-prof"><button type="button" data-q="run" title="Draw every frame for 10 s; AO and bloom switch off briefly at the end to time them. Logged to the console.">Profile 10 s</button>
<button type="button" class="fd-tog" data-q="bdBtn" aria-expanded="false" aria-controls="fd-bd" hidden>Breakdown</button>
<button type="button" class="fd-tog" data-q="moreBtn" aria-expanded="false" aria-controls="fd-more">More</button></div>
<dl class="fd-grid fd-panel" id="fd-bd" data-q="bd" hidden></dl>
<dl class="fd-grid fd-panel" id="fd-more" data-q="more" hidden>${cell('dpr', 'DPR')}${cell('buf', 'buffer')}
${cell('tex', 'textures')}${cell('geo', 'geometries')}${cell('quality', 'quality')}${cell('heap', 'heap', 'JS heap in use')}</dl>`;
    this.spark = el.querySelector('canvas')!;
    el.querySelectorAll<HTMLElement>('[data-q]').forEach((e) => (this.q[e.dataset.q!] = e));
    el.querySelectorAll<HTMLElement>('[data-k]').forEach((e) => (this.q[`k:${e.dataset.k}`] = e));
    this.q.run.onclick = (e) => {
      this.runProfile();
      (e.currentTarget as HTMLElement).blur();
    };
    // Disclosures remember their state per viewer.
    const ui = loadUi();
    for (const [k, btn, panel] of [['more', 'moreBtn', 'more'], ['breakdown', 'bdBtn', 'bd']] as const) {
      const b = this.q[btn];
      const show = (open: boolean) => {
        b.setAttribute('aria-expanded', String(open));
        this.q[panel].hidden = !open;
      };
      show(ui[k]);
      b.onclick = () => {
        const open = b.getAttribute('aria-expanded') !== 'true';
        show(open);
        saveUi({ ...loadUi(), [k]: open });
        b.blur();
      };
    }
    this.q.bd.hidden = true; // no profile yet
  }

  /** Set one grid value (hidden when null). */
  private val(k: string, v: string | null, unit = '') {
    const c = this.q[`k:${k}`];
    if (!c) return;
    c.hidden = v === null;
    if (v !== null) c.querySelector('dd')!.innerHTML = unit ? `${v}<small>${unit}</small>` : v;
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

  private paceLabel(): { state: string; text: string; tip: string } {
    if (document.hidden) return { state: 'hidden', text: 'hidden', tip: 'Tab hidden: the browser stops drawing' };
    const p = this.post.pacer;
    const st = p.state();
    const r = p.rates;
    return {
      continuous: { state: st, text: 'continuous', tip: 'Drawing every animation frame' },
      interacting: { state: st, text: 'interacting', tip: 'Someone is interacting: full rate' },
      active: { state: st, text: 'on-demand', tip: `Something moves fast: up to ${r.active} fps` },
      quiet: { state: st, text: `idle ${r.quiet}`, tip: `Nothing moves fast: capped at ${r.quiet} fps on purpose, not slowness` },
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
    if (sum > 0 && sum <= 1.15 * pct(recent, 0.5)) this.gpuTrusted = true;
    if (sum > 1.15 * pct(recent, 0.5)) {
      this.gpuBogus = true;
      console.info(`[r3x frame stats] GPU timer reports ${sum.toFixed(1)} ms per frame at a ${pct(recent, 0.5).toFixed(1)} ms interval: not GPU time on this driver; showing CPU per pass`);
    }
  }

  private passCosts(): { name: string; ms: number; gpu: boolean }[] {
    const names = [...new Set(this.post.composer.passes.map((p) => this.passName(p)))];
    this.checkGpu(names);
    return names.map((name) => {
      const g = this.gpuBogus || !this.gpuTrusted ? undefined : this.passGpu.get(name);
      const c = this.passCpu.get(name);
      return g?.length ? { name, ms: avg(g.slice(-120)), gpu: true } : { name, ms: avg((c ?? []).slice(-120)), gpu: false };
    });
  }

  private draw() {
    const s = this.samples;
    const iv = s.map((x) => x.interval);
    const sorted = [...iv].sort((a, b) => a - b);
    const mean = avg(iv);
    const pace = this.paceLabel();
    this.q.fps.textContent = s.length ? (1000 / mean).toFixed(0) : '…';
    this.q.ms.textContent = s.length ? mean.toFixed(1) : '';
    this.q.pace.textContent = pace.text;
    this.q.pace.title = pace.tip;
    this.q.pace.dataset.state = pace.state;
    const f1 = (x: number) => x.toFixed(1);
    this.val('p95', s.length ? f1(pct(sorted, 0.95)) : '…', 'ms');
    this.val('worst', s.length ? f1(sorted[sorted.length - 1]) : '…', 'ms');
    const costs = this.passCosts();
    const gpu = costs.length && costs.every((c) => c.gpu) ? costs.reduce((a, c) => a + c.ms, 0) : null;
    this.val('cpu', f1(avg(s.map((x) => x.cpu))), 'ms');
    this.val('gpu', gpu === null ? null : f1(gpu), 'ms');
    this.q['k:cpu'].title = gpu === null
      ? `JS time per frame. GPU time: ${!this.ext ? 'no timer query in this browser' : this.gpuBogus ? 'the timer reports wall time on this driver, so it is not shown' : 'shown once checked against a Profile run or interaction'}`
      : 'JS time per frame (submitting the passes)';
    this.val('draws', fmtN(this.calls));
    this.val('tris', fmtN(this.tris));
    this.val('scale', `${this.post.pixelRatio.toFixed(2)}×`);

    // Per-pass cost, most expensive first, as bars.
    const byCost = [...costs].sort((a, b) => b.ms - a.ms);
    const max = Math.max(1e-6, ...byCost.map((c) => c.ms));
    const list = this.q.passes;
    list.title = gpu === null ? 'Per-pass CPU (submit) time; no reliable GPU timer here' : 'Per-pass GPU time';
    while (list.children.length > byCost.length) list.lastElementChild!.remove();
    byCost.forEach((c, i) => {
      let li = list.children[i] as HTMLElement | undefined;
      if (!li) {
        li = document.createElement('li');
        li.innerHTML = '<span></span><i><u></u></i><b></b>';
        list.appendChild(li);
      }
      li.children[0].textContent = c.name;
      (li.children[1].firstElementChild as HTMLElement).style.width = `${(100 * c.ms) / max}%`;
      li.children[2].textContent = c.ms < 0.1 ? c.ms.toFixed(2) : c.ms.toFixed(1);
    });

    const info = this.renderer.info;
    const buf = this.renderer.getDrawingBufferSize(this.buf);
    this.val('dpr', String(window.devicePixelRatio));
    this.val('buf', `${buf.x}×${buf.y}`);
    this.val('tex', String(info.memory.textures));
    this.val('geo', String(info.memory.geometries));
    this.val('quality', this.post.currentQuality);
    const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
    this.val('heap', mem ? (mem.usedJSHeapSize / 2 ** 20).toFixed(0) : null, 'MB');
    if (this.profile) {
      const left = Math.max(0, this.profile.phases.slice(this.profile.i).reduce((a, p) => a + p.ms, 0)
        - (performance.now() - (this.profile.phaseEnd - this.profile.phases[this.profile.i].ms)));
      this.q.result.textContent = `profiling… ${Math.ceil(left / 1000)} s`;
    }
    this.drawSpark(iv);
  }

  private drawSpark(iv: number[]) {
    const c = this.spark;
    const g = c.getContext('2d');
    if (!g) return;
    const w = c.width;
    const h = c.height;
    g.clearRect(0, 0, w, h);
    const top = Math.max(40, ...iv) * 1.08;
    const y = (ms: number) => h - 2 - (Math.min(ms, top) / top) * (h - 4);
    g.strokeStyle = 'rgba(138,144,156,.28)';
    g.lineWidth = 1;
    for (const ms of [1000 / 60, 1000 / 30]) {
      g.beginPath();
      g.moveTo(0, Math.round(y(ms)) + 0.5);
      g.lineTo(w, Math.round(y(ms)) + 0.5);
      g.stroke();
    }
    if (!iv.length) return;
    const n = Math.min(iv.length, w / 2);
    const tail = iv.slice(-n);
    g.strokeStyle = '#e8762a';
    g.lineWidth = 2;
    g.lineJoin = 'round';
    g.beginPath();
    tail.forEach((ms, i) => {
      const x = w - (n - i) * 2;
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
    this.q.result.hidden = false;
    this.q.result.textContent = `profiling… ${Math.ceil(ms / 1000)} s`;
    this.q.run.toggleAttribute('disabled', true);
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
    // One result row (the biggest cost highlighted); the rest behind Breakdown.
    const f0 = (x: number) => (1000 / (x || 1)).toFixed(0);
    const shown = top && !(atRate && top.how !== 'gpu' && top.ms < 1);
    const delta = (ms: number) => `${ms >= 0 ? '−' : '+'}${Math.abs(ms).toFixed(1)}`;
    const esc = (t: string) => t.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!);
    this.q.result.innerHTML = `avg ${f0(mean)} · p95 ${f0(p95)} fps${shown
      ? ` · <em>${esc(top.name)} ${top.how === 'gpu' ? top.ms.toFixed(1) : delta(top.ms)} ms</em>` : atRate ? ' · <span title="Holding the display rate: costs under the frame budget do not show">at vsync</span>' : ''}`;
    this.q.result.title = text;
    this.q.run.toggleAttribute('disabled', false);
    const row = (k: string, v: string) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`;
    this.q.bd.innerHTML = [
      row('frames', String(iv.length)),
      row('avg', `${(1000 / (mean || 1)).toFixed(1)}<small>fps</small>`),
      row('p95', `${p95.toFixed(1)}<small>ms</small>`),
      row('CPU', `${cpu.toFixed(1)}<small>ms</small>`),
      ...(gpu === null ? [] : [row('GPU', `${gpu.toFixed(1)}<small>ms</small>`), row('bound', gpu > cpu ? 'GPU' : 'CPU')]),
      ...saved.map((p) => row(`${p.name} off`, `${delta(p.ms)}<small>ms</small>`)),
      ...passes.map((p) => row(p.name, `${p.ms.toFixed(2)}<small>ms</small>`)),
    ].join('');
    this.q.bdBtn.hidden = false;
    this.q.bd.hidden = this.q.bdBtn.getAttribute('aria-expanded') !== 'true';
    const buf = this.renderer.getDrawingBufferSize(this.buf);
    console.log(`[r3x profile] ${text}\n  quality ${this.post.currentQuality}, scale ${this.post.pixelRatio.toFixed(2)}x, buffer ${buf.x}x${buf.y}, ` +
      `passes ${passes.map((p) => `${p.name} ${p.ms.toFixed(2)}`).join(', ')} ms, ${navigator.userAgent}`);
  }
}
