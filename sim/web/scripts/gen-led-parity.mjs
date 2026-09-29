// Records LED emulator parity fixtures for the Rust port (rust/crates/r3x-performer-core,
// tests/leds.rs). Runs the TS firmware.ts / chest.ts / host.ts through a fixed driver loop
// (1 ms steps) with a seeded mulberry32, and writes the timed inputs plus sampled frames.
//
//   cd sim/web && node scripts/gen-led-parity.mjs
//
// Driver, per step t (keep in step with replay() in tests/leds.rs):
//   face.advanceTo(t); apply events with at <= t; if host: dual.tick(t), chest words ->
//   chestFw.write; chestFw.update(t); fold eye+mouth+chest bytes into an FNV-1a hash;
//   every `sample_every` ms emit a frame (hex buffers, hash, lines since the last frame).
import { createServer } from 'vite';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(here, '../../../rust/crates/r3x-performer-core/tests/parity');

function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/** chest.test.ts layout(): three panels ~31 deg apart, 8 dots + 3 windows each. */
function syntheticLayout() {
  const out = [];
  for (const deg of [-88.7, -57.7, -26.7]) {
    const a = (deg * Math.PI) / 180;
    const at = (y) => [Math.sin(a) * 0.14, y, Math.cos(a) * 0.14];
    for (let k = 0; k < 8; k++) out.push({ kind: 'dot', pos: at(0.4336 + k * 0.0055), normal: [0, 0, 1], w: 0.0024, h: 0.0024, panel: 'MS_P_1_Full' });
    for (let k = 0; k < 3; k++) out.push({ kind: 'window', pos: at(0.44 + k * 0.015), normal: [0, 0, 1], w: 0.014, h: 0.012, panel: 'MS_P_1_Full' });
  }
  return out;
}

const rigPath = resolve(here, '../public/model/rig.json');
const rig = existsSync(rigPath) ? JSON.parse(readFileSync(rigPath, 'utf8')).chest_lights : null;
const [rigLayout, rigName] = rig ? [rig, 'rig.json'] : [syntheticLayout(), 'synthetic'];

const server = await createServer({ root: resolve(here, '..'), server: { middlewareMode: true, hmr: false }, appType: 'custom', logLevel: 'error' });
const { RexFaceFirmware } = await server.ssrLoadModule('/src/firmware.ts');
const { ChestFirmware, ChestHost } = await server.ssrLoadModule('/src/chest.ts');
const { CantinaHostEmulator, DualHost, TtsAmplitudeAgc } = await server.ssrLoadModule('/src/host.ts');

const hex = (px) => Buffer.from(px.flat()).toString('hex');

function run(sc) {
  const face = new RexFaceFirmware({ random: mulberry32(sc.seed), oobAliasesMouth: sc.oob_aliases_mouth });
  const chestFw = new ChestFirmware(sc.chest_specs, mulberry32(sc.seed + 1));
  const tx = [];
  const faceHost = new CantinaHostEmulator(face, (dir, line, at) => tx.push([line, at]));
  const pending = [];
  const chestHost = new ChestHost((c) => pending.push(c));
  const dual = new DualHost(faceHost, chestHost);
  const agc = new TtsAmplitudeAgc();
  let rx = [];
  let ctx = [];
  const frames = [];
  let h = 0x811c9dc5;
  let ev = 0;
  const apply = ({ ev: name, a = [] }, t) => {
    switch (name) {
      case 'setMode': dual.setMode(a[0]); break;
      case 'listeningStarted': dual.listeningStarted(); break;
      case 'listeningStopped': dual.listeningStopped(); break;
      case 'llmChunk': dual.llmChunk(); break;
      case 'speechStarted': dual.speechStarted(); break;
      case 'rms': dual.amplitude(agc.next(a[0])); break;
      case 'speechEnded': dual.speechEnded(); agc.reset(); break;
      case 'eyeCommand': faceHost.eyeCommand(a[0], a[1]); break;
      case 'serviceStatus': chestHost.serviceStatus(a[0], a[1], a[2]); break;
      case 'faultHold': chestHost.faultHoldMs = a[0]; break;
      case 'music': chestHost.music(a[0], a[1]); break;
      case 'dj': chestHost.dj(a[0], a[1]); break;
      case 'sleep': chestHost.sleeping = a[0]; break;
      case 'chestBoot': chestHost.boot(t); break;
      case 'override': chestHost.override(a[0], a[1], t); break;
      case 'faceWrite': face.write(a[0]); break;
      case 'chestWrite': chestFw.write(a[0]); break;
      case 'blinkNow': face.blinkNow(); break;
      default: throw new Error(`unknown event ${name}`);
    }
  };
  for (let t = 0; t <= sc.end; t++) {
    face.advanceTo(t);
    while (ev < sc.events.length && sc.events[ev].at <= t) apply(sc.events[ev++], t);
    if (sc.host) {
      dual.tick(t);
      for (const c of pending.splice(0)) { chestFw.write(c + '\n'); ctx.push(c); }
    }
    chestFw.update(t);
    rx.push(...face.readLines());
    for (const b of [...face.eyeLeds, ...face.mouthLeds, ...chestFw.pixels].flat()) {
      h ^= b;
      h = Math.imul(h, 16777619) >>> 0;
    }
    if (t % sc.sample_every === 0) {
      frames.push({
        t, h, st: face.currentState, eye: hex(face.eyeLeds), mouth: hex(face.mouthLeds), chest: hex(chestFw.pixels),
        tx: tx.splice(0), rx, ctx, dropped: faceHost.droppedMouthResets,
      });
      rx = [];
      ctx = [];
    }
  }
  return { ...sc, frames };
}

// ------------------------------------------------------------------ scenarios

const e = (at, ev, ...a) => ({ at, ev, a });

/** A speech amplitude stream: int16 RMS per 16.7 ms chunk, syllable-shaped, seeded. */
function rmsStream(start, end, seed) {
  const r = mulberry32(seed);
  const out = [];
  for (let i = 0; ; i++) {
    const at = Math.round(start + i * 16.7);
    if (at >= end) break;
    const syl = Math.sin(i * 0.45) * 0.5 + 0.5;
    const v = Math.round(r() < 0.08 ? 0 : 300 + syl * 9000 * (0.4 + r() * 0.6));
    out.push(e(at, 'rms', v));
  }
  return out;
}

const conversation = [
  e(0, 'chestBoot'),
  e(500, 'setMode', 'IDLE'),
  e(3500, 'setMode', 'INTERACTIVE'),
  e(4000, 'listeningStarted'),
  e(5200, 'listeningStopped'), // THINKING: right-eye dot bug + mouth[0] alias
  e(6000, 'llmChunk'),
  e(6100, 'speechStarted'),
  ...rmsStream(6100, 8900, 11),
  e(8900, 'speechEnded'), // < 100 ms after the last mouth word: M000 dropped
  e(9500, 'music', true, 128),
  e(9800, 'faultHold', 2000),
  e(10000, 'serviceStatus', 'MusicControllerService', 'degraded', false),
  e(11000, 'eyeCommand', 'happy', 1.5),
  e(11200, 'eyeCommand', 'flash', 0),
  e(12000, 'override', 'X3', 1.0),
  e(12500, 'serviceStatus', 'ClaudeService', 'ERROR', true),
  e(13500, 'serviceStatus', 'ClaudeService', 'running', true),
  e(13600, 'dj', true, 100),
  e(13900, 'music', false),
  e(14000, 'listeningStarted'),
  e(14600, 'listeningStopped'),
  e(15000, 'speechStarted'),
  ...rmsStream(15000, 16800, 23),
  e(17000, 'speechEnded'), // quiet gap first: M000 goes out
  e(17500, 'faceWrite', 'R\n\u0000ÿZZ?\n'),
  e(18000, 'sleep', true),
  e(18200, 'dj', false),
  e(19000, 'setMode', 'IDLE'),
];

const idle = [
  e(0, 'setMode', 'IDLE'),
  e(26000, 'blinkNow'),
  e(26100, 'blinkNow'), // mid-blink: ignored
  e(33000, 'setMode', 'AMBIENT'),
  e(36000, 'blinkNow'), // not IDLE: ignored
  e(38000, 'setMode', 'IDLE'),
];

const rawFace = [
  e(0, 'faceWrite', 'SE\n'),
  e(50, 'faceWrite', 'SX\n?'),
  e(100, 'faceWrite', 'T\nT3T9\nTx12\n'),
  e(200, 'faceWrite', 'M 12\n'),
  e(210, 'faceWrite', 'M-12\n'),
  e(220, 'faceWrite', 'M1x3'),
  e(230, 'faceWrite', 'Mabc'),
  e(240, 'faceWrite', 'M042\n'),
  e(400, 'faceWrite', 'SS\n'),
  ...[5, 30, 90, 140, 200, 250, 255, 180, 60, 0, 3].map((v, i) => e(420 + i * 40, 'faceWrite', `M${String(v).padStart(3, '0')}\n`)),
  e(900, 'faceWrite', 'ST\n'),
  e(1400, 'faceWrite', 'SF\n'),
  e(1800, 'faceWrite', 'SL\n'),
  e(2200, 'faceWrite', '\u0000ÿégarbage\r\n'),
  e(2300, 'faceWrite', 'S'),
  e(2350, 'faceWrite', 'T\n'),
  e(2500, 'faceWrite', 'R\n'),
  e(2600, 'faceWrite', 'SS\nM255\n'),
  e(2700, 'faceWrite', 'SF\n'),
  e(2750, 'faceWrite', 'SS\n'), // same state while flashing: not ignored
  e(3200, 'faceWrite', 'Sé\n'),
  e(3300, 'faceWrite', 'SI\n'),
  e(3350, 'faceWrite', 'Q'),
];

const rawChest = [
  e(3000, 'chestWrite', 'X0\n'),
  e(3010, 'chestWrite', 'SE\n'),
  e(5000, 'chestWrite', 'SL\n'),
  e(6000, 'chestWrite', 'ST\n'),
  e(7000, 'chestWrite', 'SS\nM200\n'),
  ...[20, 80, 255, 999, 140, 64, 0].map((v, i) => e(7050 + i * 60, 'chestWrite', `M${String(v).padStart(3, '0')}\n`)),
  e(7600, 'chestWrite', 'SI\r'),
  e(8000, 'chestWrite', 'SF\n'),
  e(8500, 'chestWrite', 'B128\n'),
  e(10000, 'chestWrite', 'H1FE\r'),
  e(10500, 'chestWrite', 'h0a5\nH0a5\n'),
  e(11000, 'chestWrite', 'X3\n'),
  e(12500, 'chestWrite', 'X2\n'),
  e(15000, 'chestWrite', 'X1\n'),
  e(16500, 'chestWrite', ' R \n'),
  e(17000, 'chestWrite', 'Q\nM12\nB1234\nH1G0\nX4\né\nSX\n'),
  e(17500, 'chestWrite', 'SI\n'),
  e(19000, 'chestWrite', 'B000\nSE\n'),
];

const scenarios = [
  { name: 'conversation', seed: 1234, host: true, end: 20000, sample_every: 20, events: conversation },
  { name: 'idle', seed: 99, host: true, end: 42000, sample_every: 40, events: idle },
  { name: 'raw_serial', seed: 7, host: false, end: 3600, sample_every: 5, events: rawFace },
  { name: 'raw_serial_nooob', seed: 7, host: false, end: 1500, sample_every: 5, events: rawFace, oob_aliases_mouth: false },
  { name: 'chest', seed: 42, host: false, end: 20000, sample_every: 25, events: rawChest },
  { name: 'chest_synthetic', seed: 42, host: false, end: 20000, sample_every: 50, events: rawChest, layout: 'synthetic' },
];

for (const s of scenarios) {
  const synthetic = s.layout === 'synthetic';
  const sc = {
    name: s.name, seed: s.seed, layout: synthetic ? 'synthetic' : rigName,
    oob_aliases_mouth: s.oob_aliases_mouth ?? true, host: s.host, end: s.end, sample_every: s.sample_every,
    chest_specs: synthetic ? syntheticLayout() : rigLayout, events: s.events,
  };
  const out = run(sc);
  const path = resolve(OUT, `leds_${s.name}.json`);
  writeFileSync(path, JSON.stringify(out));
  console.log(`${path}: ${out.frames.length} frames, ${(JSON.stringify(out).length / 1024).toFixed(0)} KB`);
}
await server.close();
