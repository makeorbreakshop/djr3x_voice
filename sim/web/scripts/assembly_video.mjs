#!/usr/bin/env node
/**
 * An assembly video of one Build section (default: Hunter's head mech), rendered offline: headless Chrome
 * on the GPU, the sim in still mode (src/still.ts: a virtual clock), every frame set explicitly - the step's
 * time, the camera - then captured, so the video never depends on how fast the machine renders.
 *
 * It builds up from an empty stage: each step's parts and hardware come in along the manifest's approach
 * paths (mech/workbench/paths.py: swept clear of what is already there), in its order, and stay; the camera
 * frames what is built so far plus what is coming, easing out as it grows, with a slow orbit; a small label
 * names each step. At the end the mech moves through its joints, then a short orbit.
 *
 *   node scripts/assembly_video.mjs --url http://localhost:5413 --out <dir> [--section hunter_head] [--w 1080 --h 1080]
 *
 * Writes <dir>/frames/*.png, <dir>/<section>_assembly.mp4 (H.264, yuv420p, faststart) and <dir>/contact_sheet.png.
 * The frames show vendor-derived meshes: keep the output out of the repo.
 */
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const opt = (k, d) => { const i = argv.indexOf(`--${k}`); return i >= 0 ? argv[i + 1] : d; };
const URL0 = opt('url', 'http://localhost:5413');
const OUT = path.resolve(opt('out', '.video-out'));
const SECTION = opt('section', 'hunter_head');
const W = Number(opt('w', 1080));
const H = Number(opt('h', 1080));
const FPS = 30;
const TITLE = opt('title', "Hunter Smoke's head mech");
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
// pacing (s): parts, screws (a bolt circle's overlap), the hold after a step, the opening, the ending
// parts one at a time with a short gap, screws in order (a bolt circle overlapping a little); `hold` is the camera's move
const MOVE_S = 1.0;
const TIMING = { part: 0.75, fastener: 0.35, overlap: (0.75 + 0.12) / 0.75, fastenerOverlap: 0.6, hold: MOVE_S + 0.1, maxTotal: 1e9, minDur: 0.1 };
const STEP_HOLD = 0.6;
const OPEN_S = 1.0;
const MOTION_S = 2.8;
const ORBIT_S = 1.6;

fs.mkdirSync(path.join(OUT, 'frames'), { recursive: true });
for (const f of fs.readdirSync(path.join(OUT, 'frames'))) fs.rmSync(path.join(OUT, 'frames', f));

// ------------------------------------------------------------------ chrome
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'r3x-video-'));
const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, '--no-first-run',
  '--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist', '--hide-scrollbars', `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
const kill = () => { try { chrome.kill('SIGKILL'); } catch {} try { fs.rmSync(prof, { recursive: true, force: true }); } catch {} };
process.on('exit', kill);
process.on('SIGINT', () => process.exit(1));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws;
for (let i = 0; i < 60 && !ws; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    const page = list.find((t) => t.type === 'page');
    if (page) ws = new WebSocket(page.webSocketDebuggerUrl);
  } catch { await sleep(200); }
}
await new Promise((r) => ws.addEventListener('open', r));
let id = 0;
const pending = new Map();
ws.addEventListener('message', (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text);
  return r.result?.result?.value;
};
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: `${URL0}/?still&quality=high&dpr=1` });
for (let i = 0; i < 300 && !(await ev(`!!document.querySelector('[data-stage-mode=build]') && !!window.__r3xStill`).catch(() => false)); i++) await sleep(200);
await ev(`document.querySelector('[data-stage-mode=build]').click(); 1`);
for (let i = 0; i < 600 && !(await ev(`!!globalThis.__r3xBuildLoaded`)); i++) await sleep(200);
await ev(`__r3xStill.hold(10)`);

// ------------------------------------------------------------------ the page: the section alone, no UI
const steps = await ev(`(() => {
  const wb = __r3x.build;
  const node = wb.nodeOf(${JSON.stringify(SECTION)});
  wb.setGuide(node);
  wb.guideRect = { right: 0, bottom: 0, top: 0 };
  wb.setLook('mechanism');
  wb.seqTiming = ${JSON.stringify(TIMING)};
  wb.setVideo(true);
  const st = document.createElement('style');
  st.textContent = 'body > *:not(#stage):not(#vid) { display: none !important; }'
    + '#vid { position: fixed; inset: 0; pointer-events: none; font-family: "SF Pro Display", system-ui, -apple-system, sans-serif; color: #1c1d20; }'
    + '#vid .card { position: absolute; left: 0; right: 0; top: 40%; text-align: center; font-size: 46px; font-weight: 600; letter-spacing: -0.01em; }'
    + '#vid .card small { display: block; margin-top: 10px; font-size: 22px; font-weight: 400; color: #6b6e74; letter-spacing: 0; }'
    + '#vid .lab { position: absolute; left: 44px; bottom: 44px; font-size: 24px; font-weight: 550; display: flex; gap: 14px; align-items: baseline; }'
    + '#vid .lab b { font-weight: 300; font-size: 40px; color: #1c1d20; font-variant-numeric: tabular-nums; }';
  document.head.append(st);
  const v = document.createElement('div');
  v.id = 'vid';
  v.innerHTML = '<div class="card"></div><div class="lab"><b></b><span></span></div>';
  document.body.append(v);
  dispatchEvent(new Event('resize'));
  // the steps that show anything (step 1's inserts ride in later with their parts)
  return wb.steps.map((s, i) => ({ i, n: i + 1, title: s.title, parts: (s.parts ?? []).length }));
})()`);
const shown = steps.filter((s) => s.parts > 0);

// each step's length
const plan = [];
for (const s of shown) {
  const total = await ev(`(() => { const wb = __r3x.build; wb.setGuideStep(${s.i}, true); wb.seqSetTime(0); return wb.seqState.total; })()`);
  plan.push({ ...s, total });
}
// the shells come last: the mechanism moves once without them, then they go on and it moves again
const SHELLS_FROM = Number(opt('shells-from', 15));
const mech = plan.filter((p) => p.n < SHELLS_FROM);
const shells = plan.filter((p) => p.n >= SHELLS_FROM);

// ------------------------------------------------------------------ the timeline
// open on the finished head, explode it along the build's own paths (last in, first out), then build it up
const EXPLODE_S = 2.0;
const segs = [{ kind: 'open', dur: 3.0 }, { kind: 'explode', dur: EXPLODE_S }, { kind: 'exploded', dur: 0.8 }, { kind: 'disperse', dur: 1.2 }];
for (const p of mech) segs.push({ kind: 'step', p, dur: p.total + STEP_HOLD });
segs.push({ kind: 'wide', dur: MOVE_S + 0.2 }, { kind: 'motion', dur: MOTION_S });
for (const p of shells) segs.push({ kind: 'step', p, dur: p.total + STEP_HOLD });
segs.push({ kind: 'wide', dur: MOVE_S + 0.2 }, { kind: 'motion', dur: MOTION_S }, { kind: 'orbit', dur: ORBIT_S });
const total = segs.reduce((t, s) => t + s.dur, 0);
const nFrames = Math.round(total * FPS);
console.log(`${shown.length} steps, ${total.toFixed(1)} s, ${nFrames} frames`);

const node = JSON.stringify(SECTION);
// the band is always there; its text fades (0.3 s) in and out
const label = async (num, text, op) => ev(`(() => { const l = document.querySelector('#vid .lab'); l.querySelector('b').textContent = ${JSON.stringify(String(num ?? ''))}; l.querySelector('span').textContent = ${JSON.stringify(text ?? '')}; l.style.opacity = 1; for (const e of l.children) e.style.opacity = ${op}; 1 })()`);
const smooth = (x) => { const k = Math.min(1, Math.max(0, x)); return k * k * (3 - 2 * k); };
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2);
const AZ0 = 32;
const EL0 = 24;
const DRIFT = 0.12; // deg/s: barely there
const BAND = 0.12; // the label band under the picture (share of the frame)
const PIC = 1 - BAND;
const MIN_SHOT = 0.06; // m: the closest a step's shot comes
// the safe area in normalised device coordinates: in from the edges, above the band
const SAFE = [-0.88, -1 + 2 * BAND + 0.06, 0.88, 0.9];
await ev(`(() => { __r3x.build.guideRect = { right: 0, bottom: ${Math.round(BAND * H)}, top: 0 }; 1 })()`);
await ev(`(() => { const st = document.createElement('style'); st.textContent = '#vid .card { display: none; } #vid .lab { left: 0; right: 0; bottom: 0; height: ${BAND * 100}%; background: #1b1c1f; color: #f2f1ee; justify-content: center; align-items: center; font-size: 34px; font-weight: 600; gap: 20px; text-shadow: none; padding: 0 40px; } #vid .lab b { font-size: 56px; font-weight: 300; color: #f2f1ee; }'; document.head.append(st); 1 })()`);

const view = (az, el) => {
  const a = (az * Math.PI) / 180, e = (el * Math.PI) / 180;
  return { dir: [Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e)], right: [Math.cos(a), 0, -Math.sin(a)], up: [-Math.sin(a) * Math.sin(e), Math.cos(e), -Math.cos(a) * Math.sin(e)] };
};
/** A box's shot from this view: its centre and the size to frame (the picture's height at `fill`). */
const boxShot = (b, az, el, fill, grow = 1) => {
  const { right, up } = view(az, el);
  let w = 0, h = 0;
  for (const sx of [-0.5, 0.5]) for (const sy of [-0.5, 0.5]) for (const sz of [-0.5, 0.5]) {
    const p = [b.s[0] * sx * grow, b.s[1] * sy * grow, b.s[2] * sz * grow];
    w = Math.max(w, 2 * Math.abs(p[0] * right[0] + p[2] * right[2]));
    h = Math.max(h, 2 * Math.abs(p[0] * up[0] + p[1] * up[1] + p[2] * up[2]));
  }
  return { c: [...b.c], r: Math.max(h, w / (W / (H * PIC)), MIN_SHOT) / (fill * PIC), az, el };
};
const wholeShot = async (az, el, fill) => {
  const v = await ev(`JSON.stringify(__r3x.build.videoWholeView(${JSON.stringify(view(az, el).right)}, ${JSON.stringify(view(az, el).up)}))`).then(JSON.parse);
  return { c: v.c, r: Math.max(v.h, v.w / (W / (H * PIC))) / (fill * PIC), az, el };
};
/** Put the camera on a shot (and orbit by `orbit` deg). */
const place = async (sh, orbit = 0) => {
  const { dir } = view(sh.az + orbit, sh.el);
  await ev(`(() => {
    const cam = __r3x.camera, wb = __r3x.build;
    cam.updateProjectionMatrix();
    const d = (${sh.r} / 2) * cam.projectionMatrix.elements[5];
    const t = ${JSON.stringify(sh.c)}, dir = ${JSON.stringify(dir)};
    const c = wb.cameraState();
    c.target.set(t[0], t[1], t[2]);
    c.pos.set(t[0] + dir[0] * d, t[1] + dir[1] * d, t[2] + dir[2] * d);
    wb.setCameraState(c);
    cam.position.copy(c.pos); cam.lookAt(t[0], t[1], t[2]); cam.updateMatrixWorld();
    return 1;
  })()`);
};
/** A step's shot: the first view (3/4 from above, a low angle for what comes from below, other sides, wider)
 *  where every item's start and seat are in the safe area and not both hidden; else the one that fails least. */
const stepShot = async (az) => {
  const b = await ev(`JSON.stringify(__r3x.build.videoStepBox())`).then(JSON.parse);
  let best = null;
  // something coming in from below: a low camera looking up at the underside first, and closer
  const below = await ev(`__r3x.build.videoFromBelow()`);
  const els = below ? [-22, EL0, 40] : [EL0, -14, 40];
  const fills = below ? [0.72, 0.6, 0.45] : [0.6, 0.45];
  for (const fill of fills) for (const el of els) for (const da of [0, 45, -45, 100, -100, 180]) {
    const sh = boxShot(b, az + da, el, fill, 1.15);
    await place(sh);
    const bad = await ev(`JSON.stringify(__r3x.build.videoShotCheck(${JSON.stringify(SAFE)}))`).then(JSON.parse);
    if (!best || bad.length < best.bad.length) best = { sh, bad };
    if (!bad.length) return best;
  }
  return best;
};

let T = 0;
let f = 0;
let cur = null; // the camera shot: { c, r, az, el }
let from = null;
let to = null;
const report = [];
for (const sg of segs) {
  const n = Math.round(sg.dur * FPS);
  for (let k = 0; k < n; k++, f++) {
    const t = k / FPS;
    let orbit = 0;
    if (sg.kind === 'open' || sg.kind === 'explode' || sg.kind === 'exploded' || sg.kind === 'disperse') {
      if (sg.kind === 'open' && k === 0) {
        await ev(`(() => { const wb = __r3x.build; wb.setGuideStep(-1, false); 1 })()`);
        cur = await wholeShot(AZ0 - 25, EL0, 0.78);
      }
      if (sg.kind === 'open') await label('', TITLE, smooth(t / 0.4));
      if (sg.kind === 'disperse') await label('', TITLE, 1 - smooth(t / 0.4));
      if (sg.kind === 'explode' && k === 0) {
        // where the camera ends up: the exploded view, framed whole
        await ev(`__r3x.build.videoExplodeAt(1)`);
        from = cur;
        to = await wholeShot(cur.az, EL0, 0.85);
        await ev(`__r3x.build.videoExplodeAt(0)`);
      }
      if (sg.kind === 'open') { cur = { ...cur, az: AZ0 - 25 + 25 * ease(t / sg.dur) }; }
      if (sg.kind === 'explode') {
        const e = ease(t / sg.dur);
        await ev(`__r3x.build.videoExplodeAt(${e.toFixed(4)})`);
        cur = { c: from.c.map((v, j) => v + (to.c[j] - v) * e), r: from.r + (to.r - from.r) * e, az: from.az, el: from.el };
      }
      if (sg.kind === 'exploded') await ev(`__r3x.build.videoExplodeAt(1)`);
      if (sg.kind === 'disperse') {
        // what the first step needs stays where the explode left it; the rest flies on out, fading
        if (k === 0) await ev(`(() => { const wb = __r3x.build; const st = wb.steps[${plan[0].i}]; const ids = new Set((st.sequence ?? []).flatMap((it) => it.ids));
          for (const s of wb.steps) for (const it of s.sequence ?? []) for (const id of it.ids) { const f = wb.fastenerInfo(id); if (f && f.joins.some((j) => ids.has(j))) ids.add(id); }
          wb.videoStay = ids; 1 })()`);
        await ev(`__r3x.build.videoExplodeAt(1, ${smooth((k + 1) / n).toFixed(4)})`); // (the last frame fully gone)
      }
      await place(cur);
    } else if (sg.kind === 'step') {
      const p = sg.p;
      if (k === 0) {
        await ev(`(() => { const wb = __r3x.build; const n = wb.nodeOf(${node}); for (const j of ['head_tilt','head_roll','visor']) wb.setJoint(n, j, 0); wb.setGuideStep(${p.i}, true); wb.seqSetTime(0); 1 })()`);
        const got = await stepShot(AZ0 + T * DRIFT);
        report.push({ step: p.n, az: Math.round(got.sh.az), el: got.sh.el, fill: got.sh.r, bad: got.bad });
        if (p !== plan[0]) await ev(`(() => { __r3x.build.videoStay = new Set(); 1 })()`);
        from = cur;
        to = got.sh;
      }
      await ev(`__r3x.build.seqSetTime(${t.toFixed(4)})`);
      await label(p.n, p.title, Math.min(smooth(t / 0.3), smooth((sg.dur - t) / 0.3)));
    } else {
      if (k === 0 && sg.kind === 'wide') {
        await label('', '', 0);
        await ev(`(() => { __r3x.build.videoStay = new Set(); 1 })()`);
        from = cur;
        to = await wholeShot(AZ0 + T * DRIFT, EL0, 0.74);
      }
      if (sg.kind === 'motion') {
        const u = t / sg.dur;
        const w = (a, b) => Math.max(0, Math.min(1, (u - a) / (b - a)));
        const swing = (x, lo, hi) => (x <= 0 || x >= 1 ? 0 : x < 0.25 ? hi * ease(x * 4) : x < 0.75 ? hi + (lo - hi) * ease((x - 0.25) * 2) : lo * (1 - ease((x - 0.75) * 4)));
        const tilt = swing(w(0, 0.45), -20, 25), roll = swing(w(0.3, 0.75), -12, 12), visor = swing(w(0.55, 1), -15, 30);
        await ev(`(() => { const wb = __r3x.build; const n = wb.nodeOf(${node}); wb.setJoint(n, 'head_tilt', ${tilt}); wb.setJoint(n, 'head_roll', ${roll}); wb.setJoint(n, 'visor', ${visor}); 1 })()`);
      }
      if (sg.kind === 'orbit') orbit = 60 * ease(t / sg.dur);
    }
    if (sg.kind === 'step' || sg.kind === 'wide' || sg.kind === 'motion' || sg.kind === 'orbit') {
      // one eased move at the start of a step (or of the wide shot), then still
      if ((sg.kind === 'step' || sg.kind === 'wide') && t < MOVE_S) {
        const e = ease(t / MOVE_S);
        cur = { c: from.c.map((v, j) => v + (to.c[j] - v) * e), r: from.r + (to.r - from.r) * e, az: from.az + (to.az - from.az) * e, el: from.el + (to.el - from.el) * e };
      } else if (sg.kind === 'step' || sg.kind === 'wide') cur = { ...to, az: to.az + (t - MOVE_S) * DRIFT };
      await place(cur, orbit);
    }
    await ev(`__r3xStill.hold(2)`);
    const shot = await send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
    fs.writeFileSync(path.join(OUT, 'frames', `${String(f).padStart(5, '0')}.png`), Buffer.from(shot.result.data, 'base64'));
    T += 1 / FPS;
    if (f % 300 === 0) console.log(`frame ${f}/${nFrames}`);
  }
}
for (const r of report) console.log(`step ${r.step}: az ${r.az} el ${r.el}${r.bad.length ? ` - still not clear: ${r.bad.join(', ')}` : ''}`);
ws.close();
kill();

// ------------------------------------------------------------------ encode
const mp4 = path.join(OUT, `${SECTION}_assembly.mp4`);
const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(OUT, 'frames', '%05d.png'),
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '26', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4], { stdio: 'inherit' });
if (r.status) process.exit(r.status);
const small = mp4.replace(/\.mp4$/, '_720.mp4');
spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(OUT, 'frames', '%05d.png'), '-vf', 'scale=720:-2',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '26', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', small], { stdio: 'inherit' });
// one-frame glitches: consecutive frames' difference (64x64 grey) spiking above both neighbours
{
  const raw = spawnSync('ffmpeg', ['-loglevel', 'error', '-i', mp4, '-vf', 'scale=64:64,format=gray', '-f', 'rawvideo', '-'], { maxBuffer: 1 << 30 }).stdout;
  const N = 64 * 64, n = Math.floor(raw.length / N);
  const d = [];
  for (let i = 1; i < n; i++) {
    let s = 0;
    for (let j = 0; j < N; j++) s += Math.abs(raw[i * N + j] - raw[(i - 1) * N + j]);
    d.push(s / N);
  }
  const spikes = [];
  for (let i = 1; i < d.length - 1; i++) if (d[i] > 3 && d[i] > 4 * Math.max(d[i - 1], d[i + 1], 0.5)) spikes.push(`${i + 1} (${d[i].toFixed(1)})`);
  console.log(spikes.length ? `frame-diff spikes at: ${spikes.join(', ')}` : 'frame-diff: no one-frame spikes');
}
const every = Math.max(1, Math.floor(f / 12));
spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', mp4, '-vf', `select='not(mod(n\\,${every}))',scale=360:-1,tile=4x3`, '-frames:v', '1',
  path.join(OUT, 'contact_sheet.png')], { stdio: 'inherit' });
console.log(JSON.stringify({ mp4, seconds: +(f / FPS).toFixed(1), mb: +(fs.statSync(mp4).size / 1e6).toFixed(1), mb720: +(fs.statSync(small).size / 1e6).toFixed(1) }));
