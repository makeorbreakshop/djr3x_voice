#!/usr/bin/env node
/**
 * Regression harness for the shared viewer (viewer.html): what only a real browser can show.
 *
 *   npm run viewer:check                  # against the dev server on :5399, else one started here
 *   npm run viewer:check -- --url http://localhost:5399
 *   npm run viewer:check -- --only idle,link
 *
 * Headless Chrome over the DevTools protocol (no puppeteer), GPU on, 1440x900 at device scale factor 2,
 * a fresh profile (no saved state). Asserts counts and states only, never frame times (headless timings
 * are not to be trusted). Uses the dev build's `window.__viewer` ({ workbench, post }, page.ts) and
 * post.ts's `buildFrames` counter (Build frames by AO: `move` the cheap pass, `still` the accumulating
 * full pass, `plain` none).
 *
 * Exit: 0 every check passed, 1 a check failed, 2 the harness could not run (Chrome, the server),
 * 3 mech/out (the built assemblies, `cd mech && .venv/bin/python -m workbench build ...`) is missing.
 * The pure halves of these behaviours are unit tests: test/regressions.test.ts.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const MECH_OUT = path.resolve(WEB, '../../mech/out');
const argv = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : fallback;
};
const ONLY = opt('only', '').split(',').filter(Boolean);
const CHROME = opt('chrome', process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

if (!fs.existsSync(path.join(MECH_OUT, 'index.json')) || !fs.existsSync(path.join(MECH_OUT, 'r3x_droid/manifest.json'))) {
  console.error(`viewer-check: no built assemblies at ${MECH_OUT} (index.json, r3x_droid/). Build them first: cd mech && .venv/bin/python -m workbench build r3x_droid`);
  process.exit(3);
}

// ------------------------------------------------------------------ server and Chrome

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
  if (await reachable('http://localhost:5399/viewer.html')) return { url: 'http://localhost:5399', stop() {} };
  const port = 5399;
  const vite = spawn('npx', ['vite', '--port', String(port), '--strictPort'], { cwd: WEB, stdio: 'ignore' });
  const url = `http://localhost:${port}`;
  for (let i = 0; i < 150 && !(await reachable(url + '/viewer.html')); i++) await sleep(200);
  if (!(await reachable(url + '/viewer.html'))) {
    vite.kill();
    throw new Error('the dev server did not start on :5399');
  }
  return { url, stop: () => vite.kill() };
}

async function launchChrome() {
  const port = 9300 + Math.floor(Math.random() * 500);
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'r3x-viewer-check-'));
  const args = ['--headless=new', `--remote-debugging-port=${port}`, '--hide-scrollbars', '--mute-audio', '--no-first-run',
    '--enable-gpu', '--ignore-gpu-blocklist', '--use-angle=metal', '--window-size=1440,900', `--user-data-dir=${dataDir}`];
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
  const errors = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
    if (d.method === 'Runtime.exceptionThrown') errors.push(d.params.exceptionDetails.exception?.description ?? d.params.exceptionDetails.text);
  };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const js = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? 'evaluate failed');
    return r.result?.result?.value;
  };
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false });
  return {
    send, js, errors,
    async close() {
      ws.close();
      const exited = new Promise((r) => proc.once('exit', r));
      proc.kill();
      await Promise.race([exited, sleep(3000)]);
      try { fs.rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* temp dir */ }
    },
  };
}

// ------------------------------------------------------------------ page helpers

let base = '';
let c;
const until = async (expr, ms = 60_000, what = expr) => {
  for (const t0 = Date.now(); Date.now() - t0 < ms;) {
    if (await c.js(expr).catch(() => false)) return;
    await sleep(150);
  }
  throw new Error(`timed out waiting for ${what}`);
};
/** A fresh load of the viewer (with `hash`), waited until the library and the design it opens are in. */
async function open(hash = '') {
  await c.send('Page.navigate', { url: `${base}/viewer.html?check=${Date.now()}${hash}` });
  await until("!!window.__viewer && !!document.querySelector('[data-lib]')", 90_000, 'the viewer to load');
  await sleep(1500); // the link applied, the camera landed
}
/** Same document: only the fragment changes (a pasted link), no reload. */
async function hashTo(hash) {
  await c.js(`location.hash = ${JSON.stringify(hash)}; 0`);
  await sleep(2500);
}
const mouse = (type, x, y, buttons = 1, modifiers = 0) => c.send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' && !buttons ? 'none' : 'left', buttons, clickCount: 1, modifiers });
const wheel = (x, y, deltaY, modifiers = 0) => c.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY, modifiers });
async function drag(x0, y0, dx, dy, steps = 12, holdMs = 0) {
  await mouse('mouseMoved', x0, y0, 0);
  await mouse('mousePressed', x0, y0);
  for (let i = 1; i <= steps; i++) {
    await mouse('mouseMoved', x0 + (dx * i) / steps, y0 + (dy * i) / steps);
    await sleep(16);
  }
  if (holdMs) await sleep(holdMs);
  await mouse('mouseReleased', x0 + dx, y0 + dy);
}
/** Canvas buffer sizes seen over a stretch (sampled every 16 ms in the page). */
/**
 * The pixel ratios Build sets over a stretch (post.ts applyScale -> renderer.setPixelRatio, wrapped here):
 * every one must be Build's own scale (buildDyn: native, or lower only where this machine's frames run
 * long, which headless GPU frames can). One set at the quality's scale is the bug: a buffer reallocation
 * at each gesture's start and end. `sizes`: the canvas sizes seen, sampled every 16 ms.
 */
const watchSizes = () => c.js(`(() => { const post = window.__viewer.post; const r = post.renderer; const d = post.buildDyn;
  const cv = document.querySelector('#stage canvas');
  window.__px = { sets: [], foreign: [], sizes: new Set([cv.width + 'x' + cv.height]) };
  if (!r.__wrapped) { const set = r.setPixelRatio.bind(r); r.setPixelRatio = (v) => { const z = window.__px;
    if (z) { z.sets.push(v); if (Math.abs(v - d.scale) > 1e-6 && Math.abs(v - d.max) > 1e-6) z.foreign.push(v); } set(v); }; r.__wrapped = true; }
  clearInterval(window.__sizeT); window.__sizeT = setInterval(() => window.__px.sizes.add(cv.width + 'x' + cv.height), 16); return 0; })()`);
const sizesSeen = () => c.js('(() => { clearInterval(window.__sizeT); const z = window.__px; return { seen: [...z.sizes], sets: z.sets.map((v) => +v.toFixed(3)), foreign: z.foreign.map((v) => +v.toFixed(3)) }; })()');
const frames = () => c.js('({ ...window.__viewer.post.buildFrames })');
const counts = () => c.js(`(() => { const wb = window.__viewer.workbench; let vis = 0, own = 0, faint = 0;
  for (const [id, po] of wb.parts) { if (!po.mesh.visible) continue; vis++; if (po.mat.opacity < 1) faint++; if (!wb.scope || wb.scope.parts.has(id)) own++; }
  return { vis, own, faint, total: wb.scope ? wb.scope.parts.size : wb.parts.size, look: wb.look, ctx: wb.context }; })()`);
const click = (sel) => c.js(`(() => { const b = document.querySelector(${JSON.stringify(sel)}); if (!b) throw new Error('no ' + ${JSON.stringify(sel)}); b.click(); return 0; })()`);
const openLib = async (id) => { await click(`[data-lib="${id}"]`); await sleep(1500); };
const CX = 720, CY = 470; // the viewport between the panels

// ------------------------------------------------------------------ checks

const results = [];
const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail });
const CHECKS = {
  async idle() {
    await open('#at=lib:hunter&look=mechanism');
    await drag(CX, CY, 140, 30);
    await sleep(400); // settled (150 ms) and its still frames (the AO accumulation) drawn
    await watchSizes();
    const f0 = await frames();
    await sleep(2000);
    const f1 = await frames();
    const drawn = f1.still + f1.move + f1.plain - f0.still - f0.move - f0.plain;
    check('idle after a drag draws no frame (2 s)', drawn === 0, `${drawn} frames`);
    const s = await sizesSeen();
    check('idle holds one canvas size', s.seen.length === 1, s.seen.join(' '));
  },
  async gestures() {
    await open('#at=lib:hunter&look=mechanism');
    await watchSizes();
    const f0 = await frames();
    await mouse('mouseMoved', CX, CY, 0);
    await mouse('mousePressed', CX, CY);
    for (let i = 1; i <= 20; i++) { await mouse('mouseMoved', CX + i * 8, CY + i * 2); await sleep(16); }
    const mid = await frames();
    await mouse('mouseReleased', CX + 160, CY + 40);
    for (let i = 0; i < 10; i++) { await wheel(CX, CY, i < 5 ? -120 : 120); await sleep(30); }
    for (let i = 0; i < 10; i++) { await wheel(CX, CY, i < 5 ? -8 : 8, 2 /* ctrl: a pinch */); await sleep(16); }
    await sleep(800);
    const f1 = await frames();
    const s = await sizesSeen();
    check('orbit, wheel zoom and pinch: no pixel ratio but Build\'s own', s.foreign.length === 0,
      `ratios set ${s.sets.join(',') || 'none'}; foreign ${s.foreign.join(',') || 'none'}; sizes ${s.seen.join(' ')}`);
    check('moving frames draw the cheap AO', mid.move - f0.move > 0 && mid.still === f0.still && mid.plain === f0.plain,
      `during the drag: ${mid.move - f0.move} cheap AO, ${mid.still - f0.still} full AO, ${mid.plain - f0.plain} none`);
    check('a settle accumulates the full AO over a few frames', f1.still - mid.still >= 2, `${f1.still - mid.still} full-AO frames after the gestures`);
    const warm = await c.js('globalThis.__r3xWarm ?? null');
    check('shader programs warmed after the first view', warm && warm.programs > 0, warm ? `${warm.programs} programs` : 'not run');
  },
  async settle() {
    await open('#at=lib:hunter&look=mechanism');
    await until('!!globalThis.__r3xDeferredDone || !globalThis.__r3xProgressive', 60_000, 'the build to load');
    await sleep(1500);
    await watchSizes();
    await drag(CX, CY, 120, 20);
    const f0 = await frames();
    await sleep(600); // settled (150 ms), the accumulation drawn
    const f1 = await frames();
    await sleep(1500);
    const f2 = await frames();
    const s = await sizesSeen();
    const n = f1.still - f0.still;
    check('the settle draws SETTLE_FRAMES (4) accumulation frames, then stops', n === 4 && f2.still + f2.move + f2.plain === f1.still + f1.move + f1.plain,
      `${n} full-AO frames, ${f1.move - f0.move} cheap after release, ${f2.still + f2.move + f2.plain - f1.still - f1.move - f1.plain} in the next 1.5 s`);
    // (Build's own dynamic scale may step where headless frames run long: only a foreign ratio is the bug)
    check('no pixel ratio but Build\'s own across a drag and its settle', s.foreign.length === 0, `ratios ${s.sets.join(',') || 'none'}; foreign ${s.foreign.join(',') || 'none'}`);
  },
  async context() {
    await open('#at=lib:hunter&look=mechanism');
    await click('[data-ctx="ghost"]');
    const g = await counts();
    await click('[data-ctx="hide"]');
    const h = await counts();
    check('Ghost and Hide differ in a library design', g.vis > h.vis && h.vis === h.own, `ghost ${g.vis} parts, hide ${h.vis} (own ${h.own})`);
    await click('[data-crumb="library"]');
    await sleep(1500);
    const off = await c.js("document.querySelector('[data-ctx]').disabled");
    check('Ghost/Hide disabled with nothing in focus', off === true, `disabled ${off}`);
  },
  async looks() {
    await open('#at=lib:morton&look=exterior&ctx=hide');
    const m = await counts();
    check('Lower cage draws its own parts in Exterior', m.look === 'exterior' && m.own === m.total && m.total > 0, `${m.own}/${m.total} in ${m.look}`);
    await open('#at=lib:kit&look=mechanism');
    const k = await counts();
    const dis = await c.js("[...document.querySelectorAll('[data-look]')].map((b) => b.dataset.look + ':' + b.disabled).join(' ')");
    check('Kit shells forced to Exterior from a link', k.look === 'exterior', k.look);
    check('Kit shells: Mechanism and X-ray disabled', dis.includes('mechanism:true') && dis.includes('inspect:true') && dis.includes('exterior:false'), dis);
  },
  async link() {
    await open('#at=lib:hunter&look=mechanism');
    await click('[data-look="inspect"]');
    await click('[data-ctx="hide"]');
    await c.js(`(() => { const x = document.getElementById('bv-explode'); x.value = '0.3'; x.dispatchEvent(new Event('input'));
      const wb = window.__viewer.workbench; let n; wb.forEachNode((m) => { if (m.asm.joints.some((j) => j.id === 'visor')) n = m; });
      wb.setJoint(n, 'visor', -10); const T = wb.cameraState().pos.constructor;
      wb.setCameraState({ pos: new T(-0.4, 1.0, 0.9), target: new T(0, 0.86, 0.02) }); return 0; })()`);
    await sleep(2000);
    const href = await c.js('location.href');
    const want = { scope: 'hunter', look: 'inspect', ctx: 'hide', explode: 0.3, visor: -10, cam: '-0.400,1.000,0.900' };
    const state = () => c.js(`(() => { const wb = window.__viewer.workbench; let v = null; wb.forEachNode((m) => { if (m.pose.visor !== undefined) v = m.pose.visor; });
      return { scope: wb.scope?.id ?? null, look: wb.look, ctx: wb.context, explode: wb.explode, visor: v, cam: wb.cameraState().pos.toArray().map((x) => x.toFixed(3)).join() }; })()`);
    const same = (s) => Object.keys(want).every((k) => (typeof want[k] === 'number' ? Math.abs(s[k] - want[k]) < 1e-3 : s[k] === want[k]));
    await open(href.slice(href.indexOf('#')));
    const fresh = await state();
    check('a share link round-trips across a fresh load', same(fresh), JSON.stringify(fresh));
    await c.js('window.__noReload = 1; 0');
    await hashTo('#at=lib:column&look=mechanism');
    const hc = await state();
    const kept = await c.js('window.__noReload === 1');
    check('a hash change applies the link without a reload', kept && hc.scope === 'column' && hc.look === 'mechanism', JSON.stringify({ kept, ...hc }));
    check("a link's unmentioned settings take defaults", hc.ctx === 'ghost' && hc.explode === 0 && !hc.visor, JSON.stringify(hc));
    await hashTo(href.slice(href.indexOf('#')));
    const back = await state();
    check('a share link round-trips across a hash change', same(back), JSON.stringify(back));
  },
  async held() {
    await open('#at=lib:hunter&look=mechanism');
    await sleep(1500);
    const before = await c.js('location.href');
    await mouse('mouseMoved', CX, CY, 0);
    await mouse('mousePressed', CX, CY);
    let changed = 0;
    for (let i = 1; i <= 90; i++) { // ~1.6 s of a held drag, past the once-a-second write
      await mouse('mouseMoved', CX + i * 2, CY);
      await sleep(16);
      if ((await c.js('location.href')) !== before) changed++;
    }
    await mouse('mouseReleased', CX + 180, CY);
    await sleep(2500);
    const after = await c.js('location.href');
    check('the address does not change during a held drag', changed === 0, `${changed} changes while held`);
    check('the address follows once the drag ends', after !== before, after === before ? 'unchanged' : 'updated');
  },
  async progressive() {
    await open('#at=lib:hunter&look=mechanism');
    const r = await c.js(`(() => { const wb = window.__viewer.workbench; const f = globalThis.__r3xFirstView; const first = new Set(f.ids);
      const own = [...wb.scope.parts]; return { own: own.length, ownFirst: own.filter((id) => first.has(id)).length, first: first.size, pending: f.pending }; })()`);
    check("a link's design is all in the first view, before any context", r.own > 0 && r.ownFirst === r.own && r.first === r.own && r.pending > 0,
      `design ${r.ownFirst}/${r.own} in the first view; first view ${r.first} parts; ${r.pending} pending after it`);
    await until('!!globalThis.__r3xDeferredDone', 60_000, 'the context to stream in');
    const after = await c.js(`(() => { const wb = window.__viewer.workbench; let empty = 0;
      for (const po of wb.allParts) if (!po.lazy && !wb.variantHidden.has(po.node) && !po.mesh.geometry.attributes.position) empty++; return { empty, ctx: wb.context }; })()`);
    check('the context streams in after it (every shown part drawn)', after.empty === 0, `${after.empty} shown parts without a mesh`);
  },
  async viewer() {
    await open();
    const land = await c.js(`({ tab: document.querySelector('[data-src][aria-selected="true"]')?.dataset.src, first: document.querySelector('[data-src]')?.dataset.src,
      title: document.title, guide: getComputedStyle(document.getElementById('bb-guide')).display })`);
    check('lands on the Library', land.tab === 'library' && land.first === 'library', JSON.stringify(land));
    check('Instructions hidden on the Library list', land.guide === 'none', land.guide);
    const vis = {};
    for (const id of ['hunter', 'kit', 'column']) {
      await openLib(id);
      vis[id] = await c.js(`({ guide: getComputedStyle(document.getElementById('bb-guide')).display !== 'none', title: document.title, head: document.querySelector('#panel header h1').textContent })`);
    }
    check('Instructions only for the head', vis.hunter.guide && !vis.kit.guide && !vis.column.guide, Object.entries(vis).map(([k, v]) => `${k}:${v.guide}`).join(' '));
    check('header and title name the open design', vis.kit.title === 'Kit shells · DJ R3X' && vis.kit.head.startsWith('Kit shells'), `${vis.kit.title} | ${vis.kit.head}`);
    await click('[data-crumb="library"]');
    await sleep(1500);
    const out = await c.js(`({ list: !document.getElementById('bv-lib-pane').hidden, title: document.title })`);
    check('stepping out returns to the Library list', out.list && out.title === 'DJ R3X', JSON.stringify(out));
    // the sim (no viewer option): Our build first, as it was
    await c.send('Page.navigate', { url: `${base}/?check=${Date.now()}` });
    await until("!!document.querySelector('[data-stage-mode=build]')", 90_000, 'the sim to load');
    await c.js("document.querySelector('[data-stage-mode=build]').click(); 0");
    await until("!!document.querySelector('#bv-tree li')", 90_000, 'the sim\'s Build to load');
    const sim = await c.js(`({ first: document.querySelector('#sc-build [data-src]').dataset.src, viewer: 'viewer' in document.body.dataset,
      tab: document.querySelector('#sc-build [data-src][aria-selected="true"]')?.dataset.src })`);
    check('the sim is unchanged (Our build first and open, no viewer mode)', sim.first === 'build' && sim.tab === 'build' && !sim.viewer, JSON.stringify(sim));
  },
};

// ------------------------------------------------------------------ run

let server;
let code = 0;
try {
  server = await startServer();
  base = server.url;
  c = await launchChrome();
  for (const [name, fn] of Object.entries(CHECKS)) {
    if (ONLY.length && !ONLY.includes(name)) continue;
    try {
      await fn();
    } catch (e) {
      check(`${name}: ran`, false, String(e.message ?? e).split('\n')[0]);
    }
  }
} catch (e) {
  console.error(`viewer-check: could not run: ${e.message ?? e}`);
  code = 2;
} finally {
  await c?.close();
  server?.stop();
}
if (code) process.exit(code);
const w = Math.max(...results.map((r) => r.name.length));
for (const r of results) console.log(`${r.ok ? 'pass' : 'FAIL'}  ${r.name.padEnd(w)}  ${r.detail ?? ''}`);
if (c?.errors.length) console.log(`page exceptions: ${c.errors.length}\n  ${c.errors.slice(0, 5).join('\n  ')}`);
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
