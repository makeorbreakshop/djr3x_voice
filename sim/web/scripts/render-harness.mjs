#!/usr/bin/env node
/**
 * Screenshot regression harness for the R3X sim.
 *
 *   npm run render:check                 # render every shot, diff against baselines
 *   npm run render:check -- --update     # accept the current renders as the baselines
 *   npm run render:check -- --only face,photo-oga-front-low
 *   npm run render:perf                  # fps at 1440x900 @2x, every quality level (unpaced)
 *
 * Headless Chrome is driven over the DevTools protocol (no puppeteer). The page runs in
 * still mode (src/still.ts): virtual clock, seeded Math.random, frozen film grain, fixed
 * render resolution, fixed pose and LED state. Each shot is a named camera; a few are
 * matched to reference photos, which the contact sheet shows side by side.
 *
 * Renders are of the kit-derived model (gitignored, CC BY-NC), so outputs and baselines
 * live in gitignored folders next to this script's package: .render-out/ and
 * .render-baselines/. Reference photos are linked in place, never copied.
 *
 * Options:
 *   --url <u>          sim to shoot (default: http://localhost:5391, else starts vite)
 *   --out <dir>        output folder (default .render-out)
 *   --baselines <dir>  baseline folder (default .render-baselines)
 *   --update           copy this run's renders over the baselines
 *   --only a,b         shots to run (names or prefixes, e.g. turn)
 *   --quality q        high | balanced | performance (default high)
 *   --max-diff <pct>   fail when more than this % of pixels differ (default 0.5)
 *   --min-ssim <v>     fail when SSIM drops below this (default 0.98)
 *   --refs <dir>       reference photo folder (default ~/Desktop/DJ-R3X/Reference Photos)
 *   --chrome <path>    Chrome binary (default: CHROME_PATH or the macOS app)
 *   --extra <k=v&..>   extra URL flags (e.g. tonemap=agx, post=hot)
 *   --perf             measure fps instead of shooting
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// ------------------------------------------------------------------ options

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(`--${name}`);
const opt = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : fallback;
};
const OUT = path.resolve(WEB, opt('out', '.render-out'));
const BASE = path.resolve(WEB, opt('baselines', '.render-baselines'));
const REFS = opt('refs', path.join(os.homedir(), 'Desktop/DJ-R3X/Reference Photos'));
const QUALITY = opt('quality', 'high');
const MAX_DIFF = Number(opt('max-diff', 0.5));
const MIN_SSIM = Number(opt('min-ssim', 0.98));
const ONLY = opt('only', '')?.split(',').filter(Boolean) ?? [];
const CHROME = opt('chrome', process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');

// ------------------------------------------------------------------ shots

/**
 * Cameras. The droid faces +Z and stands on the floor at y = 0; the head is at ~0.8 m.
 * `ref` is a photo in the reference folder shown next to the render on the contact sheet.
 */
const TARGET = [0, 0.5, 0];
const turntable = Array.from({ length: 8 }, (_, k) => {
  const a = (k * Math.PI) / 4;
  return {
    name: `turn-${String(k * 45).padStart(3, '0')}`,
    size: [960, 960],
    cam: { pos: [Math.sin(a) * 2.1, 0.75, Math.cos(a) * 2.1], target: TARGET, fov: 32 },
  };
});
export const SHOTS = [
  { name: 'full', size: [1280, 800], cam: { pos: [0.9, 0.85, 1.9], target: [0, 0.5, 0], fov: 35 }, ref: 'figure-front_actionfigurebarbecue.jpg' },
  { name: 'face', size: [1280, 800], cam: { pos: [0.16, 0.84, 0.9], target: [0, 0.77, 0.08], fov: 35 }, ref: 'figure-head_futureoftheforce.jpg' },
  { name: 'face-speaking', state: 'speaking', size: [1280, 800], cam: { pos: [0.05, 0.8, 0.62], target: [0, 0.77, 0.08], fov: 35 } },
  { name: 'chest', size: [1280, 800], cam: { pos: [-0.55, 0.5, 0.42], target: [-0.1, 0.45, 0.06], fov: 35 } },
  { name: 'arms', size: [1280, 800], cam: { pos: [0.1, 0.6, 1.2], target: [0, 0.5, 0.1], fov: 35 } },
  // WDWNT 2019 (900x1200 phone photo): from below the booth counter, a little to camera
  // right (the logic panels face the lens), looking up; head turned to the camera, hero arm
  // at rest. Phone lens at ~36 deg vertical field of view.
  { name: 'photo-oga-front-low', size: [900, 1200], pose: { head_pan: -24 }, cam: { pos: [-0.62, 0.44, 1.5], target: [0.08, 0.6, 0.05], fov: 36 }, ref: 'oga-front-low_wdwnt-2019.jpg' },
  // StarWars.com gallery still (Oga's Cantina, 1280x640): square on to the logic panels and the
  // RX-24 plate, lens between the rings and the head. The rest-pose check (show/SPEC.md "Frame
  // and zeros"): panels under the plate, hero arm up on camera right, poker arm out toward camera
  // right, throttle arm hanging on camera left.
  { name: 'photo-starwars-front', size: [1036, 540], pose: { hero_shoulder: -20, poker_shoulder: 10, head_lift: 20 }, cam: { pos: [0, 0.6, 2.1], target: [0.02, 0.53, 0], fov: 23 }, ref: 'starwars-front_starwarscom.webp' },
  ...turntable,
];

/**
 * Fixed pose and LED state, 4 simulated seconds after boot: hero arm raised, everything
 * else at rest unless a shot's `pose` says otherwise (degrees per joint). Shots sharing a
 * state and pose share one page load. `idle` is the face in the park photos
 * (amber eyes, dim mouth); `speaking` is INTERACTIVE with the mouth held at a steady level.
 */
const setup = (state, pose = {}) => `(async () => {
  const r = window.__r3x, s = window.__r3xStill, p = r.performer;
  const pose = Object.assign({ hero_shoulder: 20 }, ${JSON.stringify(pose)});
  p.command({ cmd: 'autonomy', on: false });
  p.command({ cmd: 'look', pan_tilt: null });
  document.getElementById('look').checked = false;
  document.getElementById('look').dispatchEvent(new Event('change'));
  for (const j of r.rig.joints.keys()) p.command({ cmd: 'jog', joint: j, value: pose[j] ?? 0 });
  if (${JSON.stringify(state)} === 'speaking') {
    p.command({ cmd: 'mode', mode: 'INTERACTIVE' });
    p.command({ cmd: 'speech_started' });
  }
  await s.advance(150);
  if (${JSON.stringify(state)} === 'speaking') p.command({ cmd: 'amplitude', value: 150 / 255 });
  await s.advance(90);
  return { t: s.now, state: ${JSON.stringify(state)} };
})()`;

// ------------------------------------------------------------------ chrome over CDP

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function reachable(url) {
  try {
    return (await fetch(url, { signal: AbortSignal.timeout(1500) })).ok;
  } catch {
    return false;
  }
}

async function startServer() {
  const want = opt('url', null);
  if (want) return { url: want.replace(/\/$/, ''), stop() {} };
  if (await reachable('http://localhost:5391/')) return { url: 'http://localhost:5391', stop() {} };
  const port = 5399;
  const vite = spawn('npx', ['vite', '--port', String(port), '--strictPort'], { cwd: WEB, stdio: 'ignore' });
  const url = `http://localhost:${port}`;
  for (let i = 0; i < 100 && !(await reachable(url + '/')); i++) await sleep(200);
  return { url, stop: () => vite.kill() };
}

async function launchChrome({ uncapped = false } = {}) {
  const port = 9300 + Math.floor(Math.random() * 500);
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'r3x-harness-'));
  const args = [
    '--headless=new', `--remote-debugging-port=${port}`, '--hide-scrollbars', '--mute-audio',
    '--autoplay-policy=no-user-gesture-required', '--enable-gpu', '--ignore-gpu-blocklist',
    '--use-angle=metal', '--window-size=1440,900', `--user-data-dir=${dataDir}`,
  ];
  if (uncapped) args.push('--disable-gpu-vsync', '--disable-frame-rate-limit');
  const proc = spawn(CHROME, [...args, 'about:blank'], { stdio: 'ignore' });
  let tabs = [];
  for (let i = 0; i < 75 && !tabs.length; i++) {
    try { tabs = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).filter((t) => t.type === 'page'); } catch { /* not up */ }
    if (!tabs.length) await sleep(200);
  }
  if (!tabs.length) throw new Error(`Chrome did not start (${CHROME})`);
  const ws = new WebSocket(tabs[0].webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0;
  const pending = new Map();
  const logs = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
    if (d.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(d.params.type)) {
      logs.push(`${d.params.type}: ${d.params.args.map((a) => a.value ?? a.description ?? '').join(' ')}`);
    }
    if (d.method === 'Runtime.exceptionThrown') logs.push(`exception: ${d.params.exceptionDetails.exception?.description ?? d.params.exceptionDetails.text}`);
  };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? 'evaluate failed');
    return r.result?.result?.value;
  };
  await send('Page.enable');
  await send('Runtime.enable');
  return {
    send, js, logs,
    async open(url, w, h, dpr) {
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: dpr, mobile: false });
      await send('Page.navigate', { url });
      for (let i = 0; i < 240; i++) {
        if (await js("document.readyState === 'complete' && !document.getElementById('loading') && !!window.__r3x?.performer && !!window.__r3x?.rig").catch(() => false)) return;
        await sleep(250);
      }
      throw new Error('sim did not finish loading (is the model built? sim/model/build.sh)');
    },
    async close() {
      ws.close();
      const exited = new Promise((r) => proc.once('exit', r));
      proc.kill();
      await Promise.race([exited, sleep(3000)]);
      try { fs.rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* temp dir; the OS cleans it */ }
    },
  };
}

// ------------------------------------------------------------------ image comparison

const readPng = (f) => PNG.sync.read(fs.readFileSync(f));

/** Mean SSIM over 8x8 windows (stride 4) of Rec.709 luma. */
function ssim(a, b) {
  const { width: w, height: h } = a;
  const luma = (img) => {
    const y = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) y[i] = 0.2126 * img.data[i * 4] + 0.7152 * img.data[i * 4 + 1] + 0.0722 * img.data[i * 4 + 2];
    return y;
  };
  const ya = luma(a);
  const yb = luma(b);
  const c1 = (0.01 * 255) ** 2;
  const c2 = (0.03 * 255) ** 2;
  let sum = 0;
  let n = 0;
  for (let y0 = 0; y0 + 8 <= h; y0 += 4) {
    for (let x0 = 0; x0 + 8 <= w; x0 += 4) {
      let ma = 0, mb = 0, va = 0, vb = 0, cov = 0;
      for (let y = y0; y < y0 + 8; y++) for (let x = x0; x < x0 + 8; x++) { ma += ya[y * w + x]; mb += yb[y * w + x]; }
      ma /= 64; mb /= 64;
      for (let y = y0; y < y0 + 8; y++) {
        for (let x = x0; x < x0 + 8; x++) {
          const da = ya[y * w + x] - ma, db = yb[y * w + x] - mb;
          va += da * da; vb += db * db; cov += da * db;
        }
      }
      va /= 63; vb /= 63; cov /= 63;
      sum += ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2));
      n++;
    }
  }
  return sum / n;
}

function compare(name) {
  const cur = path.join(OUT, `${name}.png`);
  const base = path.join(BASE, `${name}.png`);
  if (!fs.existsSync(base)) return { status: 'new' };
  const a = readPng(cur);
  const b = readPng(base);
  if (a.width !== b.width || a.height !== b.height) return { status: 'fail', note: `size ${a.width}x${a.height} vs baseline ${b.width}x${b.height}` };
  const diff = new PNG({ width: a.width, height: a.height });
  const bad = pixelmatch(a.data, b.data, diff.data, a.width, a.height, { threshold: 0.1, alpha: 0.35 });
  fs.writeFileSync(path.join(OUT, `${name}.diff.png`), PNG.sync.write(diff));
  const pct = (100 * bad) / (a.width * a.height);
  const s = ssim(a, b);
  return { status: pct > MAX_DIFF || s < MIN_SSIM ? 'fail' : 'pass', pct, ssim: s };
}

// ------------------------------------------------------------------ contact sheet

function contactSheet(rows, meta) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const img = (src, label) => (src ? `<figure><img src="${esc(src)}" loading="lazy"><figcaption>${esc(label)}</figcaption></figure>` : '<figure class="none"></figure>');
  const body = rows.map((r) => {
    const refPath = r.ref ? path.join(REFS, r.ref) : null;
    const ref = refPath && fs.existsSync(refPath) ? pathToFileURL(refPath).href : null;
    const base = fs.existsSync(path.join(BASE, `${r.name}.png`)) ? pathToFileURL(path.join(BASE, `${r.name}.png`)).href : null;
    const diff = fs.existsSync(path.join(OUT, `${r.name}.diff.png`)) && r.status !== 'new' ? `${r.name}.diff.png` : null;
    const metrics = r.status === 'new' ? 'no baseline' : r.pct === undefined ? r.note : `${r.pct.toFixed(3)}% px differ · SSIM ${r.ssim.toFixed(4)}`;
    return `<section class="${r.status}"><h2>${esc(r.name)} <span>${esc(r.status)}</span> <small>${esc(metrics)}</small></h2>
<div class="row">${img(`${r.name}.png`, 'this run')}${img(base, 'baseline')}${img(diff, 'diff')}${img(ref, r.ref ? `reference: ${r.ref}` : '')}</div></section>`;
  }).join('\n');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>R3X render check</title><style>
:root{color-scheme:dark;--bg:#0b0c10;--fg:#d6d9e0;--muted:#7d8391;--pass:#57d38c;--fail:#ff6b6b;--new:#e0b050}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 ui-sans-serif,system-ui,sans-serif}
header p{color:var(--muted);margin:4px 0 16px}
section{border-top:1px solid #20232b;padding:12px 0}
h2{font-size:15px;margin:0 0 8px}h2 span{font-size:11px;text-transform:uppercase;letter-spacing:.08em;padding:2px 6px;border-radius:99px;border:1px solid}
.pass h2 span{color:var(--pass)}.fail h2 span{color:var(--fail)}.new h2 span{color:var(--new)}
h2 small{color:var(--muted);font-weight:400;margin-left:8px}
.row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
figure{margin:0}figure img{width:100%;height:auto;display:block;background:#000;border-radius:4px}
figcaption{color:var(--muted);font-size:11px;margin-top:4px;overflow-wrap:anywhere}
@media (max-width:700px){.row{grid-template-columns:1fr 1fr}}
</style></head><body><header><h1>R3X render check</h1>
<p>${esc(meta)}</p></header>${body}</body></html>`;
}

// ------------------------------------------------------------------ runs

async function shoot(server) {
  fs.mkdirSync(OUT, { recursive: true });
  const shots = SHOTS.filter((s) => !ONLY.length || ONLY.some((o) => s.name === o || s.name.startsWith(o)));
  if (!shots.length) throw new Error(`no shot matches ${ONLY.join(',')}`);
  const chrome = await launchChrome();
  const rows = [];
  const states = [];
  const extra = opt('extra', '') ? `&${opt('extra', '')}` : '';
  try {
    // One fresh page per LED state; within it, shots only move the camera (clock frozen).
    const key = (s) => `${s.state ?? 'idle'} ${JSON.stringify(s.pose ?? {})}`;
    for (const k of [...new Set(shots.map(key))]) {
      const group = shots.filter((s) => key(s) === k);
      const state = group[0].state ?? 'idle';
      const [w0, h0] = group[0].size;
      await chrome.open(`${server.url}/?offline&still&seed=1&quality=${QUALITY}${extra}`, w0, h0, 1);
      const info = await chrome.js(setup(state, group[0].pose));
      states.push(`${k}: t=${(info.t / 1000).toFixed(2)} s, face ${info.state}`);
      for (const s of group) {
        const [w, h] = s.size;
        await chrome.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
        const { pos, target, fov } = s.cam;
        await chrome.js(`(async () => {
          const r = window.__r3x;
          await window.__r3xStill.hold(2);
          r.camera.fov = ${fov};
          r.camera.position.set(${pos});
          r.controls.target.set(${target});
          r.camera.updateProjectionMatrix();
          r.controls.update();
          await window.__r3xStill.hold(4);
        })()`);
        const shot = await chrome.send('Page.captureScreenshot', { format: 'png' });
        fs.writeFileSync(path.join(OUT, `${s.name}.png`), Buffer.from(shot.result.data, 'base64'));
        const res = compare(s.name);
        rows.push({ ...s, ...res });
        const m = res.pct === undefined ? res.note ?? '' : `${res.pct.toFixed(3)}% · SSIM ${res.ssim.toFixed(4)}`;
        console.log(`${res.status.padEnd(4)}  ${s.name.padEnd(22)} ${m}`);
      }
    }
  } finally {
    if (chrome.logs.length) console.log(`\nconsole:\n  ${chrome.logs.slice(0, 20).join('\n  ')}`);
    await chrome.close();
  }
  if (flag('update')) {
    fs.mkdirSync(BASE, { recursive: true });
    for (const s of rows) fs.copyFileSync(path.join(OUT, `${s.name}.png`), path.join(BASE, `${s.name}.png`));
    console.log(`\nbaselines updated: ${BASE}`);
  }
  const meta = `${new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })} ET · quality=${QUALITY} · ` +
    `${states.join('; ')}${extra ? ` · url ${extra.slice(1)}` : ''} · fail when >${MAX_DIFF}% px differ or SSIM <${MIN_SSIM}`;
  fs.writeFileSync(path.join(OUT, 'index.html'), contactSheet(rows, meta));
  console.log(`\ncontact sheet: ${path.join(OUT, 'index.html')}`);
  const failed = rows.filter((r) => r.status === 'fail');
  if (failed.length && !flag('update')) {
    console.log(`${failed.length} shot(s) changed: ${failed.map((r) => r.name).join(', ')}`);
    process.exitCode = 1;
  }
}

/** Frame rate at 1440x900 @2x with vsync off (so headroom above 60 shows). */
async function perf(server) {
  const qualities = opt('quality', null) ? [QUALITY] : ['high', 'balanced', 'performance'];
  const seconds = Number(opt('seconds', 6));
  for (const q of qualities) {
    const chrome = await launchChrome({ uncapped: true });
    try {
      await chrome.open(`${server.url}/?offline&pace=0&quality=${q}${opt('extra', '') ? '&' + opt('extra', '') : ''}`, 1440, 900, 2);
      await sleep(3000); // shader compiles, first frames, dynamic resolution settling
      const r = await chrome.js(`new Promise((done) => {
        const dts = []; let last = performance.now(); const end = last + ${seconds * 1000};
        const f = (t) => { dts.push(t - last); last = t; if (t < end) requestAnimationFrame(f); else done(dts); };
        requestAnimationFrame((t) => { last = t; requestAnimationFrame(f); });
      })`);
      const post = await chrome.js('window.__r3x?.post ? { ratio: window.__r3x.post.pixelRatio, ao: window.__r3x.post.aoEnabled } : null');
      const sorted = [...r].sort((a, b) => a - b);
      const mean = r.reduce((a, b) => a + b, 0) / r.length;
      const pct = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
      console.log(`quality=${q.padEnd(4)} fps=${(1000 / mean).toFixed(1).padStart(6)}  frame p50=${pct(0.5).toFixed(2)} ms p95=${pct(0.95).toFixed(2)} ms` +
        `  (${r.length} frames${post ? `, render scale ${post.ratio.toFixed(2)}, AO ${post.ao ? 'on' : 'off'}` : ''})`);
      if (chrome.logs.length) console.log(`  console: ${chrome.logs.slice(0, 5).join(' | ')}`);
    } finally {
      await chrome.close();
    }
  }
}

const server = await startServer();
try {
  if (flag('perf')) await perf(server);
  else await shoot(server);
} finally {
  server.stop();
}
