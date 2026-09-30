/**
 * `visit.html`: the public R3X page (plan Phase 10). Iframe-friendly; built on its own with
 * `npm run build:visit` (relative asset paths, so it can live under any prefix).
 *
 * - Standalone (no token): the embedded WASM performer alone - idle life, looking at the
 *   camera, emotes. No server, no cost.
 * - Connected (`#token=<visitor token>`, gateway `?gw=host[:port][/path]`): a follower of the
 *   visitor's own session on `r3x-runtime --public` - its frames, captions and speech - with
 *   hold-to-talk and typed turns. Falls back to standalone whenever the link is down.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { limitControls, setupStage } from '../booth'; // before any material compiles (patches a chunk)
import { PostPipeline } from '../post';
import { Rig, type RigDoc } from '../rig';
import { FaceLeds, type RGB } from '../leds';
import { ChestLights } from '../chestlights';
import { prepareDroidMaterials, tameHighlights } from '../look';
import { Performer, PROFILE_JSON } from '../performer';
import { GatewayClient, gatewayUrl, type Frames } from '../gateway';
import type { Command } from '../generated/Command';
import { RemoteVoice } from '../voice/remote';

const BASE = import.meta.env.BASE_URL;
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

// The token lives in the fragment (never sent to a server by the browser); drop it from the
// address bar. Not persisted: an embedding dashboard mints a fresh one per visit.
const token = new URLSearchParams(location.hash.slice(1)).get('token') ?? '';
if (token) history.replaceState(null, '', location.pathname + location.search);

// ------------------------------------------------------------------ scene
const renderer = new THREE.WebGLRenderer({ antialias: false });
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
$('stage').appendChild(renderer.domElement);
const scene = new THREE.Scene();
const set = setupStage(renderer, scene, new URLSearchParams(location.search).get('booth') !== '0');
const camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 0.02, 30);
camera.position.copy(set.cams.full[0]);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(set.cams.full[1]);
controls.enableDamping = true;
limitControls(controls, set.booth);
const post = new PostPipeline(renderer, scene, camera);
function fit() {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  post.setSize(innerWidth, innerHeight);
}
addEventListener('resize', fit);
fit();

let rig: Rig | null = null;
let leds: FaceLeds | null = null;
let chest: ChestLights | null = null;
async function load() {
  const draco = new DRACOLoader().setDecoderPath(`${BASE}draco/`);
  const ktx2 = new KTX2Loader().setTranscoderPath(`${BASE}basis/`).detectSupport(renderer);
  const loader = new GLTFLoader().setDRACOLoader(draco).setKTX2Loader(ktx2);
  const [gltf, doc] = await Promise.all([
    loader.loadAsync(`${BASE}model/r3x.glb`),
    fetch(`${BASE}model/rig.json`).then((r) => r.json() as Promise<RigDoc>),
  ]);
  ktx2.dispose();
  gltf.scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.isMesh) m.castShadow = m.receiveShadow = true;
  });
  prepareDroidMaterials(gltf.scene, renderer);
  scene.add(gltf.scene);
  rig = new Rig(gltf.scene, doc);
  leds = new FaceLeds(rig);
  tameHighlights(gltf.scene);
  chest = new ChestLights(rig.get('torso_middle').node, doc.chest_lights ?? []);
  $('loading').remove();
}
load().catch((e) => {
  console.error(e);
  $('loading').textContent = 'R3X could not be loaded.';
});

// ------------------------------------------------------------------ conductors
interface View { joints: Record<string, number>; eyes: RGB[]; mouth: RGB[]; chest: RGB[]; stage: number[][] | null }
let view: View | null = null;
let performer: Performer | null = null;
let connected = false;
const t0 = performance.now();
const clock = () => (performance.now() - t0) / 1000;

Performer.create(Math.floor(Math.random() * 2 ** 31))
  .then((p) => {
    performer = p;
    p.command({ cmd: 'autonomy', on: true });
    p.command({ cmd: 'mode', mode: 'IDLE' });
  })
  .catch((e) => console.error('performer (wasm) failed to load', e));

/** (pan, tilt) degrees in the head_pan parent frame, towards `p`. */
const tmp = new THREE.Vector3();
function aimAt(p: THREE.Vector3): [number, number] | null {
  const pan = rig?.joints.get('head_pan')?.node;
  const tilt = rig?.joints.get('head_tilt')?.node;
  if (!pan?.parent || !tilt) return null;
  const l = pan.parent.worldToLocal(tmp.copy(p));
  const dy = l.y - (pan.position.y + tilt.position.y);
  return [THREE.MathUtils.radToDeg(Math.atan2(l.x, l.z)), -THREE.MathUtils.radToDeg(Math.atan2(dy, Math.hypot(l.x, l.z)))];
}

function onFrames(f: Frames) {
  const px = (k: string) => f.lights[k] ?? [];
  view = { joints: f.joints, eyes: px('eyes'), mouth: px('mouth'), chest: px('chest'), stage: f.lights.stage ? f.lights.stage.map((c) => c.map((v) => v / 255)) : null };
}

// ------------------------------------------------------------------ talk (connected only)
const caption = $('caption');
const status = $('status');
function setCaption(you: string | null, r3x: string) {
  caption.replaceChildren();
  if (you) {
    const y = document.createElement('span');
    y.className = 'you';
    y.textContent = `You: ${you}`;
    caption.append(y);
  }
  caption.append(r3x);
}
let heard: string | null = null;
let said = '';

const gw = token ? new GatewayClient(gatewayUrl(token)) : null;
async function send(c: Command) {
  if (!gw) return;
  const a = await gw.send(c);
  if (a.status === 'rejected') status.textContent = a.reason;
}

if (gw) {
  const talk = $<HTMLButtonElement>('talk');
  const voice = new RemoteVoice(gw, {
    onState(s, detail) {
      talk.dataset.state = s;
      talk.textContent = s === 'listening' ? 'Listening… release to send' : s === 'starting' ? 'Starting…' : 'Hold to talk';
      if (s === 'refused') status.textContent = detail ?? 'not listening';
    },
  });
  gw.subscribe({
    onHello() {
      void send({ class: 'telemetry', type: 'frames', enabled: true });
    },
    onStatus(on) {
      connected = on;
      $('voice').hidden = !on;
      status.textContent = on ? '' : 'Connecting…';
      if (on) performer?.command({ cmd: 'stop', all: true });
      else view = null; // back to the embedded performer
    },
    onFrames,
    onEvent(e) {
      if (e.domain !== 'conversation') return;
      if (e.type === 'listening_started') [heard, said] = ['…', ''];
      else if (e.type === 'transcript') heard = e.text;
      else if (e.type === 'listening_stopped') [heard, said] = [e.transcript || '(nothing heard)', '…'];
      else if (e.type === 'reply_delta') said = said === '…' ? e.text : said + e.text;
      else if (e.type === 'reply') said = e.text;
      else return;
      setCaption(heard, said);
    },
  });
  const press = (e: Event) => {
    e.preventDefault();
    status.textContent = '';
    void voice.press();
  };
  const release = () => void voice.release();
  talk.addEventListener('pointerdown', (e) => {
    talk.setPointerCapture(e.pointerId);
    press(e);
  });
  talk.addEventListener('pointerup', release);
  talk.addEventListener('pointercancel', release);
  talk.addEventListener('contextmenu', (e) => e.preventDefault());
  $('say').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $<HTMLInputElement>('say-text');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    status.textContent = '';
    void send({ class: 'intent', type: 'say', text });
  });
  gw.start();
  status.textContent = 'Connecting…';
}

// Emote slots from the robot profile: local when standalone, the visitor's session when connected.
const SLOTS = (JSON.parse(PROFILE_JSON) as { emotes: string[] }).emotes;
SLOTS.forEach((name, slot) => {
  const b = document.createElement('button');
  b.textContent = name.replace(/_/g, ' ');
  b.onclick = () => (connected ? void send({ class: 'perf', type: 'emote', slot }) : performer?.command({ cmd: 'emote', slot }));
  $('emotes').append(b);
});

// ------------------------------------------------------------------ frame loop
function frame() {
  requestAnimationFrame(frame);
  if (!connected && performer) {
    const pt = aimAt(camera.position);
    if (pt) performer.command({ cmd: 'look', pan_tilt: pt });
    const f = performer.tick(clock());
    performer.events(); // drained; standalone has no sfx or speech
    view = { joints: f.joints, eyes: f.eyes, mouth: f.mouth, chest: f.chest, stage: f.stage };
  }
  if (view && rig && leds && chest) {
    rig.apply(new Map(Object.entries(view.joints)));
    leds.update(view.eyes, view.mouth);
    chest.update(view.chest);
  }
  set.lights?.setExternal(view?.stage ?? null);
  controls.update();
  set.constrain(camera, controls.target);
  post.render();
}
requestAnimationFrame(frame);
