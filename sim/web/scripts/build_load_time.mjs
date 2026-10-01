// Cold-load time of Build with an assembly, in headless Chrome against the running dev server.
//   node scripts/build_load_time.mjs [http://localhost:5397] [r3x_droid]
// A fresh temp profile each run (no cache); Chrome is always killed at the end.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const URL0 = process.argv[2] || 'http://localhost:5397/';
const ASM = process.argv[3] || 'r3x_droid';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'r3x-cdp-'));
const port = 9300 + Math.floor(Math.random() * 500);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, '--no-first-run',
  '--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist', '--window-size=1400,900', 'about:blank'], { stdio: 'ignore' });
const kill = () => { try { chrome.kill('SIGKILL'); } catch {} try { fs.rmSync(dir, { recursive: true, force: true }); } catch {} };
process.on('exit', kill);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws;
for (let i = 0; i < 50 && !ws; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    const page = list.find((t) => t.type === 'page');
    if (page) ws = new WebSocket(page.webSocketDebuggerUrl);
  } catch { await sleep(200); }
}
await new Promise((r) => ws.addEventListener('open', r));
let id = 0;
const pending = new Map();
const events = [];
ws.addEventListener('message', (m) => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else events.push(d);
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send('Network.enable');
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Page.navigate', { url: URL0 });
for (let i = 0; i < 100 && !(await ev(`!!document.querySelector('[data-stage-mode=build]')`)); i++) await sleep(200);
await ev(`localStorage.clear(); 0`);
const t0 = await ev(`performance.now()`);
await ev(`document.querySelector('[data-stage-mode=build]').click(); 0`);
for (let i = 0; i < 100 && !(await ev(`!!document.querySelector('#bp-assembly option[value=${ASM}]')`)); i++) await sleep(100);
await ev(`(()=>{const s=document.querySelector('#bp-assembly'); globalThis.__r3xBuildLoaded=null; s.value='${ASM}'; s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
let done = null;
for (let i = 0; i < 1800 && !done; i++) { done = await ev(`(()=>{const d=globalThis.__r3xBuildLoaded; return d && d.url.includes('${ASM}/') ? d.at : null})()`); if (!done) await sleep(100); }
const res = await ev(`(()=>{const r=performance.getEntriesByType('resource').filter(e=>e.name.includes('/mech/out/')); return r.length ? {first: Math.min(...r.map(e=>e.startTime)), last: Math.max(...r.map(e=>e.responseEnd)), n: r.length} : null})()`);
const bytes = events.filter((e) => e.method === 'Network.loadingFinished').reduce((s, e) => s + e.params.encodedDataLength, 0);
const reqs = new Map(events.filter((e) => e.method === 'Network.requestWillBeSent').map((e) => [e.params.requestId, e.params.request.url]));
const mech = events.filter((e) => e.method === 'Network.loadingFinished' && (reqs.get(e.params.requestId) || '').includes('/mech/out/'));
const mb = mech.reduce((s, e) => s + e.params.encodedDataLength, 0) / 1e6;
console.log(JSON.stringify({ asm: ASM, first_view_s: done ? +((done - t0) / 1000).toFixed(2) : null, mech_requests: mech.length,
  mech_mb: +mb.toFixed(1), all_mb: +(bytes / 1e6).toFixed(1),
  mech_fetch_s: res ? { first: +((res.first - t0) / 1000).toFixed(2), last: +((res.last - t0) / 1000).toFixed(2) } : null }));
ws.close();
kill();
process.exit(0);
