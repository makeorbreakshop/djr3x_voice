import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { STILL } from './still'; // first: ?still swaps the clock and RNG before anything reads them
import { PostPipeline } from './post';

import { RexFaceFirmware, RGB, OUTPUT_BRIGHTNESS } from './firmware';
import { CantinaHostEmulator, DualHost, SystemMode, TtsAmplitudeAgc } from './host';
import { Rig, RigDoc } from './rig';
import { FaceLeds } from './leds';
import { Activity, Performer } from './behavior';
import { SpeechAudio } from './audio';
import { LiveEvent, LiveLink } from './link';
import { ChestFirmware, ChestHost, ChestLights, WINDOW_SUBSYSTEMS } from './chest';
import { Actuation, DEFAULT_PROFILE, PROFILES } from './actuation/pipeline';
import { MaestroScript, MaestroScriptError } from './actuation/maestro';
import { limitControls, setupStage } from './booth'; // before any material compiles (patches a chunk)
import { prepareDroidMaterials, tameHighlights } from './look';

// ------------------------------------------------------------------ renderer / scene
// The droid's look (per-class materials, weathering) lives in look.ts; the booth set and
// its lights in booth.ts; tone mapping and the rest of the image in post.ts.
const stage = document.getElementById('stage')!;
// No MSAA: the canvas only ever receives full-screen post quads; SMAA does the edges and
// post.ts owns the pixel ratio (dynamic resolution).
const renderer = new THREE.WebGLRenderer({ antialias: false });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap; // PCFSoft was removed in r18x; soft edges via shadow.radius
stage.appendChild(renderer.domElement);

const scene = new THREE.Scene();
// The set - Oga's Cantina DJ booth, its lights, baked bounce and reflections - is booth.ts.
// ?booth=0 swaps it for a clean turntable stage.
const set = setupStage(renderer, scene, new URLSearchParams(location.search).get('booth') !== '0');

const camera = new THREE.PerspectiveCamera(35, window.innerWidth / window.innerHeight, 0.02, 30);
camera.position.copy(set.cams.full[0]);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(set.cams.full[1]);
controls.enableDamping = true;
limitControls(controls, set.booth);

// AO, bloom (LEDs only), tone mapping, SMAA, grade, film - and the render scale: post.ts.
const post = new PostPipeline(renderer, scene, camera);

/** Centre the droid in the space left of the control panel (full width on phones). */
function fitView() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const panel = w > 720 && !STILL ? 364 : 0;
  camera.aspect = w / h;
  camera.setViewOffset(w, h, panel / 2, 0, w, h);
  camera.updateProjectionMatrix();
  post.setSize(w, h);
}
addEventListener('resize', fitView);
fitView();

// ------------------------------------------------------------------ simulation core
const t0 = performance.now();
const fw = new RexFaceFirmware();
const log: { dir: 'tx' | 'rx'; line: string; at: number }[] = [];
// Two boards, one event stream: the face (EyeLightControllerService port) and the chest
// (ChestLightControllerService port). Live, the chest takes the real service's commands.
let chestFw: ChestFirmware | null = null;
let chestLights: ChestLights | null = null;
const host = new DualHost(
  new CantinaHostEmulator(fw, (dir, line, at) => pushLog(dir, line, at)),
  new ChestHost((cmd) => chestFw?.write(cmd + '\n')),
);
const agc = new TtsAmplitudeAgc();
const audio = new SpeechAudio();

let rig: Rig | null = null;
let leds: FaceLeds | null = null;
let performer: Performer | null = null;
let actuation: Actuation | null = null;
let rigDoc: RigDoc | null = null;
let script: MaestroScript | null = null;
/** Joints with no servo in the active profile: hand-posed, like the static kit. */
const posed = new Map<string, number>();
let amplitude = 0;
let speaking = false;
let busy = false;
let clips: string[] = [];

function pushLog(dir: 'tx' | 'rx', line: string, at: number) {
  log.push({ dir, line, at });
  if (log.length > 40) log.shift();
  logDirty = true;
}

function setActivity(a: Activity) {
  performer?.setActivity(a, clock());
}

const clock = () => (performance.now() - t0) / 1000;
const sleep = (s: number) => new Promise((r) => setTimeout(r, s * 1000));

// ------------------------------------------------------------------ load model
async function load() {
  // Meshes are Draco, textures KTX2 (transcoded to the GPU's own format); both decoders are
  // copied into public/ by scripts/copy-draco.mjs.
  const draco = new DRACOLoader().setDecoderPath('/draco/');
  const ktx2 = new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer);
  const loader = new GLTFLoader().setDRACOLoader(draco).setKTX2Loader(ktx2);
  const [gltf, doc, clipList] = await Promise.all([
    loader.loadAsync('/model/r3x.glb'),
    fetch('/model/rig.json').then((r) => r.json() as Promise<RigDoc>),
    fetch('/sfx/index.json').then((r) => (r.ok ? r.json() : []), () => []),
  ]);
  clips = clipList as string[];
  ktx2.dispose(); // frees the transcoder workers; the textures are on the GPU

  // The paint (baked PBR textures) comes with the GLB's materials - see look.ts.
  gltf.scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    m.castShadow = true;
    m.receiveShadow = true;
  });
  prepareDroidMaterials(gltf.scene, renderer);
  scene.add(gltf.scene);

  rig = new Rig(gltf.scene, doc);
  rigDoc = doc;
  leds = new FaceLeds(rig);
  tameHighlights(gltf.scene);
  chestFw = new ChestFirmware(doc.chest_lights ?? []);
  host.chest.boot(fw.now); // the boot sweep, as when CantinaOS starts
  chestLights = new ChestLights(rig.get('torso_middle').node, doc.chest_lights ?? []);
  setProfile(currentProfile);
  performer = new Performer(rig, (joint, value) => actuation?.command(joint, value));
  buildJointUi(rig);
  loadShows();
  document.getElementById('loading')!.remove();
}

load().catch((e) => {
  console.error(e);
  document.getElementById('loading')!.innerHTML =
    'Model not found. Build it first:<br><code>sim/model/build.sh</code>';
});

// ------------------------------------------------------------------ show scripts
function randomClip() {
  return clips.length ? `/sfx/${clips[Math.floor(Math.random() * clips.length)]}` : null;
}

/** One reply: SPEECH_GENERATION_STARTED -> amplitude stream -> SPEECH_GENERATION_COMPLETE. */
async function speak(url: string) {
  agc.reset();
  await audio.play(url, () => {
    speaking = true;
    host.speechStarted();
    setActivity('speaking');
  });
  speaking = false;
  amplitude = 0;
  host.speechEnded();
  setActivity(host.mode === 'IDLE' ? 'idle' : 'engaged');
}

async function converse() {
  if (busy) return;
  busy = true;
  try {
    if (host.mode !== 'INTERACTIVE') {
      host.setMode('INTERACTIVE');
      setActivity('engaged');
      await sleep(0.8);
    }
    host.listeningStarted();
    setActivity('listening');
    await sleep(2.4);
    host.listeningStopped();
    setActivity('thinking');
    await sleep(0.9 + Math.random() * 0.8);
    const url = randomClip();
    if (url) await speak(url);
    else await fakeSpeech(2.5);
  } finally {
    busy = false;
  }
}

/** Without the kit's clips: a synthetic syllable envelope instead of audio. */
async function fakeSpeech(seconds: number) {
  host.speechStarted();
  setActivity('speaking');
  speaking = true;
  const start = clock();
  while (clock() - start < seconds) await sleep(0.05);
  speaking = false;
  amplitude = 0;
  host.speechEnded();
  setActivity('engaged');
}

// ------------------------------------------------------------------ live link
// With CantinaOS running, SimBridgeService streams the real bus events here and they go
// through the same host emulator -> firmware path as the demo, so the LEDs show what the
// Arduino would. Motion follows the same events.
let musicPlaying = false;
let liveSpeaking = false;
let liveDj = false;
const capHeard = document.getElementById('cap-heard')!;
const capSaid = document.getElementById('cap-said')!;
const caption = document.getElementById('caption')!;

function liveMode(raw: unknown): SystemMode {
  const m = String(raw ?? '').toUpperCase();
  return m === 'AMBIENT' || m === 'INTERACTIVE' ? m : 'IDLE';
}

function restingActivity(): Activity {
  if (musicPlaying || djOn || liveDj) return 'dj';
  return host.mode === 'IDLE' ? 'idle' : 'engaged';
}

function onLiveEvent(ev: LiveEvent) {
  const d = ev.data;
  switch (ev.topic) {
    case 'system.mode.change':
      host.setMode(liveMode(d.new_mode));
      if (!liveSpeaking) setActivity(restingActivity());
      break;
    case 'voice.listening.started':
      host.listeningStarted();
      setActivity('listening');
      capHeard.textContent = '';
      capSaid.textContent = '';
      break;
    case 'voice.listening.stopped':
    case 'voice.processing.started':
    case 'mouse.recording.stopped':
      host.listeningStopped();
      if (host.mode === 'INTERACTIVE') setActivity('thinking');
      if (typeof d.transcript === 'string' && d.transcript) capHeard.textContent = d.transcript;
      break;
    case 'transcription.interim':
      if (typeof d.text === 'string') capHeard.textContent = d.text;
      break;
    case 'llm.response.chunk':
      host.llmChunk();
      break;
    case 'llm.response':
      if (typeof d.text === 'string' && d.text) capSaid.textContent = d.text;
      break;
    case 'speech.generation.started':
    case 'speech.synthesis.started':
      if (!liveSpeaking) agc.reset();
      liveSpeaking = true;
      host.speechStarted();
      setActivity('speaking');
      break;
    case 'speech.synthesis.amplitude':
      amplitude = Number(d.amplitude) || 0;
      host.amplitude(amplitude);
      break;
    case 'speech.generation.complete':
    case 'speech.synthesis.ended':
      if (!liveSpeaking) break;
      liveSpeaking = false;
      amplitude = 0;
      host.speechEnded();
      setActivity(restingActivity());
      break;
    case 'chest.command':
      // The real ChestLightControllerService is running: mirror its exact commands.
      if (typeof d.command === 'string') {
        host.chest.muted = true;
        chestFw?.write(d.command + '\n');
      }
      break;
    case 'music.playback.started':
      musicPlaying = true;
      host.chest.music(true, bpm);
      if (!liveSpeaking) setActivity('dj');
      break;
    case 'music.playback.stopped':
      musicPlaying = false;
      host.chest.music(false);
      if (!liveSpeaking) setActivity(restingActivity());
      break;
    case 'dj.mode.changed':
      liveDj = Boolean(d.is_active);
      if (!liveSpeaking) setActivity(restingActivity());
      break;
  }
  caption.hidden = !(capHeard.textContent || capSaid.textContent);
}

const liveEl = document.getElementById('st-live')!;
const link = new LiveLink(`ws://${location.hostname || '127.0.0.1'}:8765`, {
  onHello(mode) {
    host.setMode(liveMode(mode));
    setActivity(restingActivity());
  },
  onEvent: onLiveEvent,
  onStatus(on) {
    liveEl.textContent = on ? 'LIVE' : 'OFFLINE';
    liveEl.classList.toggle('on', on);
    if (!on) {
      liveSpeaking = false;
      musicPlaying = false;
      liveDj = false;
      host.chest.muted = false; // back to the offline port of the chest service
      host.chest.resync();
    }
  },
});
// ?offline keeps a tab on the built-in demo even while CantinaOS is running.
if (!new URLSearchParams(location.search).has('offline')) link.start();

// Devtools: __r3x.fw.write('ST\n'), __r3x.actuation.command('head_pan', 40)
Object.assign(window, { __r3x: { fw, host, log, get rig() { return rig; }, get actuation() { return actuation; }, get chest() { return chestFw; }, camera, controls, post } });

// ------------------------------------------------------------------ frame loop
let last = clock();
let logDirty = true;
function frame() {
  requestAnimationFrame(frame);
  const t = clock();
  const dt = Math.min(0.05, t - last);
  last = t;

  // Speech amplitude, as ElevenLabsService computes it.
  if (speaking || audio.micOn) {
    if (audio.active) {
      amplitude = agc.next(audio.rmsInt16());
    } else if (speaking) {
      const syl = Math.max(0, Math.sin(t * 9.5) * Math.sin(t * 2.3 + 1));
      amplitude = agc.next(1200 + syl * 9000 + Math.random() * 800);
    }
    host.amplitude(amplitude);
  }

  // Firmware runs on its own simulated millis(); the host's 60 Hz loop rides on it.
  host.tick(fw.now);
  fw.advanceTo((performance.now() - t0));
  for (const line of fw.readLines()) pushLog('rx', line, fw.now);

  if (rig && performer && leds && actuation) {
    if (script) {
      script.run(actuation.simTime * 1000);
      if (!script.running) stopScript();
    } else if (!manual) {
      performer.update(t, dt, {
        amplitude,
        lookTarget: lookAtCamera ? camera.position : null,
        bpm,
      });
    }
    actuation.update(dt);
    const values = actuation.jointValues();
    for (const j of rig.joints.keys()) if (!values.has(j)) values.set(j, posed.get(j) ?? 0);
    rig.apply(values);
    leds.update(fw);
    if (chestFw && chestLights) {
      chestFw.update(fw.now);
      chestLights.update(chestFw.pixels);
    }
    updateJointReadout();
    updateServoTable();
  }

  // Stage lights: the venue's separate light desk, following the show state.
  set.lights?.setMode(liveSpeaking || speaking ? 'speaking' : djOn || liveDj ? 'dj' : musicPlaying ? 'music' : 'idle');
  set.lights?.setBpm(bpm);
  set.lights?.update(dt);

  controls.update();
  set.constrain(camera, controls.target);
  post.render();
  drawLeds();
  updateStatus();
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ UI
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
let manual = false;
let lookAtCamera = true;
let bpm = 118;
let djOn = false;

$('btn-converse').onclick = () => void converse();
$('btn-line').onclick = async () => {
  if (busy) return;
  busy = true;
  try {
    if (host.mode === 'IDLE') host.setMode('INTERACTIVE');
    const url = randomClip();
    if (url) await speak(url);
    else await fakeSpeech(2.5);
  } finally {
    busy = false;
  }
};
$('btn-mic').onclick = async (e) => {
  const btn = e.currentTarget as HTMLButtonElement;
  if (audio.micOn) {
    audio.stop();
    speaking = false;
    amplitude = 0;
    host.speechEnded();
    setActivity('engaged');
    btn.classList.remove('on');
    return;
  }
  try {
    if (host.mode !== 'INTERACTIVE') host.setMode('INTERACTIVE');
    agc.reset();
    await audio.startMic();
    host.speechStarted();
    setActivity('speaking');
    btn.classList.add('on');
  } catch (err) {
    console.warn('mic unavailable', err);
  }
};
$('btn-dj').onclick = (e) => {
  djOn = !djOn;
  (e.currentTarget as HTMLElement).classList.toggle('on', djOn);
  host.chest.dj(djOn, bpm);
  if (djOn) {
    if (host.mode === 'IDLE') host.setMode('AMBIENT');
    setActivity('dj');
  } else {
    setActivity(host.mode === 'IDLE' ? 'idle' : 'engaged');
  }
};
$<HTMLInputElement>('bpm').oninput = (e) => {
  bpm = Number((e.target as HTMLInputElement).value);
  $('bpm-out').textContent = String(bpm);
  if (djOn) host.chest.dj(true, bpm);
  else if (musicPlaying) host.chest.music(true, bpm);
};

document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach((b) => {
  b.onclick = () => {
    const m = b.dataset.mode as SystemMode;
    host.setMode(m);
    if (!djOn) setActivity(m === 'IDLE' ? 'idle' : 'engaged');
  };
});
document.querySelectorAll<HTMLButtonElement>('[data-ev]').forEach((b) => {
  b.onclick = () => {
    const ev = b.dataset.ev!;
    if (ev === 'listen') { host.listeningStarted(); setActivity('listening'); }
    if (ev === 'stop') { host.listeningStopped(); setActivity('thinking'); }
    if (ev === 'speak') { host.speechStarted(); setActivity('speaking'); agc.reset(); speaking = true; }
    if (ev === 'end') { speaking = false; amplitude = 0; host.speechEnded(); setActivity('engaged'); }
  };
});

const serialIn = $<HTMLInputElement>('serial-in');
const sendSerial = () => {
  const v = serialIn.value.trim();
  if (!v) return;
  fw.write(v + '\n');
  chestFw?.write(v + '\n');
  pushLog('tx', v, fw.now);
  serialIn.value = '';
};
$('serial-send').onclick = sendSerial;
serialIn.onkeydown = (e) => { if (e.key === 'Enter') sendSerial(); };

$<HTMLInputElement>('manual').onchange = (e) => { manual = (e.target as HTMLInputElement).checked; };
$<HTMLInputElement>('look').onchange = (e) => { lookAtCamera = (e.target as HTMLInputElement).checked; };

const pivots: THREE.Object3D[] = [];
$<HTMLInputElement>('axes').onchange = (e) => {
  const on = (e.target as HTMLInputElement).checked;
  if (on && rig && !pivots.length) {
    for (const j of rig.joints.values()) {
      const arrow = new THREE.ArrowHelper(j.axis, new THREE.Vector3(), 0.06, 0xffcc33, 0.015, 0.01);
      (arrow.line.material as THREE.Material).depthTest = false;
      (arrow.cone.material as THREE.Material).depthTest = false;
      arrow.renderOrder = 10;
      j.node.add(arrow);
      pivots.push(arrow);
    }
  }
  pivots.forEach((p) => (p.visible = on));
};

// Presets come from the set (booth.ts): in the booth they frame the droid through the arch.
// The chest preset looks at the logic panels on the droid's right-front quarter.
const CAMS = set.cams;
document.querySelectorAll<HTMLButtonElement>('[data-cam]').forEach((b) => {
  b.onclick = () => {
    const [p, tgt] = CAMS[b.dataset.cam!];
    camera.position.copy(p);
    controls.target.copy(tgt);
  };
});

// Drop an audio file to make R3X say it.
addEventListener('dragover', (e) => { e.preventDefault(); stage.classList.add('dragging'); });
addEventListener('dragleave', () => stage.classList.remove('dragging'));
addEventListener('drop', async (e) => {
  e.preventDefault();
  stage.classList.remove('dragging');
  const f = e.dataTransfer?.files?.[0];
  if (!f || !f.type.startsWith('audio') || busy) return;
  busy = true;
  try {
    if (host.mode !== 'INTERACTIVE') host.setMode('INTERACTIVE');
    await speak(URL.createObjectURL(f));
  } finally {
    busy = false;
  }
});

// ------------------------------------------------------------------ joint sliders
const jointInputs = new Map<string, { input: HTMLInputElement; out: HTMLOutputElement }>();
function buildJointUi(r: Rig) {
  const container = $('joints');
  const groups: [string, RegExp][] = [
    ['Head', /^head|^visor/], ['Torso rings', /^torso/], ['Hero arm', /^hero/],
    ['Throttle arm', /^throttle/], ['Poker arm', /^poker/],
  ];
  for (const [label, re] of groups) {
    const g = document.createElement('div');
    g.className = 'joint group';
    g.textContent = label;
    container.appendChild(g);
    for (const j of r.joints.values()) {
      if (!re.test(j.spec.name)) continue;
      const row = document.createElement('label');
      row.className = 'joint';
      row.title = j.spec.note || j.spec.name;
      const name = document.createElement('span');
      name.textContent = j.spec.name.replace(/^(head|torso|hero|throttle|poker)_/, '');
      const input = document.createElement('input');
      input.type = 'range';
      input.min = String(j.spec.min);
      input.max = String(j.spec.max);
      input.step = '0.5';
      input.value = '0';
      const out = document.createElement('output');
      out.textContent = '0';
      input.oninput = () => {
        const v = Number(input.value);
        if (actuation?.byJoint.has(j.spec.name)) {
          if (!manual) {
            manual = true;
            $<HTMLInputElement>('manual').checked = true;
          }
          actuation.command(j.spec.name, v);
        } else {
          posed.set(j.spec.name, v); // no servo: pose by hand, instantly
        }
      };
      const tag = document.createElement('i');
      row.append(name, input, out, tag);
      container.appendChild(row);
      jointInputs.set(j.spec.name, { input, out });
    }
  }
}

function updateJointReadout() {
  if (!rig || !actuation) return;
  for (const [name, ui] of jointInputs) {
    const j = rig.get(name);
    ui.out.textContent = j.value.toFixed(0);
    const ch = actuation.byJoint.get(name);
    const row = ui.input.parentElement!;
    row.classList.toggle('posable', !ch);
    (row.lastElementChild as HTMLElement).textContent = ch ? `ch${ch.cfg.ch}` : 'pose';
    if (ch && !manual && document.activeElement !== ui.input) ui.input.value = String(ch.follower.target);
  }
}

// ------------------------------------------------------------------ actuation UI
let currentProfile = DEFAULT_PROFILE;
function setProfile(name: string) {
  if (!rigDoc) return;
  stopScript();
  currentProfile = name;
  actuation = new Actuation(rigDoc.joints, rigDoc.dynamics ?? {}, name);
  actuation.plantEnabled = $<HTMLInputElement>('plant').checked;
  buildServoTable();
}

const profileSel = $<HTMLSelectElement>('profile');
for (const [k, label] of Object.entries(PROFILES)) profileSel.add(new Option(label, k, k === DEFAULT_PROFILE, k === DEFAULT_PROFILE));
profileSel.onchange = () => setProfile(profileSel.value);
$<HTMLInputElement>('plant').onchange = (e) => {
  if (actuation) actuation.plantEnabled = (e.target as HTMLInputElement).checked;
};

const servoRows = new Map<number, HTMLTableRowElement>();
function buildServoTable() {
  const body = $('servo-body');
  body.innerHTML = '';
  servoRows.clear();
  if (!actuation) return;
  $('servo-summary').textContent =
    `${actuation.channels.length} servos, ${{ custom: 'custom controller', maestro: 'Maestro', pca9685: 'PCA9685' }[actuation.controller.type]} ` +
    `@ ${actuation.frequencyHz.toFixed(1)} Hz, ${actuation.usPerUnit} us resolution`;
  const warnings: string[] = [];
  for (const ch of actuation.channels) {
    const tr = document.createElement('tr');
    const load = Math.round(ch.utilisation * 100);
    tr.title = [ch.cfg.note, ch.cfg.calibration === 'assumed' ? 'Calibration assumed - set centreUs on the bench.' : '', ...ch.warnings]
      .filter(Boolean).join('\n');
    tr.innerHTML = `<td>${ch.cfg.ch}</td><td>${ch.cfg.name}</td><td>${ch.model.label}</td>` +
      `<td class="us"></td><td class="${load > 50 ? 'bad' : load > 30 ? 'meh' : ''}">${load}%</td>`;
    body.appendChild(tr);
    servoRows.set(ch.cfg.ch, tr);
    for (const w of ch.warnings) warnings.push(`ch${ch.cfg.ch} ${ch.cfg.name}: ${w}`);
  }
  const wEl = $('servo-warn');
  wEl.hidden = !warnings.length;
  wEl.textContent = warnings.join(' · ');
}

let servoTick = 0;
function updateServoTable() {
  if (!actuation || ++servoTick % 6) return; // ~10 Hz is plenty for a readout
  for (const ch of actuation.channels) {
    const cell = servoRows.get(ch.cfg.ch)?.querySelector('.us');
    if (cell) cell.textContent = `${ch.us.toFixed(0)}${ch.directUs !== null ? '*' : ''}`;
  }
}

$('export-frames').onclick = () => {
  if (!actuation) return;
  const blob = new Blob([actuation.exportLog()], { type: 'application/x-ndjson' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `r3x-frames-${actuation.profileName}.jsonl`;
  a.click();
  URL.revokeObjectURL(a.href);
};

// ------------------------------------------------------------------ Maestro show scripts
const scriptText = $<HTMLTextAreaElement>('script-text');
const scriptStatus = $('script-status');
async function loadShows() {
  try {
    const names: string[] = await fetch('/shows/index.json').then((r) => (r.ok ? r.json() : []));
    if (names.length && !scriptText.value) {
      scriptText.value = await fetch(`/shows/${encodeURIComponent(names[0])}`).then((r) => r.text());
      scriptStatus.textContent = `Loaded ${names[0]}`;
    }
  } catch {
    /* no shows copied - paste one */
  }
}

function startScript() {
  if (!actuation) return;
  const a = actuation;
  try {
    script = new MaestroScript(scriptText.value, {
      setTarget: (ch, q) => a.setTarget(ch, q),
      setSpeed: (ch, v) => a.setMaestroLimits(ch, v, undefined),
      setAccel: (ch, v) => a.setMaestroLimits(ch, undefined, v),
      getPosition: (ch) => a.byNumber.get(ch)?.target ?? 0,
      anyMoving: () => a.channels.some((c) => c.directUs !== null && Math.abs(c.directUs - c.us) > 0.5),
    });
    scriptStatus.textContent = 'Running on the sim clock - channels marked * are script-driven.';
    $('script-run').textContent = 'Stop';
  } catch (e) {
    scriptStatus.textContent = e instanceof MaestroScriptError ? e.message : String(e);
    script = null;
  }
}

function stopScript() {
  script = null;
  actuation?.releaseAll();
  $('script-run').textContent = 'Run script';
}
$('script-run').onclick = () => (script ? stopScript() : startScript());

// ------------------------------------------------------------------ status + LED view
const stMode = $('st-mode');
const stFw = $('st-fw');
const warn = $('warn-reset');
const logEl = $('serial-log');
function updateStatus() {
  stMode.textContent = host.mode;
  stFw.textContent = fw.flashActive ? 'FLASH' : fw.currentState;
  if (host.droppedMouthResets > 0) {
    warn.hidden = false;
    warn.textContent =
      `${host.droppedMouthResets}x the end-of-speech M000 landed inside the adapter's 10 Hz throttle ` +
      `window and was dropped, so the mouth kept its last amplitude in ENGAGED. Timing-dependent: ` +
      `it only happens when an amplitude update went out <100 ms before speech ended.`;
  }
  if (logDirty) {
    logDirty = false;
    logEl.innerHTML = log.slice(-12).map((e) =>
      `<li><span class="${e.dir}">${e.dir === 'tx' ? '→' : '←'}</span> ${(e.at / 1000).toFixed(2).padStart(7)}s  ${escapeHtml(e.line)}</li>`,
    ).join('');
  }
}
function escapeHtml(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!);
}

const ledCanvas = $<HTMLCanvasElement>('ledview');
const lctx = ledCanvas.getContext('2d')!;
function drawLeds() {
  const W = ledCanvas.width;
  const H = ledCanvas.height;
  lctx.clearRect(0, 0, W, H);
  const k = (OUTPUT_BRIGHTNESS + 1) / 256;
  const css = (c: RGB) => {
    // Show emitted light (after global brightness), boosted to read on screen.
    const b = (v: number) => Math.min(255, Math.round(v * k * 1.9));
    return `rgb(${b(c[0])},${b(c[1])},${b(c[2])})`;
  };
  const dot = (x: number, y: number, c: RGB, r = 7) => {
    lctx.beginPath();
    lctx.arc(x, y, r, 0, Math.PI * 2);
    lctx.fillStyle = css(c);
    lctx.fill();
    lctx.strokeStyle = '#2a2e38';
    lctx.stroke();
  };
  // Viewer's perspective: droid's left eye (LEDs 0-6) on the right.
  const eye = (cx: number, start: number) => {
    dot(cx, 44, fw.eyeLeds[start]);
    for (let i = 1; i < 7; i++) {
      const a = ((i - 1) * Math.PI) / 3;
      dot(cx + Math.sin(a) * 20, 44 - Math.cos(a) * 20, fw.eyeLeds[start + i]);
    }
  };
  eye(W * 0.8, 0);
  eye(W * 0.2, 7);
  const vx = W / 2;
  for (let i = 0; i < 8; i++) {
    const arm = i < 4 ? i : 7 - i;
    const side = i < 4 ? -1 : 1;
    dot(vx + side * (22 - arm * 6), 12 + arm * 22, fw.mouthLeds[i], 5);
  }
  lctx.fillStyle = '#5b6170';
  lctx.font = '10px ui-monospace, Menlo, monospace';
  lctx.fillText('R eye 7-13', 8, 90);
  lctx.fillText('mouth 0-7', vx - 24, 92);
  lctx.fillText('L eye 0-6', W - 62, 90);
}

// ------------------------------------------------------------------ machine status (chest)
// Offline stand-ins for what CantinaOS reports: service health and system state.
{
  const box = document.getElementById('subsystems')!;
  WINDOW_SUBSYSTEMS.forEach(([label, services], i) => {
    const row = document.createElement('label');
    row.className = 'inline sub';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.onchange = () => host.chest.serviceStatus(services[0], cb.checked ? 'running' : 'error');
    const panel = Math.floor(i / 3) + 1;
    row.append(cb, `${label}`);
    row.title = `Panel ${panel}, window ${(i % 3) + 1}: ${services.join(' / ')}`;
    box.appendChild(row);
  });
  document.getElementById('btn-boot')!.onclick = () => host.chest.boot(fw.now);
  document.getElementById('btn-sleep')!.onclick = (e) => {
    host.chest.sleeping = !host.chest.sleeping;
    (e.currentTarget as HTMLElement).classList.toggle('on', host.chest.sleeping);
  };
}
