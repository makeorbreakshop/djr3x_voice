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
 *   node scripts/assembly_video.mjs --url http://localhost:5413 --out <dir> [--section hunter_head] [--w 1080 --h 1350]
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
const H = Number(opt('h', 1350));
const FPS = 30;
const TITLE = opt('title', "Hunter Smoke's head mech");
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
// pacing (s): parts, screws (a bolt circle's overlap), the hold after a step, the opening, the ending
const TIMING = { part: 0.45, fastener: 0.25, overlap: 0.75, fastenerOverlap: 0.45, hold: 0.05, maxTotal: 1e9, minDur: 0.1 };
const STEP_HOLD = 0.2;
const OPEN_S = 1.0;
const MOTION_S = 2.6;
const ORBIT_S = 1.4;
const CAM_EASE_S = 0.5;

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

// pre-pass: each step's length and the box of what is built by then plus what it brings
const plan = [];
for (const s of shown) {
  const r = await ev(`(() => {
    const wb = __r3x.build;
    wb.setGuideStep(${s.i}, true);
    wb.seqSetTime(0);
    const b = wb.guideBuiltBox();
    const c = b.getCenter(new b.min.constructor());
    return { total: wb.seqState.total, c: c.toArray(), r: b.getSize(new b.min.constructor()).length() / 2 };
  })()`);
  plan.push({ ...s, ...r });
}
// what is built only grows: the framing never closes in
let acc = null;
for (const p of plan) {
  if (!acc) acc = { c: p.c, r: p.r };
  else {
    // the union of two spheres
    const d = Math.hypot(...p.c.map((v, k) => v - acc.c[k]));
    if (d + p.r > acc.r) {
      const r = (acc.r + d + p.r) / 2;
      const k = d > 1e-6 ? (r - acc.r) / d : 0;
      acc = { c: acc.c.map((v, j) => v + (p.c[j] - v) * k), r };
    }
  }
  p.frame = { c: [...acc.c], r: acc.r };
}

// ------------------------------------------------------------------ the timeline
const segs = [];
segs.push({ kind: 'open', dur: OPEN_S });
for (const p of plan) segs.push({ kind: 'step', p, dur: p.total + STEP_HOLD });
segs.push({ kind: 'motion', dur: MOTION_S });
segs.push({ kind: 'orbit', dur: ORBIT_S });
const total = segs.reduce((t, s) => t + s.dur, 0);
const nFrames = Math.round(total * FPS);
console.log(`${shown.length} steps, ${total.toFixed(1)} s, ${nFrames} frames`);

const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2);
const AZ0 = 35; // deg, from the front right
const EL = 20;
const AZ_RATE = 3.2; // deg/s, a slow orbit
const camAt = (T, frame) => {
  const az = ((AZ0 + T * AZ_RATE) * Math.PI) / 180;
  const el = (EL * Math.PI) / 180;
  return { target: frame.c, dir: [Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)], r: frame.r };
};
const lerpF = (a, b, k) => ({ c: a.c.map((v, i) => v + (b.c[i] - v) * k), r: a.r + (b.r - a.r) * k });

let T = 0;
let f = 0;
let lastStep = -1;
let prevFrame = plan[0].frame;
const node = JSON.stringify(SECTION);
const label = async (num, text, op) => ev(`(() => { const l = document.querySelector('#vid .lab'); l.querySelector('b').textContent = ${JSON.stringify(String(num ?? ''))}; l.querySelector('span').textContent = ${JSON.stringify(text ?? '')}; l.style.opacity = ${op}; 1 })()`);
const card = async (text, sub, op) => ev(`(() => { const c = document.querySelector('#vid .card'); c.innerHTML = ${JSON.stringify(text)} + (${JSON.stringify(sub ?? '')} ? '<small>' + ${JSON.stringify(sub ?? '')} + '</small>' : ''); c.style.opacity = ${op}; 1 })()`);
await card(TITLE, 'Assembly', 1);
await label('', '', 0);

for (const sg of segs) {
  const n = Math.round(sg.dur * FPS);
  for (let k = 0; k < n; k++, f++) {
    const t = k / FPS;
    let frame = plan[0].frame;
    if (sg.kind === 'open') {
      // the empty stage, then the card fades as the first step starts
      if (lastStep !== 0) {
        await ev(`(() => { const wb = __r3x.build; wb.setGuideStep(${plan[0].i}, true); wb.seqSetTime(0); 1 })()`);
        lastStep = 0;
      }
      await card(TITLE, 'Assembly', t < sg.dur - 0.35 ? 1 : Math.max(0, (sg.dur - t) / 0.35));
    } else if (sg.kind === 'step') {
      const p = sg.p;
      if (lastStep !== p.i) {
        if (k === 0 && lastStep !== -1) prevFrame = camFrame;
        await ev(`(() => { const wb = __r3x.build; wb.setGuideStep(${p.i}, true); 1 })()`);
        lastStep = p.i;
        await card('', '', 0);
      }
      await ev(`__r3x.build.seqSetTime(${t.toFixed(4)})`);
      frame = lerpF(prevFrame, p.frame, ease(Math.min(1, t / CAM_EASE_S)));
      await label(p.n, p.title, Math.min(1, t / 0.2));
    } else if (sg.kind === 'motion') {
      // the finished mech moves: tilt, roll, the visor - the push rods follow
      frame = plan[plan.length - 1].frame;
      const u = t / sg.dur;
      const w = (a, b) => Math.max(0, Math.min(1, (u - a) / (b - a)));
      const swing = (x, lo, hi) => (x <= 0 || x >= 1 ? 0 : x < 0.25 ? hi * ease(x * 4) : x < 0.75 ? hi + (lo - hi) * ease((x - 0.25) * 2) : lo * (1 - ease((x - 0.75) * 4)));
      const tilt = swing(w(0, 0.45), -20, 25), roll = swing(w(0.3, 0.75), -12, 12), visor = swing(w(0.55, 1), -15, 30);
      await ev(`(() => { const wb = __r3x.build; const n = wb.nodeOf(${node}); wb.setJoint(n, 'head_tilt', ${tilt}); wb.setJoint(n, 'head_roll', ${roll}); wb.setJoint(n, 'visor', ${visor}); 1 })()`);
      await label('', '', Math.max(0, 1 - t / 0.3));
    } else {
      frame = plan[plan.length - 1].frame;
    }
    var camFrame = frame;
    const cam = camAt(T, frame);
    await ev(`(() => {
      const cam = __r3x.camera, ctl = __r3x.build;
      const fov = cam.fov * Math.PI / 180, aspect = ${W / H};
      const half = Math.min(Math.tan(fov / 2), Math.tan(fov / 2) * aspect);
      const d = ${cam.r} / Math.sin(Math.atan(half)) * 1.08;
      const t = ${JSON.stringify(cam.target)}, dir = ${JSON.stringify(cam.dir)};
      cam.position.set(t[0] + dir[0] * d, t[1] + dir[1] * d, t[2] + dir[2] * d);
      __r3x.camera.lookAt(t[0], t[1], t[2]);
      const c = ctl.cameraState(); c.target.set(t[0], t[1], t[2]); c.pos.copy(cam.position); ctl.setCameraState(c);
      return 1;
    })()`);
    await ev(`__r3xStill.hold(2)`);
    const shot = await send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
    fs.writeFileSync(path.join(OUT, 'frames', `${String(f).padStart(5, '0')}.png`), Buffer.from(shot.result.data, 'base64'));
    T += 1 / FPS;
    if (f % 150 === 0) console.log(`frame ${f}/${nFrames}`);
  }
}
ws.close();
kill();

// ------------------------------------------------------------------ encode
const mp4 = path.join(OUT, `${SECTION}_assembly.mp4`);
const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(OUT, 'frames', '%05d.png'),
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4], { stdio: 'inherit' });
if (r.status) process.exit(r.status);
const every = Math.max(1, Math.floor(f / 12));
spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', mp4, '-vf', `select='not(mod(n\\,${every}))',scale=360:-1,tile=4x3`, '-frames:v', '1',
  path.join(OUT, 'contact_sheet.png')], { stdio: 'inherit' });
console.log(JSON.stringify({ mp4, seconds: +(f / FPS).toFixed(1), mb: +(fs.statSync(mp4).size / 1e6).toFixed(1) }));
