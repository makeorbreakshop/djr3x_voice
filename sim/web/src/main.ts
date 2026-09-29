/**
 * The 3D sim. It never conducts: the Rust performer does (plan D4).
 *
 * - Connected (the r3x gateway is up): a follower. It renders the runtime's `frames`
 *   (joints + light pixels) and shows its events (captions, sfx, show runs, rig switches).
 * - Standalone (`?offline`, or whenever the gateway is down): the same performer, embedded
 *   as WASM (src/performer.ts) and ticked every animation frame - idle life, LED firmware,
 *   the stage-light desk and actuation all come from it.
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { STILL } from './still'; // first: ?still swaps the clock and RNG before anything reads them
import { PostPipeline } from './post';

import { Rig, RigDoc } from './rig';
import { FaceLeds, OUTPUT_BRIGHTNESS, type RGB } from './leds';
import { ChestLights } from './chestlights';
import { SpeechAudio, TtsAmplitudeAgc } from './audio';
import { accessToken, LiveLink } from './link';
import { ControlPanel } from './panel';
import type { Event as R3xEvent, Frames, RetainedState } from './gateway';
import type { Command } from './generated/Command';
import { limitControls, setupStage } from './booth'; // before any material compiles (patches a chunk)
import { prepareDroidMaterials, tameHighlights } from './look';
import { Ghosts } from './ghost';
import { ServoWhine } from './servowhine';
import { Studio } from './studio/studio';
import {
  INTENTS, PROFILE_JSON, PUPPET_MODES, Performer,
  type CatalogItem, type PerfCmd, type PerfOut, type RunLayer, type SystemMode,
} from './performer';

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

/** Height of the Studio dock over the stage bottom (0 = closed); the droid centres above it. */
let viewInset = 0;
/** Centre the droid in the space left of the control panel (full width on phones). */
function fitView() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const panelEl = document.getElementById('panel');
  const panel = w > 720 && !STILL && panelEl ? panelEl.offsetWidth + 24 : 0;
  document.documentElement.style.setProperty('--panel-space', `${panel}px`);
  camera.aspect = w / h;
  camera.setViewOffset(w, h, panel / 2, viewInset / 2, w, h);
  camera.updateProjectionMatrix();
  post.setSize(w, h);
}
addEventListener('resize', fitView);
fitView();


// ------------------------------------------------------------------ state
const params = new URLSearchParams(location.search);
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const t0 = performance.now();
const clock = () => (performance.now() - t0) / 1000;
const sleep = (s: number) => new Promise((r) => setTimeout(r, s * 1000));
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');

interface Profile {
  actuators: { name: string; joints: Record<string, number>; channel: number; servo: string }[];
  emotes: string[];
}
const PROFILE = JSON.parse(PROFILE_JSON) as Profile;
const SLOTS = PROFILE.emotes;
/** Wired WINDOW_SUBSYSTEMS (panel-major), as the chest service reports them. */
const SUBSYSTEMS: [string, string][] = [
  ['mic / speech-to-text', 'DeepgramDirectMicService'], ['LLM', 'ClaudeService'], ['text-to-speech', 'ElevenLabsService'],
  ['intent routing', 'IntentRouterService'], ['music', 'MusicControllerService'], ['memory', 'MemoryService'],
  ['vision', 'VisionService'], ['face LEDs', 'EyeLightControllerService'], ['show control', 'BrainService'],
];

/** What gets drawn this frame, from whichever conductor is live. */
interface View {
  joints: Record<string, number>;
  eyes: RGB[];
  mouth: RGB[];
  chest: RGB[];
  /** Linear flux per stage group (GROUPS order), or null to leave the desk alone. */
  stage: number[][] | null;
  /** Controller units per channel (standalone only). */
  servo: number[] | null;
}

let rig: Rig | null = null;
let leds: FaceLeds | null = null;
let chestLights: ChestLights | null = null;
let ghosts: Ghosts | null = null;
let performer: Performer | null = null;
let view: View | null = null;
let clips: string[] = [];

/** Gateway up: follow its frames; the embedded performer stands down. */
let connected = false;
let gwState: RetainedState | null = null;
// Standalone bits the performer does not report back.
let mode: SystemMode = 'IDLE';
let speaking = false;
let busy = false;
let djOn = false;
let bpm = 118;
let frozen = false;
let manual = false;
let lookAtCamera = true;
let autonomy = true;

const agc = new TtsAmplitudeAgc();
const audio = new SpeechAudio();
const whine = new ServoWhine();
/** Look-dev URL params pin the booth's own desk in standalone mode (the performer's desk has no rig/cue command). */
const pinDesk = params.has('rig') || params.has('cue');

/** A command for the embedded performer (standalone only; ignored while it stands down). */
function perf(c: PerfCmd) {
  if (performer && !connected) performer.command(c);
}

/** A typed command to the r3x gateway; a rejection shows in the show status line. */
async function send(c: Command) {
  const a = await panel.gw.send(c);
  if (a.status === 'rejected') showStatus(a.reason);
}

const log: { line: string; at: number }[] = [];
let logDirty = true;
function pushLog(line: string) {
  log.push({ line, at: clock() });
  if (log.length > 40) log.shift();
  logDirty = true;
}

let statusTimer = 0;
function showStatus(msg: string) {
  const el = $('sh-status');
  el.textContent = msg;
  clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => (el.textContent = ''), 5000);
}

// ------------------------------------------------------------------ sounds, speech, captions
/** Kit sounds by normalised stem ("airhorn" -> "Air Horn.mp3"); loaded apart from the model. */
const sfxFiles = new Map<string, string>();
fetch('/sfx/index.json')
  .then((r) => (r.ok ? (r.json() as Promise<string[]>) : []))
  .then((list) => {
    clips = list;
    list.forEach((f) => sfxFiles.set(norm(f.replace(/\.[^.]+$/, '')), f));
  })
  .catch(() => { /* no kit sounds on this machine */ });

function playSfx(id: string) {
  const f = sfxFiles.get(norm(id));
  if (!f) return void console.warn(`sfx: no kit sound matches "${id}" (sim/web/public/sfx is built locally)`);
  const el = new Audio(`/sfx/${encodeURIComponent(f)}`);
  el.volume = 0.7;
  void el.play().catch(() => { /* autoplay before a user gesture */ });
}

const capHeard = $('cap-heard');
const capSaid = $('cap-said');
const caption = $('caption');
const updateCaption = () => (caption.hidden = !(capHeard.textContent || capSaid.textContent));

function randomClip() {
  return clips.length ? `/sfx/${clips[Math.floor(Math.random() * clips.length)]}` : null;
}

function setMode(m: SystemMode) {
  mode = m;
  perf({ cmd: 'mode', mode: m });
}

/** One reply: speech_started -> amplitude stream (frame loop) -> speech_ended. */
async function speak(url: string) {
  agc.reset();
  await audio.play(url, () => {
    speaking = true;
    perf({ cmd: 'speech_started' });
  });
  speaking = false;
  perf({ cmd: 'speech_ended' });
}

/** Without the kit's clips: a synthetic syllable envelope instead of audio. */
async function fakeSpeech(seconds: number) {
  agc.reset();
  speaking = true;
  perf({ cmd: 'speech_started' });
  const start = clock();
  while (clock() - start < seconds) await sleep(0.05);
  speaking = false;
  perf({ cmd: 'speech_ended' });
}

async function sayLine() {
  const url = randomClip();
  if (url) await speak(url);
  else await fakeSpeech(2.5);
}

async function converse() {
  if (busy || connected) return;
  busy = true;
  try {
    if (mode !== 'INTERACTIVE') {
      setMode('INTERACTIVE');
      await sleep(0.8);
    }
    perf({ cmd: 'listening_started' });
    await sleep(2.4);
    perf({ cmd: 'listening_stopped' });
    await sleep(0.9 + Math.random() * 0.8);
    await sayLine();
  } finally {
    busy = false;
  }
}

/** A show's `speak` (standalone): a caption plus the fake-amplitude speaking path. */
async function showSpeak(text: string) {
  if (mode === 'IDLE') setMode('INTERACTIVE');
  capSaid.textContent = text;
  updateCaption();
  await fakeSpeech(Math.max(1.2, 0.06 * text.length + 0.4));
  setTimeout(() => {
    if (capSaid.textContent !== text) return;
    capSaid.textContent = '';
    updateCaption();
  }, 1500);
}

// ------------------------------------------------------------------ standalone performer
Performer.create(Number(params.get('seed') ?? Math.floor(Math.random() * 2 ** 31)))
  .then((p) => {
    performer = p;
    const cat = p.catalog();
    if (cat.errors.length) console.warn(`show: ${cat.errors.length} problem(s)\n  ${cat.errors.join('\n  ')}`);
    buildShowUi(cat.items, cat.idle_after_s);
    p.command({ cmd: 'tempo', bpm });
    if (studioLocal) studioPerformer();
    studio.refresh();
  })
  .catch((e) => {
    console.error('performer (wasm) failed to load - run `npm run build:wasm`', e);
    showStatus('Embedded performer failed to load (npm run build:wasm).');
  });

/** Outgoing performer events, standalone: what the real drivers would get. */
function onPerfOut(o: PerfOut) {
  switch (o.type) {
    case 'started':
      showUiDirty = true;
      break;
    case 'ended':
      if (o.reason === 'rejected') showStatus(frozen ? `${o.run.id}: rejected (motion frozen)` : `${o.run.id}: rejected - unknown, or its tier does not allow source "${o.run.source}"`);
      showUiDirty = true;
      break;
    case 'sfx':
      playSfx(o.id);
      break;
    case 'speak':
      void showSpeak(o.text);
      break;
    case 'stage_lights':
      if (o.action.rig) set.lights?.setRig(o.action.rig, o.action.fade || 1);
      break;
    case 'face_line':
      pushLog(`face  ${o.line}`);
      break;
    case 'chest_line':
      pushLog(`chest ${o.line}`);
      break;
    case 'freeze':
      frozen = o.on;
      $('sh-freeze').classList.toggle('on', o.on);
      break;
  }
}

/** The droid's head as the performer wants a look target: (pan, tilt) degrees in the head_pan parent frame. */
const tmpV = new THREE.Vector3();
function aimAt(p: THREE.Vector3): [number, number] | null {
  const pan = rig?.joints.get('head_pan')?.node;
  const tilt = rig?.joints.get('head_tilt')?.node;
  if (!pan?.parent || !tilt) return null;
  const local = pan.parent.worldToLocal(tmpV.copy(p));
  const dy = local.y - (pan.position.y + tilt.position.y);
  const yaw = THREE.MathUtils.radToDeg(Math.atan2(local.x, local.z));
  // Rotation about +X tips the face down, so looking up is negative tilt.
  const pitch = -THREE.MathUtils.radToDeg(Math.atan2(dy, Math.hypot(local.x, local.z)));
  return [yaw, pitch];
}

let hadPad = false;
function pollGamepad() {
  const pad = [...(navigator.getGamepads?.() ?? [])].find((g) => g && g.mapping === 'standard');
  if (pad) {
    perf({ cmd: 'pad', axes: [...pad.axes], buttons: pad.buttons.map((b) => [b.pressed, b.value] as [boolean, number]) });
  } else if (hadPad) {
    // The performer has no "pad gone": park it at neutral.
    perf({ cmd: 'pad', axes: [0, 0, 0, 0], buttons: [] });
  }
  if (!!pad !== hadPad) {
    hadPad = !!pad;
    $('pp-pad').hidden = hadPad;
  }
}

function tickPerformer(t: number) {
  if (!performer || (connected && !studioLocal)) return studio.tick(null);
  if (speaking || audio.micOn) {
    let a = 0;
    if (audio.active) a = agc.next(audio.rmsInt16());
    else if (speaking) {
      const syl = Math.max(0, Math.sin(t * 9.5) * Math.sin(t * 2.3 + 1));
      a = agc.next(1200 + syl * 9000 + Math.random() * 800);
    }
    perf({ cmd: 'amplitude', value: a });
  }
  if (lookAtCamera && !studio.active) {
    const pt = aimAt(camera.position);
    if (pt) perf({ cmd: 'look', pan_tilt: pt });
  }
  pollGamepad();
  const f = performer.tick(t);
  for (const o of performer.events()) onPerfOut(o);
  studio.tick(f);
  view = { joints: f.joints, eyes: f.eyes, mouth: f.mouth, chest: f.chest, stage: pinDesk ? null : f.stage, servo: f.servo.targets };
}

// ------------------------------------------------------------------ gateway follower
function onFrames(f: Frames) {
  if (studioLocal) return; // Studio previews on the embedded performer
  const px = (k: string) => f.lights[k] ?? [];
  view = {
    joints: f.joints, eyes: px('eyes'), mouth: px('mouth'), chest: px('chest'),
    stage: f.lights.stage ? f.lights.stage.map((c) => c.map((v) => v / 255)) : null, servo: null,
  };
}

function onGatewayEvent(e: R3xEvent) {
  switch (e.domain) {
    case 'conversation':
      switch (e.type) {
        case 'listening_started':
          capHeard.textContent = '';
          capSaid.textContent = '';
          break;
        case 'transcript':
          capHeard.textContent = e.text;
          break;
        case 'listening_stopped':
          if (e.transcript) capHeard.textContent = e.transcript;
          break;
        case 'reply_delta':
          capSaid.textContent += e.text;
          break;
        case 'reply':
          capSaid.textContent = e.text;
          break;
      }
      updateCaption();
      break;
    case 'perf':
      switch (e.type) {
        case 'sfx':
          playSfx(e.id);
          break;
        case 'lights':
          if (e.rig) set.lights?.setRig(e.rig, e.fade || 1);
          break;
        case 'started':
        case 'ended':
          if (e.type === 'ended' && e.reason === 'rejected') showStatus(`${e.id}: rejected (tier) from ${e.source}`);
          showUiDirty = true;
          break;
      }
      break;
    case 'music':
      if (e.type === 'track_started' && e.track.bpm) setBpm(e.track.bpm);
      break;
  }
}

function onGatewayState(s: RetainedState) {
  gwState = s;
  frozen = s.stage.frozen;
  $('sh-freeze').classList.toggle('on', frozen);
  $('btn-dj').classList.toggle('on', s.dj.active);
  $<HTMLInputElement>('sh-idle').checked = s.stage.autonomy;
  stMode.textContent = s.engagement.engagement.toUpperCase();
  studio.setActive(s.stage.mode === 'studio');
  ghosts?.apply(s.stage.outputs);
  showUiDirty = true;
}

function setConnected(on: boolean) {
  if (on === connected) return;
  if (on) {
    // The runtime conducts from now on: the embedded performer stands down.
    performer?.command({ cmd: 'stop', all: true });
    performer?.command({ cmd: 'autonomy', on: false });
    if (speaking) performer?.command({ cmd: 'speech_ended' });
    audio.stop();
    speaking = false;
  }
  connected = on;
  if (!on) {
    gwState = null;
    ghosts?.apply(null);
    perf({ cmd: 'autonomy', on: autonomy });
    $<HTMLInputElement>('sh-idle').checked = autonomy;
    $('btn-dj').classList.toggle('on', djOn);
    frozen = false;
    $('sh-freeze').classList.remove('on');
    stMode.textContent = mode;
  }
  document.querySelectorAll<HTMLElement>('[data-offline] :is(button, input, select)').forEach((el) => ((el as HTMLButtonElement).disabled = on));
  showUiDirty = true;
}

// CantinaOS's SimBridge feed: the panel still reads its log lines; the 3D view no longer does.
const liveEl = $('st-live');
const link = new LiveLink(`ws://${location.hostname || '127.0.0.1'}:8765/?token=${encodeURIComponent(accessToken())}`, {
  onHello: () => {},
  onEvent: () => {},
  onStatus(on) {
    liveEl.textContent = on ? 'LIVE' : 'OFFLINE';
    liveEl.classList.toggle('on', on);
  },
});
const panel = new ControlPanel(link);
panel.gw.subscribe({
  onHello: (h) => {
    setConnected(true);
    onGatewayState(h.state);
    void panel.gw.send({ class: 'telemetry', type: 'frames', enabled: true });
  },
  onStatus: (on) => setConnected(on),
  onState: (s) => onGatewayState(s),
  onEvent: (e) => onGatewayEvent(e),
  onFrames,
});
// ------------------------------------------------------------------ Studio (plan Phase 9)
/** Studio open with the embedded performer as its preview: the 3D view follows it, not the gateway. */
let studioLocal = false;
const studio = new Studio({
  performer: () => performer,
  connected: () => connected,
  send: (c) => panel.gw.send(c),
  studioView(open, local, inset) {
    const was = studioLocal;
    studioLocal = open && local;
    document.body.classList.toggle('studio-open', open);
    viewInset = open ? inset : 0;
    fitView();
    if (open && !was && studioLocal) studioPerformer();
    else if (was && !studioLocal) {
      performer?.command({ cmd: 'alive', breathing: true, saccades: true, gaze_wander: true, speech_bob: true });
      if (!connected) performer?.command({ cmd: 'autonomy', on: autonomy });
    }
  },
});
/** Studio: alive layers and autonomy off; only the clip moves the body. */
function studioPerformer() {
  performer?.command({ cmd: 'stop', all: true });
  performer?.command({ cmd: 'autonomy', on: false });
  performer?.command({ cmd: 'alive', breathing: false, saccades: false, gaze_wander: false, speech_bob: false });
}
// Offline there is no StageManager: the mode switch is local (Studio only; Show/Bench need the runtime).
document.querySelectorAll<HTMLButtonElement>('[data-stage-mode]').forEach((b) => b.addEventListener('click', (e) => {
  if (connected) return;
  e.stopPropagation();
  setLocalStage(b.dataset.stageMode ?? 'show');
}));
function setLocalStage(mode: string) {
  document.querySelectorAll<HTMLButtonElement>('[data-stage-mode]').forEach((x) => x.classList.toggle('on', x.dataset.stageMode === mode));
  studio.setActive(mode === 'studio');
}
if (studio.wantsOpen()) setLocalStage('studio');

// ?offline keeps a tab on the embedded performer even while the runtime is running.
if (!params.has('offline')) link.start();

// ------------------------------------------------------------------ load model
async function load() {
  // Meshes are Draco, textures KTX2 (transcoded to the GPU's own format); both decoders are
  // copied into public/ by scripts/copy-draco.mjs.
  const draco = new DRACOLoader().setDecoderPath('/draco/');
  const ktx2 = new KTX2Loader().setTranscoderPath('/basis/').detectSupport(renderer);
  const loader = new GLTFLoader().setDRACOLoader(draco).setKTX2Loader(ktx2);
  const [gltf, doc] = await Promise.all([
    loader.loadAsync('/model/r3x.glb'),
    fetch('/model/rig.json').then((r) => r.json() as Promise<RigDoc>),
  ]);
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
  leds = new FaceLeds(rig);
  tameHighlights(gltf.scene);
  chestLights = new ChestLights(rig.get('torso_middle').node, doc.chest_lights ?? []);
  ghosts = new Ghosts(rig, PROFILE);
  ghosts.apply(gwState?.stage.outputs ?? null);
  buildJointUi(rig);
  document.getElementById('loading')!.remove();
}

load().catch((e) => {
  console.error(e);
  document.getElementById('loading')!.innerHTML =
    'Model not found. Build it first:<br><code>sim/model/build.sh</code>';
});

// ------------------------------------------------------------------ devtools
// __r3x.show.play('dj_intro', {intensity: 1.3}), __r3x.performer.command({cmd: 'eyes', pattern: 'happy'})
Object.assign(window, { __r3x: {
  get rig() { return rig; }, get performer() { return performer; }, get frames() { return view; },
  get connected() { return connected; }, gw: panel.gw, camera, controls, post,
  show: {
    play: (id: string, p: { intensity?: number; speed?: number } = {}) => playShow(id, p.intensity ?? 1, p.speed ?? 1),
    stop: () => stopShows(),
    running: () => (connected ? gwState?.perf.runs ?? [] : performer?.running() ?? []),
    freeze: (on = true) => setFreeze(on),
    catalog: () => performer?.catalog(),
  },
  puppet: {
    set: (c: Record<string, number>) => Object.entries(c).forEach(([intent, value]) => perf({ cmd: 'puppet', intent, value })),
    slot: (i: number) => emote(i),
    mode: (m: 'idle' | 'engaged' | 'dj') => perf({ cmd: 'puppet_mode', mode: m }),
    record: (on = true) => (on ? performer?.takeStart() : performer?.takeStop()),
    take: () => performer?.takeJsonl(),
  },
} });

// ------------------------------------------------------------------ frame loop
let last = clock();
let prevJoints: Record<string, number> | null = null;
let showUiDirty = true;
let showUiTick = 0;
let updateShowUi = () => {};
const stMode = $('st-mode');

/** Disabled light outputs (connected), dimmed: the real driver would stay dark. */
function dim(px: RGB[], output: string): RGB[] {
  if (gwState?.stage.outputs[output] !== false) return px;
  return px.map((c) => c.map((v) => v * 0.15) as RGB);
}

function frame() {
  requestAnimationFrame(frame);
  const t = clock();
  const dt = Math.min(0.05, t - last);
  last = t;

  tickPerformer(t);
  if (view && rig && leds && chestLights) {
    const values = new Map(Object.entries(view.joints));
    rig.apply(values);
    leds.update(dim(view.eyes, 'eyes'), dim(view.mouth, 'mouth'));
    chestLights.update(dim(view.chest, 'chest'));
    let speed = 0;
    if (prevJoints && dt > 0) for (const [j, v] of values) speed += Math.abs(v - (prevJoints[j] ?? v)) / dt;
    prevJoints = view.joints;
    whine.update(speed, dt);
    updateJointReadout();
    updateServoTable();
  }
  // The venue's light desk: the performer's stage output, or (look-dev pin) its own program.
  if (view?.stage) {
    const off = gwState?.stage.outputs.stage === false;
    set.lights?.setExternal(off ? view.stage.map((c) => c.map((v) => v * 0.15)) : view.stage);
  } else {
    set.lights?.setExternal(null);
  }
  if (showUiDirty || ++showUiTick % 15 === 0) updateShowUi();

  controls.update();
  set.constrain(camera, controls.target);
  post.render();
  drawLeds();
  if (logDirty) drawLog();
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ offline demo UI
$('btn-converse').onclick = () => void converse();
$('btn-line').onclick = async () => {
  if (busy) return;
  busy = true;
  try {
    if (mode === 'IDLE') setMode('INTERACTIVE');
    await sayLine();
  } finally {
    busy = false;
  }
};
$('btn-mic').onclick = async (e) => {
  const btn = e.currentTarget as HTMLButtonElement;
  if (audio.micOn) {
    audio.stop();
    speaking = false;
    perf({ cmd: 'speech_ended' });
    btn.classList.remove('on');
    return;
  }
  try {
    if (mode !== 'INTERACTIVE') setMode('INTERACTIVE');
    agc.reset();
    await audio.startMic();
    perf({ cmd: 'speech_started' });
    btn.classList.add('on');
  } catch (err) {
    console.warn('mic unavailable', err);
  }
};
function setDj(on: boolean) {
  djOn = on;
  $('btn-dj').classList.toggle('on', on);
  if (on && mode === 'IDLE') mode = 'AMBIENT'; // the performer does the same
  perf({ cmd: 'dj', on });
}
$('btn-dj').onclick = () => setDj(!djOn);
/** The tempo: the slider, or the live track's analysed bpm. */
function setBpm(n: number) {
  bpm = n;
  $<HTMLInputElement>('bpm').value = String(Math.round(n)); // the range input clamps its own display
  $('bpm-out').textContent = String(Math.round(n * 10) / 10);
  perf({ cmd: 'tempo', bpm: n });
}
$<HTMLInputElement>('bpm').oninput = (e) => setBpm(Number((e.target as HTMLInputElement).value));

document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach((b) => {
  b.onclick = () => setMode(b.dataset.mode as SystemMode);
});
document.querySelectorAll<HTMLButtonElement>('[data-ev]').forEach((b) => {
  b.onclick = () => {
    const ev = b.dataset.ev!;
    if (ev === 'listen') perf({ cmd: 'listening_started' });
    if (ev === 'stop') perf({ cmd: 'listening_stopped' });
    if (ev === 'speak') { agc.reset(); speaking = true; perf({ cmd: 'speech_started' }); }
    if (ev === 'end') { speaking = false; perf({ cmd: 'speech_ended' }); }
  };
});

$<HTMLInputElement>('manual').onchange = (e) => {
  manual = (e.target as HTMLInputElement).checked;
  if (manual) return;
  if (connected) void send({ class: 'perf', type: 'release', channels: [...jointInputs.keys()] });
  else perf({ cmd: 'jog_release' });
};
$<HTMLInputElement>('look').onchange = (e) => {
  lookAtCamera = (e.target as HTMLInputElement).checked;
  if (!lookAtCamera) perf({ cmd: 'look', pan_tilt: null });
};

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
  if (!f || !f.type.startsWith('audio') || busy || connected) return;
  busy = true;
  try {
    if (mode !== 'INTERACTIVE') setMode('INTERACTIVE');
    await speak(URL.createObjectURL(f));
  } finally {
    busy = false;
  }
});

// Machine status (chest windows): offline stand-ins for the service health CantinaOS reports.
SUBSYSTEMS.forEach(([label, service], i) => {
  const row = document.createElement('label');
  row.className = 'inline sub';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = true;
  cb.onchange = () => perf({ cmd: 'service_status', service, status: cb.checked ? 'running' : 'error', latched: true });
  row.append(cb, label);
  row.title = `Panel ${Math.floor(i / 3) + 1}, window ${(i % 3) + 1}: ${service}`;
  $('subsystems').appendChild(row);
});

// ------------------------------------------------------------------ joint sliders (jog)
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
        if (!manual) {
          manual = true;
          $<HTMLInputElement>('manual').checked = true;
        }
        // A joint name in a perf puppet command jogs that joint directly (runtime).
        if (connected) void send({ class: 'perf', type: 'puppet', channels: { [j.spec.name]: v } });
        else perf({ cmd: 'jog', joint: j.spec.name, value: v });
      };
      row.append(name, input, out);
      container.appendChild(row);
      jointInputs.set(j.spec.name, { input, out });
    }
  }
}

function updateJointReadout() {
  if (!view) return;
  for (const [name, ui] of jointInputs) {
    const v = view.joints[name] ?? 0;
    ui.out.textContent = v.toFixed(0);
    if (!manual && document.activeElement !== ui.input) ui.input.value = String(v);
  }
}

// ------------------------------------------------------------------ servo readout
const servoCells = new Map<number, HTMLElement>();
{
  $('servo-summary').textContent = `${PROFILE.actuators.length} actuators; pulses from the performer's controller frame (standalone).`;
  for (const a of PROFILE.actuators) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${a.channel}</td><td>${a.name}</td><td>${a.servo}</td><td class="us">-</td>`;
    $('servo-body').appendChild(tr);
    servoCells.set(a.channel, tr.querySelector('.us')!);
  }
}
let servoTick = 0;
function updateServoTable() {
  if (++servoTick % 6) return; // ~10 Hz is plenty for a readout
  for (const [ch, cell] of servoCells) cell.textContent = view?.servo ? String(view.servo[ch] ?? '-') : '-';
}

// ------------------------------------------------------------------ status + LED view
const logEl = $('serial-log');
function drawLog() {
  logDirty = false;
  logEl.innerHTML = log.slice(-12).map((e) =>
    `<li><span class="tx">→</span> ${e.at.toFixed(2).padStart(7)}s  ${escapeHtml(e.line)}</li>`,
  ).join('');
}
function escapeHtml(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!);
}

const ledCanvas = $<HTMLCanvasElement>('ledview');
const lctx = ledCanvas.getContext('2d')!;
const BLACK: RGB = [0, 0, 0];
function drawLeds() {
  const W = ledCanvas.width;
  const H = ledCanvas.height;
  lctx.clearRect(0, 0, W, H);
  if (!view) return;
  const { eyes, mouth } = view;
  const k = (OUTPUT_BRIGHTNESS + 1) / 256;
  const css = (c: RGB = BLACK) => {
    // Show emitted light (after global brightness), boosted to read on screen.
    const b = (v: number) => Math.min(255, Math.round(v * k * 1.9));
    return `rgb(${b(c[0])},${b(c[1])},${b(c[2])})`;
  };
  const dot = (x: number, y: number, c: RGB | undefined, r = 7) => {
    lctx.beginPath();
    lctx.arc(x, y, r, 0, Math.PI * 2);
    lctx.fillStyle = css(c);
    lctx.fill();
    lctx.strokeStyle = '#2a2e38';
    lctx.stroke();
  };
  // Viewer's perspective: droid's left eye (LEDs 0-6) on the right.
  const eye = (cx: number, start: number) => {
    dot(cx, 44, eyes[start]);
    for (let i = 1; i < 7; i++) {
      const a = ((i - 1) * Math.PI) / 3;
      dot(cx + Math.sin(a) * 20, 44 - Math.cos(a) * 20, eyes[start + i]);
    }
  };
  eye(W * 0.8, 0);
  eye(W * 0.2, 7);
  const vx = W / 2;
  for (let i = 0; i < 8; i++) {
    const arm = i < 4 ? i : 7 - i;
    const side = i < 4 ? -1 : 1;
    dot(vx + side * (22 - arm * 6), 12 + arm * 22, mouth[i], 5);
  }
  lctx.fillStyle = '#5b6170';
  lctx.font = '10px ui-monospace, Menlo, monospace';
  lctx.fillText('R eye 7-13', 8, H - 6);
  lctx.fillText('mouth 0-7', vx - 24, H - 4);
  lctx.fillText('L eye 0-6', W - 62, H - 6);
}

// ------------------------------------------------------------------ show system UI
/** Play from the UI: through the gateway when connected, else the embedded performer. */
function playShow(id: string, intensity: number, speed: number) {
  if (connected) void send({ class: 'perf', type: 'play', id, intensity, speed });
  else perf({ cmd: 'perform', id, source: 'ui', params: { intensity, speed } });
}
function stopShows() {
  if (connected) void send({ class: 'perf', type: 'stop', target: 'all' });
  else perf({ cmd: 'stop', all: true });
}
function setFreeze(on: boolean) {
  if (connected) {
    void send({ class: 'stage', type: 'freeze', on });
    return;
  }
  frozen = on;
  perf({ cmd: 'freeze', on });
  $('sh-freeze').classList.toggle('on', on);
  showUiDirty = true;
}
function emote(slot: number) {
  if (connected) void send({ class: 'perf', type: 'emote', slot });
  else perf({ cmd: 'emote', slot });
}
function puppetSet(intent: string, value: number) {
  if (connected) void send({ class: 'perf', type: 'puppet', channels: { [intent]: value } });
  else perf({ cmd: 'puppet', intent, value });
}

function buildShowUi(items: CatalogItem[], idleAfter: number | null) {
  const params = () => [Number($<HTMLInputElement>('sh-int').value), Number($<HTMLInputElement>('sh-speed').value)] as const;
  for (const kind of ['sequence', 'cue', 'clip'] as const) {
    const ul = $('sh-list-' + kind);
    const list = items.filter((it) => it.kind === kind);
    $('sh-n-' + kind).textContent = `(${list.length})`;
    for (const it of list) {
      const li = document.createElement('li');
      li.dataset.id = it.id;
      li.title = `${it.description}${it.requires ? ' [extended build]' : ''}${it.tags?.length ? `\ntags: ${it.tags.join(', ')}` : ''}`;
      const b = document.createElement('button');
      b.textContent = '▶';
      b.setAttribute('aria-label', `Play ${it.id}`);
      b.onclick = () => playShow(it.id, ...params());
      const nm = document.createElement('span');
      nm.className = 'nm';
      nm.textContent = it.id;
      const sm = document.createElement('small');
      sm.textContent = it.kind === 'sequence' ? `${it.clock === 'beat' ? 'beat' : 'time'}${it.loop ? ' loop' : ''}` : it.kind === 'clip' ? `${it.duration}s` : '';
      nm.appendChild(sm);
      const tier = document.createElement('i');
      tier.className = it.tier;
      tier.textContent = it.tier;
      li.append(b, nm, tier);
      ul.appendChild(li);
    }
  }
  $('sh-idle-label').textContent = `Idle policy (after ${idleAfter ?? '-'} s quiet)`;
  const loops = items.filter((q) => q.kind === 'sequence' && q.loop);
  for (const [sel, activity] of [['sh-bg-idle', 'idle'], ['sh-bg-dj', 'dj']] as const) {
    const el = $<HTMLSelectElement>(sel);
    el.add(new Option('none', ''));
    for (const q of loops) el.add(new Option(q.id, q.id));
    el.onchange = () => perf({ cmd: 'background', activity, id: el.value || null });
  }
  SLOTS.forEach((id, i) => {
    const b = document.createElement('button');
    b.textContent = `${i + 1} ${id}`;
    b.title = items.find((it) => it.id === id)?.description ?? id;
    b.onclick = () => emote(i);
    $('pp-slots').appendChild(b);
  });
}

{
  const out = (id: string, v: string) => ($(id).textContent = Number(v).toFixed(2));
  $<HTMLInputElement>('sh-int').oninput = (e) => out('sh-int-out', (e.target as HTMLInputElement).value);
  $<HTMLInputElement>('sh-speed').oninput = (e) => out('sh-speed-out', (e.target as HTMLInputElement).value);
  $('sh-stop').onclick = () => stopShows();
  $('sh-freeze').onclick = () => setFreeze(!frozen);
  $<HTMLInputElement>('sh-idle').onchange = (e) => {
    const on = (e.target as HTMLInputElement).checked;
    if (connected) return void send({ class: 'stage', type: 'set_autonomy', enabled: on });
    autonomy = on;
    perf({ cmd: 'autonomy', on });
  };

  // Puppeteer: one slider per continuous intent, the modes, the emote slots.
  const box = $('pp-intents');
  const sliders = new Map<string, HTMLInputElement>();
  for (const k of INTENTS) {
    const name = document.createElement('span');
    name.textContent = k;
    const input = document.createElement('input');
    input.type = 'range';
    input.min = '-1';
    input.max = '1';
    input.step = '0.01';
    input.value = '0';
    const o = document.createElement('output');
    o.textContent = '0.00';
    input.oninput = () => {
      o.textContent = Number(input.value).toFixed(2);
      puppetSet(k, Number(input.value));
    };
    box.append(name, input, o);
    sliders.set(k, input);
  }
  $('pp-center').onclick = () => {
    if (connected) void send({ class: 'perf', type: 'release', channels: [...INTENTS] });
    else perf({ cmd: 'puppet_release' });
    sliders.forEach((s) => {
      s.value = '0';
      s.nextElementSibling!.textContent = '0.00';
    });
  };
  for (const m of PUPPET_MODES) {
    const b = document.createElement('button');
    b.textContent = m;
    b.dataset.pmode = m;
    b.onclick = () => {
      perf({ cmd: 'puppet_mode', mode: m });
      document.querySelectorAll<HTMLButtonElement>('[data-pmode]').forEach((x) => x.classList.toggle('on', x === b));
    };
    $('pp-modes').appendChild(b);
  }
  $('take-rec').onclick = () => {
    if (!performer) return;
    if (performer.takeInfo().recording) performer.takeStop();
    else performer.takeStart();
    const on = performer.takeInfo().recording;
    $('take-rec').classList.toggle('on', on);
    $('take-rec').textContent = on ? 'Stop take' : 'Record take';
  };
  $('take-dl').onclick = () => {
    if (!performer) return;
    const blob = new Blob([performer.takeJsonl()], { type: 'application/x-ndjson' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `r3x-take-${new Date().toISOString().replace(/[:.]/g, '-')}.jsonl`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $<HTMLInputElement>('whine').onchange = (e) => whine.setEnabled((e.target as HTMLInputElement).checked);

  // Refreshed ~4x a second (and on every show event) from the frame loop.
  updateShowUi = () => {
    showUiDirty = false;
    const running: { id: string; layer: string; source: string }[] = connected ? gwState?.perf.runs ?? [] : performer?.running() ?? [];
    const ids = new Set(running.map((r) => r.id));
    for (const layer of ['show', 'gesture', 'background'] satisfies RunLayer[]) {
      const r = running.find((x) => x.layer === layer);
      const el = $('sh-' + layer);
      el.textContent = r ? `${r.id} (${r.source})` : '-';
      el.classList.toggle('on', !!r);
    }
    document.querySelectorAll<HTMLLIElement>('.show-list li').forEach((li) => li.classList.toggle('running', ids.has(li.dataset.id!)));
    const take = performer?.takeInfo();
    $('take-info').textContent = !take ? '' : take.recording ? `Recording: ${take.seconds.toFixed(1)} s, ${take.samples} samples @ 50 Hz` : take.samples ? `Last take: ${take.seconds.toFixed(1)} s` : '';
    if (!connected) stMode.textContent = mode;
  };
}
