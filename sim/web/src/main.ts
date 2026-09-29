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
import { ControlPanel } from './panel';
import { Actuation, DEFAULT_PROFILE, PROFILES } from './actuation/pipeline';
import { MaestroScript, MaestroScriptError } from './actuation/maestro';
import { limitControls, setupStage } from './booth'; // before any material compiles (patches a chunk)
import { prepareDroidMaterials, tameHighlights } from './look';
import { RIGS, type Mode as LightMode } from './stagelights';
import { loadCatalog } from './show/loader';
import { norm } from './show/catalog';
import { expand, ownsUnion } from './show/expand';
import { ShowPlayer, type DispatchCtx, type EndReason, type RunInfo, type RunLayer } from './show/player';
import { BodyCompositor, type Pose } from './show/body';
import { IdleRunner } from './show/idle';
import { CONTINUOUS, MODES, Puppeteer, SLOT_COUNT, type Intent, type PuppetMode } from './show/puppeteer';
import { TakeRecorder } from './show/take';
import { clampIntensity, clampSpeed, type DeptAction, type Params, type Source } from './show/types';
import { ServoWhine } from './servowhine';
import { trackBpm } from './tempo';

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
  const panelEl = document.getElementById('panel');
  const panel = w > 720 && !STILL && panelEl ? panelEl.offsetWidth + 24 : 0;
  document.documentElement.style.setProperty('--panel-space', `${panel}px`);
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
  if (a === 'listening' || a === 'thinking' || a === 'speaking') {
    // Interaction: idle stops and its timer restarts. A listening turn also blends the
    // gesture layer out (Reachy clears its move queue when the user starts talking).
    idle.poke(clock(), stopRun);
    if (a === 'listening' && performer?.activity !== 'listening') player.stop({ layer: 'gesture' }, clock());
  }
  performer?.setActivity(a, clock());
}

const clock = () => (performance.now() - t0) / 1000;
const sleep = (s: number) => new Promise((r) => setTimeout(r, s * 1000));

// ------------------------------------------------------------------ show system (show/SPEC.md)
// Clips, cues and sequences from the repo's show/ folder. Offline, ShowPlayer conducts and
// dispatches each department here; live, CantinaOS conducts and the same renderers are fed
// from the bus (onLiveEvent). The body compositor sits between the Performer and actuation.
const catalog = loadCatalog();
if (catalog.errors.length) console.warn(`show: ${catalog.errors.length} problem(s)\n  ${catalog.errors.join('\n  ')}`);
const procPose: Pose = {};
const body = new BodyCompositor();
/** Kit sounds by normalised stem ("airhorn" -> "Air Horn.mp3"); loaded apart from the model, so live show.sfx works early. */
const sfxFiles = new Map<string, string>();
fetch('/sfx/index.json')
  .then((r) => (r.ok ? (r.json() as Promise<string[]>) : []))
  .then((list) => list.forEach((f) => sfxFiles.set(norm(f.replace(/\.[^.]+$/, '')), f)))
  .catch(() => { /* no kit sounds on this machine */ });
const liveRuns = new Map<string, { id: string; layer: RunLayer; source: string }>();
const whine = new ServoWhine();
const take = new TakeRecorder();
let frozen = false;
let showUiDirty = true;
let showUiTick = 0;
/** A show's `lights {mode}`: held until `until` or until the state underneath changes. */
let showLight: { mode: LightMode; until: number; natural: LightMode } | null = null;
/** Background layer: an authored loop per activity (null = procedural only). */
const backgrounds: Record<'idle' | 'dj', string | null> = { idle: null, dj: null };
let backgroundRun: { id: string; run: string } | null = null;
let updateShowUi = () => {};
let statusTimer = 0;
function showStatus(msg: string) {
  const el = document.getElementById('sh-status');
  if (!el) return;
  el.textContent = msg;
  clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => (el.textContent = ''), 5000);
}

const player = new ShowPlayer(catalog, {
  dispatch: showDispatch,
  speechActive: () => speaking || liveSpeaking,
  // Offline the BPM slider is the live tempo whenever music or DJ mode is on.
  liveBpm: () => (musicPlaying || djOn || liveDj ? bpm : null),
  started: onRunStarted,
  ended: onRunEnded,
});
const idle = new IdleRunner(catalog.idle, 0);
const SLOTS = ['yes', 'no', 'greet', 'excited', 'thinking', 'hype_drop', 'glitch_small', 'applause_thanks'].slice(0, SLOT_COUNT);
const puppet = new Puppeteer({
  slot: (i) => performShow(SLOTS[i], { intensity: 1 + 0.3 * puppet.cmd.energy, speed: 1 + 0.15 * puppet.cmd.energy }, 'ui'),
  mode: (m) => applyPuppetMode(m),
  freeze: () => setFreeze(!frozen),
});
body.puppet = puppet;

const stopRun = (runId: string) => player.stop({ id: runId }, clock());

/** Perform from the UI, a pad slot, __r3x or the idle policy (offline only). */
function performShow(id: string, params: Params = {}, source: Source = 'ui'): string | null {
  if (link.connected) {
    showStatus('Live: CantinaOS conducts. Trigger shows from CantinaOS (show.perform).');
    return null;
  }
  if (source !== 'idle') idle.poke(clock(), stopRun);
  return player.perform(id, { source, params, now: clock() });
}

function showDispatch(a: DeptAction, ctx: DispatchCtx) {
  switch (a.do) {
    case 'clip': {
      const c = catalog.clip(a.id);
      if (c) body.play({ runId: ctx.run.run_id, clip: c, intensity: a.intensity, speed: a.speed, layer: ctx.run.layer, owns: ctx.run.owns, t0: ctx.at });
      break;
    }
    case 'eyes':
      // EYE_COMMAND through the host emulator's pattern path, so the firmware renders it.
      host.face.eyeCommand(a.pattern, a.duration ?? 0);
      break;
    case 'chest':
      host.chest.override(a.command, a.hold ?? 0, fw.now);
      break;
    case 'lights':
      lightsAction(a);
      break;
    case 'sfx':
      playSfx(a.id);
      break;
    case 'speak':
      void showSpeak(a.text);
      break;
    case 'duck':
    case 'unduck':
      break; // the sim has no music bed to duck
  }
  take.event(clock(), 'show.action', { run_id: ctx.run.run_id, ...a });
}

function onRunStarted(r: RunInfo) {
  if (r.owns) body.own(r.run_id, r.layer, r.owns, clock());
  take.event(clock(), 'show.started', { id: r.id, kind: r.kind, source: r.source, run_id: r.run_id });
  showUiDirty = true;
}

function onRunEnded(r: RunInfo, reason: EndReason) {
  body.release(r.run_id, clock());
  idle.ended(r.run_id, clock());
  take.event(clock(), 'show.ended', { id: r.id, kind: r.kind, source: r.source, run_id: r.run_id, reason });
  if (reason === 'rejected') showStatus(frozen ? `${r.id}: rejected (motion frozen)` : `${r.id}: rejected - unknown, or its tier does not allow source "${r.source}"`);
  showUiDirty = true;
}

/** motion.freeze: stop shows and gestures, stop idle, hold setpoints; off blends back over 0.5 s. */
function setFreeze(on: boolean) {
  const t = clock();
  frozen = on;
  player.freeze(on, t);
  const hold: Pose = {};
  if (on && actuation) for (const [j, ch] of actuation.byJoint) if (ch.primaryJoint === j) hold[j] = ch.follower.x;
  body.freeze(on, t, hold);
  if (on) idle.poke(t, stopRun);
  document.getElementById('sh-freeze')?.classList.toggle('on', on);
  take.event(t, 'motion.freeze', { on });
  showUiDirty = true;
}

/** The desk's mode from the system state, not counting speech (speech is a transient overlay). */
function naturalLightMode(): LightMode {
  return djOn || liveDj ? 'dj' : musicPlaying ? 'music' : 'idle';
}

/** SPEC `lights` / stage.lights: {cue?, mode?, fade?, hold?, rig?}. */
function lightsAction(a: { cue?: string; mode?: string; fade?: number; hold?: number; rig?: string }) {
  const L = set.lights;
  if (!L) return;
  const fade = Number(a.fade ?? 0);
  const hold = Number(a.hold ?? 0);
  if (a.rig && RIGS[a.rig]) L.setRig(a.rig, fade || 1);
  if (a.mode) {
    const m = a.mode as LightMode;
    showLight = { mode: m, until: hold > 0 ? clock() + hold : Infinity, natural: naturalLightMode() };
    L.setMode(m, fade || undefined);
  }
  if (a.cue && !L.showCue(a.cue, fade, hold)) console.warn(`lights: rig ${L.rigPreset} has no cue "${a.cue}"`);
}

function playSfx(id: string) {
  const f = sfxFiles.get(norm(id));
  if (!f) return void console.warn(`sfx: no kit sound matches "${id}" (sim/web/public/sfx is built locally)`);
  const el = new Audio(`/sfx/${encodeURIComponent(f)}`);
  el.volume = 0.7;
  void el.play().catch(() => { /* autoplay before a user gesture */ });
}

/** Offline `speak`: a caption plus the fake-amplitude speaking path. */
async function showSpeak(text: string) {
  if (host.mode === 'IDLE') host.setMode('INTERACTIVE');
  capSaid.textContent = text;
  caption.hidden = false;
  await fakeSpeech(Math.max(1.2, 0.06 * text.length + 0.4));
  setTimeout(() => {
    if (capSaid.textContent !== text) return;
    capSaid.textContent = '';
    caption.hidden = !capHeard.textContent;
  }, 1500);
}

function idleEligible() {
  const a = performer?.activity;
  return !frozen && !busy && !speaking && !audio.micOn && (a === 'idle' || a === 'engaged' || a === 'dj') &&
    player.running().every((r) => r.run_id === idle.current || r.layer === 'background');
}

function updateBackground() {
  const a = performer?.activity;
  const want = frozen ? null : a === 'dj' ? backgrounds.dj : a === 'idle' || a === 'engaged' ? backgrounds.idle : null;
  const cur = player.running().find((r) => r.layer === 'background');
  if (backgroundRun && (backgroundRun.id !== want || !cur || cur.run_id !== backgroundRun.run)) {
    if (cur) player.stop({ layer: 'background' }, clock());
    backgroundRun = null;
  }
  if (want && !backgroundRun) {
    const run = player.perform(want, { source: 'timeline', layer: 'background', now: clock() });
    if (run) backgroundRun = { id: want, run };
  }
}

function applyPuppetMode(m: PuppetMode) {
  if (m === 'dj') {
    if (!djOn) setDj(true);
  } else {
    if (djOn) setDj(false);
    host.setMode(m === 'idle' ? 'IDLE' : 'INTERACTIVE');
    setActivity(m === 'idle' ? 'idle' : 'engaged');
  }
  document.querySelectorAll<HTMLButtonElement>('[data-pmode]').forEach((b) => b.classList.toggle('on', b.dataset.pmode === m));
}

function takeSample() {
  const layers: Record<string, string | null> = { background: null, gesture: null, show: null };
  for (const r of player.running()) layers[r.layer] = r.id;
  for (const r of liveRuns.values()) layers[r.layer] = r.id;
  const slots = puppet.fired.splice(0);
  return {
    cmd: { ...puppet.cmd }, slots, mode: puppet.mode, frozen, activity: performer?.activity ?? 'idle',
    speaking: speaking || liveSpeaking, layers, live: link.connected,
  };
}

function startTake() {
  take.start(clock(), SLOTS);
  $('take-rec').classList.add('on');
  $('take-rec').textContent = 'Stop take';
}
function stopTake() {
  take.stop();
  $('take-rec').classList.remove('on');
  $('take-rec').textContent = 'Record take';
}

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
  // The Performer writes the procedural pose; the body compositor (show/body.ts) stacks the
  // background / gesture / show / puppeteer / freeze layers on it before actuation.
  performer = new Performer(rig, (joint, value) => (procPose[joint] = value));
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
  setActivity(restingActivity());
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
    case 'music.playback.started': {
      musicPlaying = true;
      // The analysed tempo (track.bpm) drives the stage-light desk, the bop loops and the
      // chest; with none known the BPM slider stays in charge.
      const live = trackBpm(d);
      if (live !== null) setBpm(live, false);
      host.chest.music(true, bpm);
      if (!liveSpeaking) setActivity('dj');
      break;
    }
    case 'music.playback.stopped':
      musicPlaying = false;
      host.chest.music(false);
      if (!liveSpeaking) setActivity(restingActivity());
      break;
    case 'dj.mode.changed':
      liveDj = Boolean(d.is_active);
      if (!liveSpeaking) setActivity(restingActivity());
      break;
    // ---- show system (SPEC "Live bus contract"): CantinaOS conducts, the sim renders.
    case 'show.motion': {
      const c = catalog.clip(String(d.clip));
      if (!c) { console.warn(`show.motion: unknown clip ${String(d.clip)}`); break; }
      const startAt = Number(d.start_at);
      const delay = Number.isFinite(startAt) ? Math.min(2, Math.max(0, startAt - Date.now() / 1000)) : 0;
      const layer: RunLayer = d.layer === 'show' || d.layer === 'background' ? d.layer : 'gesture';
      body.play({
        runId: String(d.run_id), clip: c, layer, t0: clock() + delay,
        intensity: clampIntensity(Number(d.intensity ?? 1)), speed: clampSpeed(Number(d.speed ?? 1)),
        owns: Array.isArray(d.owns) ? d.owns.map(String) : null,
      });
      break;
    }
    case 'stage.lights':
      lightsAction(d as Parameters<typeof lightsAction>[0]);
      break;
    case 'show.sfx':
      playSfx(String(d.id));
      break;
    case 'show.started': {
      const seq = catalog.sequence(String(d.id));
      const owns = seq ? ownsUnion(seq, catalog) : null;
      const layer: RunLayer = seq?.layer ?? (d.kind === 'sequence' ? 'show' : 'gesture');
      liveRuns.set(String(d.run_id), { id: String(d.id), layer, source: String(d.source ?? '') });
      if (owns) body.own(String(d.run_id), layer, owns, clock());
      showUiDirty = true;
      break;
    }
    case 'show.ended':
      liveRuns.delete(String(d.run_id));
      body.release(String(d.run_id), clock());
      if (d.reason === 'rejected') showStatus(`CantinaOS rejected ${String(d.id)} (tier) from ${String(d.source)}`);
      showUiDirty = true;
      break;
    case 'motion.freeze':
      setFreeze(Boolean(d.on));
      break;
  }
  if (/^(show|stage|motion|vision)\./.test(ev.topic)) take.event(clock(), ev.topic, d);
  caption.hidden = !(capHeard.textContent || capSaid.textContent);
}

const liveEl = document.getElementById('st-live')!;
const link = new LiveLink(`ws://${location.hostname || '127.0.0.1'}:8765`, {
  onHello(hello) {
    host.setMode(liveMode(hello.mode));
    setActivity(restingActivity());
  },
  onEvent: onLiveEvent,
  onStatus(on) {
    liveEl.textContent = on ? 'LIVE' : 'OFFLINE';
    liveEl.classList.toggle('on', on);
    if (on) {
      // CantinaOS conducts from now on: the offline player and idle policy stand down.
      player.stop({ all: true }, clock());
      idle.poke(clock(), stopRun);
    } else {
      liveSpeaking = false;
      musicPlaying = false;
      liveDj = false;
      host.chest.muted = false; // back to the offline port of the chest service
      host.chest.resync();
      for (const id of liveRuns.keys()) body.release(id, clock());
      liveRuns.clear();
    }
    showUiDirty = true;
  },
});
new ControlPanel(link);
// ?offline keeps a tab on the built-in demo even while CantinaOS is running.
if (!new URLSearchParams(location.search).has('offline')) link.start();

// Devtools: __r3x.fw.write('ST\n'), __r3x.actuation.command('head_pan', 40)
Object.assign(window, { __r3x: {
  fw, host, log, get rig() { return rig; }, get actuation() { return actuation; }, get chest() { return chestFw; }, camera, controls, post,
  // Show system: __r3x.show.play('dj_intro'), .play('nod', {intensity: 1.3, speed: 1.5}), .stop(), .running()
  show: {
    play: (id: string, params: Params & { source?: Source } = {}) => performShow(id, params, params.source ?? 'ui'),
    stop: (sel?: { id?: string; layer?: RunLayer; all?: boolean }) => player.stop(sel ?? { all: true }, clock()),
    running: () => player.running().map((r) => ({ ...r, clips: body.active(r.layer) })),
    freeze: (on = true) => setFreeze(on),
    expand: (id: string, bpm?: number) => expand(id, catalog, { bpm }),
    background: (activity: 'idle' | 'dj', id: string | null) => { backgrounds[activity] = id; },
    catalog, player, body, idle,
  },
  puppet: {
    set: (c: Partial<Record<Intent, number>>) => puppet.set(c),
    slot: (i: number) => puppet.trigger(i),
    mode: (m: PuppetMode) => puppet.setMode(m),
    get command() { return { ...puppet.cmd }; },
    freeze: (on = true) => setFreeze(on),
    record: (on = true) => (on ? startTake() : stopTake()),
    take: () => take.toJsonl(),
  },
} });

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
        energy: puppet.energyGain,
      });
      body.apply(procPose, t);
      for (const j of rig.joints.keys()) actuation.command(j, procPose[j] ?? 0);
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

  // Show system: offline, the sim's own player conducts (live, CantinaOS does).
  puppet.update(dt);
  if (!link.connected) {
    player.update(t);
    updateBackground();
    idle.update(t, { eligible: idleEligible(), music: musicPlaying || djOn }, (id) => performShow(id, {}, 'idle'), stopRun);
  }
  take.sample(t, takeSample);
  if (!take.recording) puppet.fired.length = 0; // slot triggers only matter to a take
  whine.update(actuation, dt);
  if (showUiDirty || ++showUiTick % 15 === 0) updateShowUi();

  // Stage lights: the venue's separate light desk, following the show state. A show's
  // `lights {mode}` holds until its hold runs out or the state underneath changes; while
  // R3X talks the desk runs its speaking program and then returns to the show's mode.
  const natural = naturalLightMode();
  if (showLight && (natural !== showLight.natural || t >= showLight.until)) showLight = null;
  set.lights?.setMode(liveSpeaking || speaking ? 'speaking' : showLight?.mode ?? natural);
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
function setDj(on: boolean) {
  djOn = on;
  $('btn-dj').classList.toggle('on', djOn);
  host.chest.dj(djOn, bpm);
  if (djOn) {
    if (host.mode === 'IDLE') host.setMode('AMBIENT');
    setActivity('dj');
  } else {
    setActivity(host.mode === 'IDLE' ? 'idle' : 'engaged');
  }
}
$('btn-dj').onclick = () => setDj(!djOn);
/** One place that changes the tempo: the slider, or CantinaOS's live track bpm. */
function setBpm(n: number, updateChest = true) {
  bpm = n;
  $<HTMLInputElement>('bpm').value = String(Math.round(n)); // the range input clamps its own display
  $('bpm-out').textContent = String(Math.round(n * 10) / 10);
  if (!updateChest) return;
  if (djOn) host.chest.dj(true, bpm);
  else if (musicPlaying) host.chest.music(true, bpm);
}
$<HTMLInputElement>('bpm').oninput = (e) => setBpm(Number((e.target as HTMLInputElement).value));

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

// ------------------------------------------------------------------ show system UI
{
  const params = (): Params => ({ intensity: Number($<HTMLInputElement>('sh-int').value), speed: Number($<HTMLInputElement>('sh-speed').value) });
  for (const kind of ['sequence', 'cue', 'clip'] as const) {
    const ul = $('sh-list-' + kind);
    const items = catalog.list(kind);
    $('sh-n-' + kind).textContent = `(${items.length})`;
    for (const it of items) {
      const li = document.createElement('li');
      li.dataset.id = it.id;
      li.title = `${it.description}${'requires' in it && it.requires ? ' [extended build]' : ''}${it.tags?.length ? `\ntags: ${it.tags.join(', ')}` : ''}`;
      const b = document.createElement('button');
      b.textContent = '\u25B6';
      b.setAttribute('aria-label', `Play ${it.id}`);
      b.onclick = () => performShow(it.id, params(), 'ui');
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
  const out = (id: string, v: string) => ($(id).textContent = Number(v).toFixed(2));
  $<HTMLInputElement>('sh-int').oninput = (e) => out('sh-int-out', (e.target as HTMLInputElement).value);
  $<HTMLInputElement>('sh-speed').oninput = (e) => out('sh-speed-out', (e.target as HTMLInputElement).value);
  $('sh-stop').onclick = () => player.stop({ all: true }, clock());
  $('sh-freeze').onclick = () => setFreeze(!frozen);
  $('sh-idle-label').textContent = `Idle policy (after ${catalog.idle?.after_s ?? '-'} s quiet)`;
  $<HTMLInputElement>('sh-idle').onchange = (e) => { idle.enabled = (e.target as HTMLInputElement).checked; };
  const loops = catalog.list('sequence').filter((q) => q.loop);
  for (const [sel, key] of [['sh-bg-idle', 'idle'], ['sh-bg-dj', 'dj']] as const) {
    const el = $<HTMLSelectElement>(sel);
    el.add(new Option('none', ''));
    for (const q of loops) el.add(new Option(q.id, q.id));
    el.onchange = () => { backgrounds[key] = el.value || null; };
  }

  // Puppeteer: one slider per continuous intent, the modes, the 8 cue slots.
  const box = $('pp-intents');
  const sliders = new Map<Intent, HTMLInputElement>();
  for (const k of CONTINUOUS) {
    const name = document.createElement('span');
    name.textContent = k;
    const input = document.createElement('input');
    input.type = 'range';
    input.min = '-1';
    input.max = '1';
    input.step = '0.01';
    input.value = '0';
    input.oninput = () => puppet.set({ [k]: Number(input.value) });
    const o = document.createElement('output');
    o.textContent = '0.00';
    box.append(name, input, o);
    sliders.set(k, input);
  }
  $('pp-center').onclick = () => {
    puppet.set(Object.fromEntries(CONTINUOUS.map((k) => [k, 0])));
    sliders.forEach((s) => (s.value = '0'));
  };
  for (const m of MODES) {
    const b = document.createElement('button');
    b.textContent = m;
    b.dataset.pmode = m;
    b.onclick = () => puppet.setMode(m);
    $('pp-modes').appendChild(b);
  }
  SLOTS.forEach((id, i) => {
    const b = document.createElement('button');
    b.textContent = `${i + 1} ${id}`;
    b.title = catalog.get(id)?.description ?? id;
    b.onclick = () => puppet.trigger(i);
    $('pp-slots').appendChild(b);
  });
  $('take-rec').onclick = () => (take.recording ? stopTake() : startTake());
  $('take-dl').onclick = () => {
    const blob = new Blob([take.toJsonl()], { type: 'application/x-ndjson' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `r3x-take-${new Date().toISOString().replace(/[:.]/g, '-')}.jsonl`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $<HTMLInputElement>('whine').onchange = (e) => whine.setEnabled((e.target as HTMLInputElement).checked);

  // Refreshed ~4x a second (and on every show event) from the frame loop.
  const outs = [...box.querySelectorAll('output')];
  updateShowUi = () => {
    showUiDirty = false;
    const running = link.connected
      ? [...liveRuns.entries()].map(([run_id, r]) => ({ run_id, id: r.id, layer: r.layer, source: r.source }))
      : player.running();
    const ids = new Set(running.map((r) => r.id));
    for (const layer of ['show', 'gesture', 'background'] as const) {
      const r = running.find((x) => x.layer === layer);
      const clipsOn = body.active(layer);
      const q = link.connected ? null : player.queued(layer);
      const el = $('sh-' + layer);
      el.textContent = r ? `${r.id} (${r.source})${clipsOn.length ? ' · ' + clipsOn.join(', ') : ''}${q ? ` · next ${q}` : ''}` : clipsOn.length ? clipsOn.join(', ') : '-';
      el.classList.toggle('on', !!r || clipsOn.length > 0);
    }
    document.querySelectorAll<HTMLLIElement>('.show-list li').forEach((li) => li.classList.toggle('running', ids.has(li.dataset.id!)));
    CONTINUOUS.forEach((k, i) => {
      outs[i].textContent = puppet.cmd[k].toFixed(2);
      if (puppet.gamepad) sliders.get(k)!.value = String(puppet.target[k]);
    });
    $('pp-pad').hidden = puppet.gamepad;
    $('take-info').textContent = take.recording ? `Recording: ${take.seconds.toFixed(1)} s, ${take.samples} samples @ 50 Hz` : take.samples ? `Last take: ${take.seconds.toFixed(1)} s` : '';
    document.querySelectorAll<HTMLButtonElement>('#show-system .show-list button').forEach((b) => (b.disabled = link.connected));
  };
}
