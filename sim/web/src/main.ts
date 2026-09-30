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

import { Rig, RigDoc, restFromUrl } from './rig';
import { FaceLeds, OUTPUT_BRIGHTNESS, type RGB } from './leds';
import { ChestLights } from './chestlights';
import {
  BoardsView, PACKAGES, PackageLeds, faceSlots, mountElectronics, ownsLights, profileJsonWith, selectedPackageId,
} from './electronics';
import type { ElectronicsPackage } from './generated/ElectronicsPackage';
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
import { SceneLook, type Backdrop } from './scene';
import { Centres, atHome, formatValue } from './centres';
import { mountScenePanel } from './scenepanel';
import { mountPanels } from './layout';
import { BodyRegions } from './regions';
import type { RobotProfile } from './generated/RobotProfile';
import type { GazeSource } from './generated/GazeSource';
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

// Backdrop + work light (scene.ts), over the booth; `?booth=0` keeps the turntable stage.
const sceneLook = set.show && !STILL
  ? new SceneLook(scene, renderer, (on) => set.show!(on), (booth) => limitControls(controls, booth))
  : null;
{
  const bg = document.getElementById('scene-bg') as HTMLSelectElement;
  const work = document.getElementById('scene-work') as HTMLButtonElement;
  const sync = () => {
    if (!sceneLook) return;
    bg.value = sceneLook.choice.backdrop;
    work.setAttribute('aria-pressed', String(sceneLook.choice.workLight));
  };
  document.getElementById('sc-env')!.hidden = !sceneLook;
  (document.querySelector('[data-scene-open="sc-env"]') as HTMLElement).hidden = !sceneLook;
  bg.onchange = () => { sceneLook?.pick({ backdrop: bg.value as Backdrop }); sync(); bg.blur(); };
  work.onclick = () => { sceneLook?.pick({ workLight: !sceneLook.choice.workLight }); sync(); work.blur(); };
  sceneLook?.onUpdate(sync);
  sync();
}

// Centres (centres.ts): on by default in Bench and Studio, off in Show, until the operator picks.
const CENTRES_KEY = 'r3x.centres';
let centresPick: boolean | null = null;
try {
  const v = localStorage.getItem(CENTRES_KEY);
  centresPick = v === null ? null : v === '1';
} catch {
  /* storage blocked */
}
let centresOn = centresPick ?? false;
/** Each joint's pivot arrow (showPivots), built on first use. */
const pivots: THREE.Object3D[] = [];
function setCentres(on: boolean) {
  centresOn = on;
  document.getElementById('scene-centres')!.setAttribute('aria-pressed', String(on));
  centres?.setVisible(on);
  showPivots(on);
}
function centresFollowMode(mode: string) {
  if (centresPick === null) setCentres(mode !== 'show');
}
document.getElementById('scene-centres')!.onclick = (e) => {
  centresPick = !centresOn;
  try {
    localStorage.setItem(CENTRES_KEY, centresPick ? '1' : '0');
  } catch {
    /* storage blocked */
  }
  setCentres(centresPick);
  (e.currentTarget as HTMLElement).blur();
};

// AO, bloom (LEDs only), tone mapping, SMAA, grade, film - and the render scale: post.ts.
const post = new PostPipeline(renderer, scene, camera);
// Input anywhere and the orbit camera moving (or settling) draw at full rate (pacer.ts).
post.pacer.listen();
controls.addEventListener('change', () => post.pacer.interact());

/** Height of the Studio dock over the stage bottom (0 = closed); the droid centres above it. */
let viewInset = 0;
/** Centre the droid in the space between the Scene and R3X panels (full width on phones). */
function fitView() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const side = (id: string) => {
    const el = document.getElementById(id);
    return w > 720 && !STILL && el ? el.offsetWidth + 24 : 0;
  };
  const panel = side('panel');
  const scenePanel = side('scene-panel');
  document.documentElement.style.setProperty('--panel-space', `${panel}px`);
  document.documentElement.style.setProperty('--scene-space', `${scenePanel || 12}px`);
  camera.aspect = w / h;
  camera.setViewOffset(w, h, (panel - scenePanel) / 2, viewInset / 2, w, h);
  // The presets are framed for a visible area at least FIT_ASPECT wide; narrower (both panels
  // open on a small window) widens the lens so he is not cut off behind a panel.
  const seen = (w - panel - scenePanel) / Math.max(1, h - viewInset);
  camera.zoom = Math.min(1, seen / FIT_ASPECT);
  camera.updateProjectionMatrix();
  if (w !== fitSize.w || h !== fitSize.h) post.setSize(w, h);
  fitSize.w = w;
  fitSize.h = h;
}
const FIT_ASPECT = 0.95;
const fitSize = { w: 0, h: 0 };
addEventListener('resize', fitView);
// A panel sliding open or shut re-fits every frame until it settles.
let fitting = 0;
const fitFrame = () => {
  fitView();
  if (fitting) requestAnimationFrame(fitFrame);
};
for (const id of ['panel', 'scene-panel']) {
  const el = document.getElementById(id)!;
  el.addEventListener('transitionrun', (e) => {
    if (e.target !== el || fitting++) return;
    requestAnimationFrame(fitFrame);
  });
  const done = (e: TransitionEvent) => {
    if (e.target !== el) return;
    fitting = Math.max(0, fitting - 1);
    fitView();
  };
  el.addEventListener('transitionend', done);
  el.addEventListener('transitioncancel', done);
}
const panels = mountPanels(fitView);
mountScenePanel(post, () => panels.set('scene', false));
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
const FULL_PROFILE = JSON.parse(PROFILE_JSON) as RobotProfile;
const JOINTS = FULL_PROFILE.joints.map((j) => j.name);
/** The electronics package this page runs offline (electronics.ts); a gateway's profile overrides it. */
const LOCAL_PACKAGE: ElectronicsPackage = PACKAGES[selectedPackageId(FULL_PROFILE.electronics)];
let gwPackage: ElectronicsPackage | null = null;
const activePackage = () => (connected && gwPackage) || LOCAL_PACKAGE;
const HOME: Record<string, number> = Object.fromEntries(JOINTS.map((j) => [j, FULL_PROFILE.home?.[j] ?? 0]));
/** This page's id for the viewport gaze (kept across reloads of this tab). */
const PANEL_ID = (() => {
  const fresh = `panel-${Math.random().toString(36).slice(2, 6)}`;
  try {
    const v = sessionStorage.getItem('r3x.panel-id');
    if (v) return v;
    sessionStorage.setItem('r3x.panel-id', fresh);
  } catch {
    /* storage blocked: a new id per load */
  }
  return fresh;
})();
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
  /** The electronics package's own light groups (grnwave), or null for the native boards. */
  package: Record<string, RGB[]> | null;
}

let rig: Rig | null = null;
let leds: FaceLeds | null = null;
let chestLights: ChestLights | null = null;
let pkgLeds: PackageLeds | null = null;
let boards: BoardsView | null = null;
let builtFor: string | null = null;
let ghosts: Ghosts | null = null;
let centres: Centres | null = null;
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
/** Standalone gaze: the head follows this view's camera (the Gaze select, offline). */
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
Performer.create(Number(params.get('seed') ?? Math.floor(Math.random() * 2 ** 31)), profileJsonWith(PROFILE_JSON, LOCAL_PACKAGE.id))
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

/** The droid's head as the performer wants a look target: (pan, tilt) degrees from its rest (Rig.aimAt). */
const aimAt = (p: THREE.Vector3) => rig?.aimAt(p) ?? null;

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
  const pkgPx = f.package && Object.keys(f.package).length ? f.package : null;
  view = { joints: f.joints, eyes: f.eyes, mouth: f.mouth, chest: f.chest, stage: pinDesk ? null : f.stage, servo: f.servo.targets, package: pkgPx };
}

// ------------------------------------------------------------------ gateway follower
function onFrames(f: Frames) {
  if (studioLocal) return; // Studio previews on the embedded performer
  const px = (k: string) => f.lights[k] ?? [];
  const pkg = activePackage();
  view = {
    joints: f.joints, eyes: px('eyes'), mouth: px('mouth'), chest: px('chest'),
    stage: f.lights.stage ? f.lights.stage.map((c) => c.map((v) => v / 255)) : null, servo: null,
    package: ownsLights(pkg) ? Object.fromEntries(pkg.lights.map((g) => [g.name, px(g.name)])) : null,
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
  studio.setActive(s.stage.mode === 'studio');
  centresFollowMode(s.stage.mode);
  ghosts?.apply(s.stage.outputs);
  renderGaze(s);
  renderHome();
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
    renderGaze(null);
    perf({ cmd: 'autonomy', on: autonomy });
    $<HTMLInputElement>('sh-idle').checked = autonomy;
    $('btn-dj').classList.toggle('on', djOn);
    frozen = false;
    $('sh-freeze').classList.remove('on');
    $('eng-now').textContent = titleCase(mode);
  }
  showUiDirty = true;
}

// CantinaOS's SimBridge feed (`./r3x --legacy` opens the panel with ?legacy): the panel reads
// its log lines; the 3D view no longer does. The standalone runtime's logs come from the gateway.
const liveEl = $('st-live');
const legacy = params.has('legacy');
liveEl.hidden = !legacy;
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
    gwPackage = (h.profile as RobotProfile | null | undefined)?.package ?? null;
    setConnected(true);
    buildPackageLights();
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
  panel.setLocalMode(mode as 'show' | 'bench' | 'studio');
  studio.setActive(mode === 'studio');
  centresFollowMode(mode);
  renderHome();
}
if (studio.wantsOpen()) setLocalStage('studio');

// ?offline keeps a tab on the embedded performer even while the runtime is running.
if (legacy && !params.has('offline')) link.start();

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

  rig = new Rig(gltf.scene, doc, restFromUrl());
  leds = new FaceLeds(rig);
  tameHighlights(gltf.scene);
  chestLights = new ChestLights(rig.get('torso_middle').node, doc.chest_lights ?? []);
  buildPackageLights();
  ghosts = new Ghosts(rig, PROFILE);
  ghosts.apply(gwState?.stage.outputs ?? null);
  centres = new Centres(rig, FULL_PROFILE.joints, $('centre-labels'));
  centres.setVisible(centresOn);
  showPivots(centresOn);
  buildJointTable(rig);
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
const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();

/** Disabled light outputs (connected), dimmed: the real driver would stay dark. */
function dim(px: RGB[], output: string): RGB[] {
  if (gwState?.stage.outputs[output] !== false) return px;
  return px.map((c) => c.map((v) => v * 0.15) as RGB);
}

let readoutAt = -1;
function frame() {
  post.pacer.next(frame);
  sendViewportGaze(); // every animation frame, drawn or not (it rate-limits itself)
  // Draw only as often as the picture can change (pacer.ts; rates from the Quality setting).
  if (!post.pacer.due(performance.now(), view)) return;
  const t = clock();
  const dt = Math.min(0.1, t - last);
  last = t;

  tickPerformer(t);
  if (view && rig && leds && chestLights) {
    const values = new Map(Object.entries(view.joints));
    rig.apply(values);
    if (view.package && pkgLeds) {
      // The package's LEDs at their real positions; the eye bulbs / mouth pipe glow from them.
      const dimmed = Object.fromEntries(Object.entries(view.package).map(([k, v]) => [k, dim(v, k)]));
      pkgLeds.update(dimmed);
      const face = faceSlots(dimmed);
      leds.update(face.eyes, face.mouth);
    } else {
      leds.update(dim(view.eyes, 'eyes'), dim(view.mouth, 'mouth'));
      chestLights.update(dim(view.chest, 'chest'));
    }
    let speed = 0;
    if (prevJoints && dt > 0) for (const [j, v] of values) speed += Math.abs(v - (prevJoints[j] ?? v)) / dt;
    prevJoints = view.joints;
    whine.update(speed, dt);
    // Panel readouts (text, a 2D canvas) at 10 Hz: nobody reads numbers faster.
    if (t - readoutAt >= 0.1) {
      readoutAt = t;
      updateJointReadout();
      updateServoTable();
      drawLeds();
    }
  }
  // The venue's light desk: the performer's stage output, or (look-dev pin) its own program.
  // Not dimmed when the stage output is disabled: that gates the DMX driver, and the sim's
  // desk also lights R3X, who must look the same in every mode.
  if (view?.stage) {
    set.lights?.setExternal(view.stage);
  } else {
    set.lights?.setExternal(null);
  }
  if (showUiDirty || ++showUiTick % 15 === 0) updateShowUi();

  controls.update();
  if (!sceneLook || sceneLook.showingBooth) set.constrain(camera, controls.target);
  post.render();
  if (view) centres?.render(renderer, camera, view.joints);
  if (++homeTick % 12 === 0) updateJointTable();
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

/** Jogging holds the joints; release hands them back to the performer. */
function setManual(on: boolean) {
  manual = on;
  $<HTMLButtonElement>('jog-release').disabled = !on;
}
$('jog-release').onclick = () => {
  setManual(false);
  if (connected) void send({ class: 'perf', type: 'release', channels: [...jointInputs.keys()] });
  else perf({ cmd: 'jog_release' });
};

/** Each joint's pivot axis, drawn with the Centres overlay. */
function showPivots(on: boolean) {
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
}

// Presets come from the set (booth.ts): in the booth they frame the droid through the arch.
// The chest preset looks at the logic panels on the droid's right-front quarter.
const CAMS = set.cams;
let camPreset = 'full';
function goCam(name: string) {
  camPreset = name;
  const [p, tgt] = CAMS[name];
  camera.position.copy(p);
  controls.target.copy(tgt);
  document.querySelectorAll<HTMLButtonElement>('[data-cam]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.cam === name)));
}
document.querySelectorAll<HTMLButtonElement>('[data-cam]').forEach((b) => {
  b.onclick = () => goCam(b.dataset.cam!);
});
$('cam-reset').onclick = () => goCam(camPreset);

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

// ------------------------------------------------------------------ joint jog (Rig -> Joints)
const jointInputs = new Map<string, { input: HTMLInputElement; out: HTMLOutputElement | null }>();

function updateJointReadout() {
  if (!view) return;
  for (const [name, ui] of jointInputs) {
    const v = view.joints[name] ?? 0;
    if (ui.out) ui.out.textContent = v.toFixed(0);
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
/**
 * The Electronics tab and the package's 3D lights/boards, for the package this page runs
 * (the gateway's once connected). Rebuilt only when that package changes.
 */
function buildPackageLights() {
  const pkg = activePackage();
  if (!rig || !chestLights || builtFor === pkg.id) {
    mountElectronics($('electronics-slot'), pkg, { boards: () => boards, connected });
    return;
  }
  builtFor = pkg.id;
  pkgLeds?.dispose();
  boards?.dispose();
  pkgLeds = ownsLights(pkg) ? new PackageLeds(rig, pkg.lights) : null;
  chestLights.setVisible(!ownsLights(pkg));
  boards = new BoardsView(rig, pkg);
  mountElectronics($('electronics-slot'), pkg, { boards: () => boards, connected });
}

/** The LED readout for a package with its own boards: eyes, mouth V, then one row per body board. */
function drawPackageLeds(px: Record<string, RGB[]>, css: (c?: RGB) => string) {
  const W = ledCanvas.width;
  if (ledCanvas.height !== 150) ledCanvas.height = 150;
  const dot = (x: number, y: number, c: RGB | undefined, r: number) => {
    lctx.beginPath();
    lctx.arc(x, y, r, 0, Math.PI * 2);
    lctx.fillStyle = css(c);
    lctx.fill();
    lctx.strokeStyle = '#2a2e38';
    lctx.stroke();
  };
  const eyes = px.eyes ?? [];
  dot(W * 0.8, 24, eyes[0], 9); // the droid's left eye on the viewer's right
  dot(W * 0.2, 24, eyes[1], 9);
  const mouth = px.mouth ?? [];
  for (let i = 0; i < 8; i++) {
    const arm = i < 4 ? i : 7 - i;
    dot(W / 2 + (i < 4 ? -1 : 1) * (18 - arm * 5), 6 + arm * 13, mouth[i], 4);
  }
  const body = px.body ?? [];
  const boardsN = Math.floor(body.length / 32);
  for (let b = 0; b < boardsN; b++) {
    const y = 72 + b * 26;
    for (let k = 0; k < 8; k++) dot(20 + k * 12, y, body[b * 32 + k], 4);
    for (let g = 0; g < 6; g++) {
      for (let j = 0; j < 4; j++) {
        lctx.fillStyle = css(body[b * 32 + 8 + g * 4 + j]);
        lctx.fillRect(122 + g * 28 + (j % 2) * 9, y - 8 + Math.floor(j / 2) * 9, 8, 8);
      }
    }
    lctx.fillStyle = '#5b6170';
    lctx.fillText('ABC'[b], 4, y + 4);
  }
}

function drawLeds() {
  const W = ledCanvas.width;
  const H = ledCanvas.height;
  lctx.clearRect(0, 0, W, H);
  if (!view) return;
  if (view.package) {
    const k2 = (OUTPUT_BRIGHTNESS + 1) / 256;
    const b2 = (v: number) => Math.min(255, Math.round(v * k2 * 1.9));
    lctx.font = '10px ui-monospace, Menlo, monospace';
    drawPackageLeds(view.package, (c: RGB = BLACK) => `rgb(${b2(c[0])},${b2(c[1])},${b2(c[2])})`);
    return;
  }
  if (ledCanvas.height !== 96) ledCanvas.height = 96;
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
  // The profile's emote slots (the gamepad's A B X Y, Back+ for 5-8).
  SLOTS.forEach((id, i) => {
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = id.replace(/_/g, ' ');
    b.title = `${items.find((it) => it.id === id)?.description ?? id} (slot ${i + 1})`;
    b.onclick = () => emote(i);
    $('emotes').appendChild(b);
  });
  $<HTMLInputElement>('sh-search').oninput = (e) => {
    const q = (e.target as HTMLInputElement).value.trim().toLowerCase();
    for (const kind of ['sequence', 'cue', 'clip']) {
      const ul = $('sh-list-' + kind);
      let n = 0;
      for (const li of Array.from(ul.children) as HTMLElement[]) {
        li.hidden = !!q && !(li.dataset.id ?? '').toLowerCase().includes(q);
        if (!li.hidden) n++;
      }
      $('sh-n-' + kind).textContent = q ? `(${n} of ${ul.children.length})` : `(${ul.children.length})`;
      (ul.parentElement as HTMLDetailsElement).open = !!q && n > 0;
    }
  };
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

  // Intensity per operating mode: Bench tests start low; each mode keeps its last value.
  const intensityFor: Record<string, number> = { show: 1, bench: 0.35, studio: 1 };
  let intensityMode = 'show';
  addEventListener('r3x:mode', (e) => {
    const m = String((e as CustomEvent).detail);
    const el = $<HTMLInputElement>('sh-int');
    intensityFor[intensityMode] = Number(el.value);
    intensityMode = m;
    el.value = String(intensityFor[m] ?? 1);
    out('sh-int-out', el.value);
  });

  // Puppeteer: one slider per continuous intent and the modes (emotes are their own section).
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
    // Frozen motion refuses every play: say so instead of offering buttons that bounce.
    document.querySelectorAll<HTMLButtonElement>('.show-list button, #emotes button').forEach((b) => (b.disabled = frozen));
    const take = performer?.takeInfo();
    $('take-info').textContent = !take ? '' : take.recording ? `Recording: ${take.seconds.toFixed(1)} s, ${take.samples} samples @ 50 Hz` : take.samples ? `Last take: ${take.seconds.toFixed(1)} s` : '';
    if (!connected) $('eng-now').textContent = titleCase(mode);
  };
}

// ------------------------------------------------------------------ Home (Bench / Studio)
let homeTick = 0;
/** Where the Studio preview or the offline demo runs: the embedded performer. */
const local = () => !connected || studioLocal;

function home(joints: string[] = []) {
  if (!local()) return void send({ class: 'perf', type: 'home', joints });
  perf({ cmd: 'home', joints });
  if (studioLocal) performer?.command({ cmd: 'home', joints }); // perf() stands down while connected
}
$('drive-home').onclick = () => home();

/** Home is a Bench/Studio action (it lives in Bench's Rig tab; the runtime refuses it in Show). */
function renderHome() {
  const mode = gwState?.stage.mode ?? null;
  $<HTMLButtonElement>('drive-home').disabled = connected && mode === 'show';
}

function renderHomeState() {
  const el = $('home-state');
  let text = '-';
  let on = false;
  if (connected && !studioLocal && gwState?.perf.at_home !== undefined) {
    on = gwState.perf.at_home;
    text = on ? 'at home' : gwState.perf.homing ? 'homing…' : 'off home';
  } else if (view) {
    on = atHome(view.joints, HOME, JOINTS, 1); // simulated servo output, not the follower
    text = on ? 'at home' : 'off home';
  }
  el.textContent = text;
  el.classList.toggle('on', on);
}

/**
 * Rig -> Joints: one row per joint, grouped by body region - a jog slider, the current and
 * home value, a per-joint Home; hover a row (or an output) to highlight it on the droid.
 */
const jointRows = new Map<string, HTMLElement>();
const REGIONS = new BodyRegions(FULL_PROFILE);
function buildJointTable(r: Rig) {
  const body = $('joint-rows');
  const names = FULL_PROFILE.joints.map((j) => j.name).filter((n) => r.joints.has(n));
  body.innerHTML = REGIONS.group(names, (n) => REGIONS.ofDrivenJoint(n)).map(([region, list]) =>
    `<tr class="grp"><th colspan="5" scope="colgroup">${region}</th></tr>` + list.map((n) => {
      const j = FULL_PROFILE.joints.find((x) => x.name === n)!;
      return `<tr data-joint="${n}" title="${n}${r.joints.get(n)!.spec.note ? ` - ${r.joints.get(n)!.spec.note}` : ''}">
        <th scope="row">${n.replace(/^(head|torso)_/, '').replace(/_/g, ' ')}</th>
        <td class="jog"><input type="range" min="${r.joints.get(n)!.spec.min}" max="${r.joints.get(n)!.spec.max}" step="0.5" value="${HOME[n]}" aria-label="Jog ${n}" /></td>
        <td class="num">-</td><td class="num home">${formatValue(HOME[n], j.unit)}</td>
        <td><button data-home-joint="${n}" aria-label="Home ${n}" title="Ease ${n} to its home value (held until released)">home</button></td></tr>`;
    }).join('')).join('');
  for (const tr of Array.from(body.querySelectorAll<HTMLElement>('tr[data-joint]'))) {
    const name = tr.dataset.joint!;
    jointRows.set(name, tr);
    const input = tr.querySelector('input')!;
    input.oninput = () => {
      const v = Number(input.value);
      if (!manual) setManual(true);
      // A joint name in a perf puppet command jogs that joint directly (runtime).
      if (connected) void send({ class: 'perf', type: 'puppet', channels: { [name]: v } });
      else perf({ cmd: 'jog', joint: name, value: v });
    };
    jointInputs.set(name, { input, out: null });
  }
  body.onclick = (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-home-joint]');
    if (b) home([b.dataset.homeJoint!]);
  };
  body.onmouseover = (e) => centres?.highlight([(e.target as HTMLElement).closest<HTMLElement>('tr')?.dataset.joint ?? ''].filter(Boolean));
  body.onmouseleave = () => centres?.highlight(null);
  // The Rig outputs are actuators: hover one to see the joints it drives.
  $('drive-outputs').onmouseover = (e) => {
    const out = (e.target as HTMLElement).closest<HTMLElement>('[data-output]')?.dataset.output;
    const a = FULL_PROFILE.actuators.find((x) => x.name === out);
    centres?.highlight(a ? Object.keys(a.joints) : null);
  };
  $('drive-outputs').onmouseleave = () => centres?.highlight(null);
}

function updateJointTable() {
  renderHomeState();
  if (!view) return;
  for (const [j, tr] of jointRows) {
    const v = view.joints[j] ?? 0;
    const unit = FULL_PROFILE.joints.find((x) => x.name === j)!.unit;
    tr.children[2].textContent = formatValue(v, unit);
    // What the (simulated) servo reached, which settles inside its deadband: amber past 1.
    tr.classList.toggle('off', Math.abs(v - HOME[j]) > 1);
  }
}

// Hovering the droid highlights the joint under the pointer (Centres on only; ~10 Hz).
{
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let pending = false;
  let last: PointerEvent | null = null;
  renderer.domElement.addEventListener('pointermove', (e) => {
    last = e;
    if (pending || !centres?.shown || !rig) return;
    pending = true;
    setTimeout(() => {
      pending = false;
      if (!last || !rig) return;
      ndc.set((last.clientX / innerWidth) * 2 - 1, -(last.clientY / innerHeight) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      let o: THREE.Object3D | null = ray.intersectObject(rig.root, true)[0]?.object ?? null;
      while (o && !o.name.startsWith('j_')) o = o.parent;
      centres?.highlight(o ? [o.name.slice(2)] : null);
    }, 100);
  });
  renderer.domElement.addEventListener('pointerleave', () => centres?.highlight(null));
}

// ------------------------------------------------------------------ gaze target (Show)
const gazeSel = $<HTMLSelectElement>('gaze-src');
let claimed = false;
const gazeOffline = ['vision'];
function renderGaze(s: RetainedState | null) {
  // Standalone: the embedded performer follows this view's camera, or nothing.
  for (const o of Array.from(gazeSel.options)) if (gazeOffline.includes(o.value)) o.disabled = !s;
  if (!s) {
    gazeSel.value = lookAtCamera ? 'viewport' : 'off';
    $('gaze-owner').hidden = true;
    return;
  }
  // A runtime without gaze support (older build): the standalone choice stands.
  const g = s.stage.gaze;
  $('gaze-owner').hidden = !g || g !== 'viewport';
  if (!g) return;
  if (document.activeElement !== gazeSel) gazeSel.value = g;
  const owner = s.stage.gaze_owner ?? null;
  $('gaze-owner').textContent = owner === PANEL_ID ? 'Following this view' : owner ? `Following ${owner}'s view` : 'No view owns it yet';
  gazeSel.title = `What R3X's head looks at in Show (Bench and Studio ignore it). This view: ${PANEL_ID}`;
  // Nobody owns the viewport: the first panel to see that claims it.
  if (g === 'viewport' && !owner && !claimed) {
    claimed = true;
    void send({ class: 'stage', type: 'set_gaze', source: 'viewport', owner: PANEL_ID });
  }
}
gazeSel.onchange = () => {
  const source = gazeSel.value as GazeSource;
  if (!connected) {
    lookAtCamera = source === 'viewport';
    if (!lookAtCamera) perf({ cmd: 'look', pan_tilt: null });
    gazeSel.blur();
    return;
  }
  void send({ class: 'stage', type: 'set_gaze', source, ...(source === 'viewport' ? { owner: PANEL_ID } : {}) });
  gazeSel.blur();
};
if (!connected) renderGaze(null); // standalone until the gateway says hello

/** The owning panel's camera as the gaze target: ~15 Hz while it moves, 2 Hz keepalive. */
let lookSent = { pan: NaN, tilt: NaN, at: 0 };
function sendViewportGaze() {
  const st = gwState?.stage;
  if (!connected || !st || st.mode !== 'show' || st.gaze !== 'viewport' || (st.gaze_owner && st.gaze_owner !== PANEL_ID)) return;
  const now = performance.now();
  if (now - lookSent.at < 66) return;
  const pt = aimAt(camera.position);
  if (!pt) return;
  const moved = Math.abs(pt[0] - lookSent.pan) > 0.3 || Math.abs(pt[1] - lookSent.tilt) > 0.3;
  if (!moved && now - lookSent.at < 500) return;
  lookSent = { pan: pt[0], tilt: pt[1], at: now };
  void panel.gw.send({ class: 'perf', type: 'look', pan: pt[0], tilt: pt[1], owner: PANEL_ID });
}
