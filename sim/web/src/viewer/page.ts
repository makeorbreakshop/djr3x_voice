/**
 * `viewer.html`: Build on its own, for sharing. The same Workbench, Build panels and Instructions
 * as the sim's Build mode (src/workbench/), with nothing else of the sim: no performer, gateway,
 * booth or droid model. It reads published workbench output (manifest.ts MECH_BASE).
 *
 * This file is only the page around them: the renderer and its ground, the two side panels,
 * and the inspector's tabs (the sim's panel.ts does those for every mode; here there is one).
 */

import '../style.css';
import './viewer.css';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PostPipeline } from '../post';
import { SceneLook } from '../scene';
import { mountPanels } from '../layout';
import { Workbench } from '../workbench/workbench';
import { injectBuildDom, mountBuildPanel } from '../workbench/buildpanel';
import { assemblyLabel } from '../workbench/systems';
import { formatView, linkSettings, mayWriteLink, parseView, type ViewState } from './link';
import { mountDiag } from './diag';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

// ------------------------------------------------------------------ renderer / scene
const renderer = new THREE.WebGLRenderer({ antialias: false });
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
$('stage').appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 0.02, 30);
camera.position.set(0.9, 1.3, 2.2);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1, 0);
controls.enableDamping = true;

// The light studio ground Build stands its parts on (the sim's stand-in for the booth).
const look = new SceneLook(scene, renderer, () => {}, () => {});
look.standIn('light');

const post = new PostPipeline(renderer, scene, camera);
post.pacer.listen();
controls.addEventListener('change', () => post.pacer.interact());

/** Centre the view in the space between the two panels (full width on phones). */
const FIT_ASPECT = 0.95;
const fitSize = { w: 0, h: 0 };
function fitView() {
  const w = innerWidth;
  const h = innerHeight;
  const side = (id: string) => (w > 720 ? $(id).offsetWidth + 24 : 0);
  const right = side('panel');
  const left = side('scene-panel');
  document.documentElement.style.setProperty('--panel-space', `${right}px`);
  document.documentElement.style.setProperty('--scene-space', `${left || 12}px`);
  camera.aspect = w / h;
  camera.setViewOffset(w, h, (right - left) / 2, 0, w, h);
  camera.zoom = Math.min(1, (w - right - left) / Math.max(1, h) / FIT_ASPECT);
  camera.updateProjectionMatrix();
  if (w !== fitSize.w || h !== fitSize.h) post.setSize(w, h);
  fitSize.w = w;
  fitSize.h = h;
  post.pacer.interact();
}
addEventListener('resize', fitView);
// A panel sliding open or shut re-fits every frame until it settles.
let fitting = 0;
const fitFrame = () => {
  fitView();
  if (fitting) requestAnimationFrame(fitFrame);
};
for (const id of ['panel', 'scene-panel']) {
  const el = $(id);
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

// ------------------------------------------------------------------ panels
injectBuildDom();
viewerDom();
const panels = mountPanels(fitView);

// A rail icon opens the Build panel at its section.
document.querySelectorAll<HTMLButtonElement>('#scene-panel [data-scene-open]').forEach((b) => {
  b.onclick = () => {
    panels.set('scene', false);
    $(b.dataset.sceneOpen!).scrollIntoView({ block: 'nearest' });
  };
});

// The inspector's tabs: Inspect and BOM (Checks is the builder's: hidden here, viewer.css).
const TAB_KEY = 'r3x.viewer.tab';
function showTab(name: string) {
  document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => {
    const on = b.dataset.tab === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll<HTMLElement>('[data-body]').forEach((el) => (el.hidden = el.dataset.body !== name));
  try {
    localStorage.setItem(TAB_KEY, name);
  } catch {
    /* storage blocked */
  }
}
document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab!)));
{
  let want = 'inspect';
  try {
    want = localStorage.getItem(TAB_KEY) ?? want;
  } catch {
    /* storage blocked */
  }
  showTab(want !== 'checks' && document.querySelector(`[data-tab="${CSS.escape(want)}"]`) ? want : 'inspect');
}

/**
 * The viewer's navigator, rearranged from Build's markup (buildpanel.ts injectBuildDom) without
 * touching it: Library before Our build (the viewer leads with the published designs), and the
 * view controls that sit loose under the lists (the rest of the droid, explode, fasteners, section,
 * frame) folded into one View section. What the viewer hides outright is viewer.css.
 */
function viewerDom() {
  document.body.dataset.viewer = '';
  const lib = $('bv-tab-lib');
  lib.parentElement!.prepend(lib);
  const nav = $('sc-build');
  const view = document.createElement('details');
  view.className = 'bv-sec bv-view';
  view.dataset.sec = 'view';
  view.innerHTML = '<summary>View</summary>';
  const others = nav.querySelector<HTMLElement>(':scope > h3.bv-h');
  if (others) others.textContent = 'Rest of the droid';
  let at = others as Element | null;
  while (at) {
    const next = at.nextElementSibling;
    view.append(at);
    at = next;
  }
  nav.append(view);
  // Copy link: the URL is always the view (see the link section below)
  const copy = document.createElement('button');
  copy.id = 'vb-link';
  copy.className = 'bb-guide vb-link';
  copy.title = 'Copy link';
  copy.setAttribute('aria-label', 'Copy link');
  copy.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 9.5a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2l-.9.9M9.5 6.5a3 3 0 0 0-4.2 0L3 8.8A3 3 0 0 0 7.2 13l.9-.9"/></svg><span aria-live="polite"></span>';
  $('view-bar').append(copy);
}

// ------------------------------------------------------------------ Build
const workbench = new Workbench({
  scene, camera, controls, renderer,
  interact: () => post.pacer.interact(),
  changed: () => post.pacer.touch(),
  setDroidVisible() {},
  quality: () => post.currentQuality,
  setCleanImage: (on) => post.setClean(on),
});
// The first load brings in what the link names first (workbench.ts firstDesign, loadorder.ts): a library
// design's parts, or with no link the shells; the rest of the droid streams in behind as context. A
// system or assembly link waits for the whole build (its parts may be anywhere in the tree).
{
  const v = parseView(location.hash);
  workbench.firstDesign = !v || v.at.kind === 'list' ? '' : v.at.kind === 'lib' ? v.at.id : null;
}
const build = mountBuildPanel(workbench, { viewer: true });
// Dev only: the workbench and renderer on window, for inspecting state from the console.
if (import.meta.env.DEV) Object.assign(window, { __viewer: { workbench, post } });
// What the device sent and what the camera did with it: `?diag` or the ` key (diag.ts).
mountDiag({ renderer, camera, target: () => controls.target, post, workbench });
workbench.setActive(true);
// Build's panels load the index when the page enters Build (buildpanel.ts).
dispatchEvent(new CustomEvent('r3x:mode', { detail: 'build' }));
fitView();
$('loading').hidden = true;

// ------------------------------------------------------------------ what is open
/** Designs with Instructions in the viewer: the ones whose assembly steps are all working. A library
 *  id (systems.ts libraryFrom) or a standalone manifest id (the index). Add to enable more. */
const INSTRUCTIONS = new Set(['hunter', 'hunter_head']);

/** The open design: a library design, or a standalone manifest; null on our build or the list. */
function openDesign(): { id: string; name: string } | null {
  const sc = workbench.scope;
  if (sc?.kind === 'library') return { id: sc.id, name: sc.label };
  const w = build.where();
  if (w.manifest && w.manifest !== w.ourBuild) return { id: w.manifest, name: assemblyLabel(workbench.manifest?.root.name ?? w.manifest) };
  return null;
}

/** The header and the tab title name the open design, with DJ R3X as its context. */
const head = document.querySelector<HTMLElement>('#panel header h1')!;
let headSig: string | null = null;
function renderOpen() {
  const d = openDesign();
  const sig = d ? `${d.id}|${d.name}` : '';
  if (sig === headSig) return;
  headSig = sig;
  head.innerHTML = '';
  if (d) {
    const ctx = document.createElement('small');
    ctx.className = 'vh-ctx';
    ctx.textContent = 'DJ R3X';
    const name = document.createElement('span');
    name.className = 'vh-name';
    name.textContent = d.name;
    head.append(name, ctx);
  } else head.textContent = 'DJ R3X';
  document.title = d ? `${d.name} · DJ R3X` : 'DJ R3X';
  $('bb-guide').classList.toggle('vh-off', !(d && INSTRUCTIONS.has(d.id)));
}
workbench.onChange(renderOpen);
renderOpen();

// ------------------------------------------------------------------ the link
/** The view as it is, for the URL. */
function readView(): ViewState | null {
  const sc = workbench.scope;
  if (sc?.id.startsWith('guide:')) return null; // Instructions borrows the scope: not a view to link
  const w = build.where();
  const d = openDesign();
  const at: ViewState['at'] = sc?.kind === 'library' ? { kind: 'lib', id: sc.id }
    : d ? { kind: 'file', id: d.id }
      : sc?.kind === 'system' ? { kind: 'sys', id: sc.id }
        : sc?.kind === 'assembly' ? { kind: 'asm', id: sc.id }
          : w.list === 'library' ? { kind: 'list' } : { kind: 'build' };
  const ours = at.kind === 'build' || at.kind === 'sys' || at.kind === 'asm';
  const joints: NonNullable<ViewState['joints']> = [];
  workbench.forEachNode((n) => {
    for (const [joint, value] of Object.entries(n.pose)) if (Math.abs(value) > 0.01) joints.push({ node: n.key, joint, value });
  });
  const c = workbench.cameraState();
  return {
    at,
    look: workbench.look,
    ctx: workbench.context,
    explode: workbench.explode,
    fasteners: workbench.fasteners,
    variants: ours ? { ...workbench.variants } : undefined,
    joints,
    cam: at.kind === 'list' ? undefined : [...c.pos.toArray(), ...c.target.toArray()],
  };
}

let restoring = true;
let linkTimer = 0;
let pointersDown = 0;
addEventListener('pointerdown', () => pointersDown++, { capture: true, passive: true });
for (const t of ['pointerup', 'pointercancel']) addEventListener(t, () => (pointersDown = Math.max(0, pointersDown - 1)), { capture: true, passive: true });
function writeLink() {
  if (restoring) return;
  clearTimeout(linkTimer);
  linkTimer = window.setTimeout(() => {
    // Not during a gesture: an address change is a round trip to the browser's own interface, and
    // the drag's pointer events wait behind it (the once-a-second write showed as a stall, then
    // one catch-up move of 30-80 degrees). The address follows once the view has been left alone.
    if (!mayWriteLink(pointersDown, (ms) => post.pacer.settled(ms))) return writeLink();
    const v = readView();
    if (!v) return;
    const url = location.pathname + location.search + formatView(v);
    if (url !== location.pathname + location.search + location.hash) history.replaceState(null, '', url);
  }, 400);
}
workbench.onChange(writeLink);
controls.addEventListener('end', writeLink);
// and once a second: the camera's own moves (a focus flies to its framing) end without an event
setInterval(writeLink, 1000);

const click = (sel: string) => document.querySelector<HTMLButtonElement>(sel)?.click();

/**
 * Open what a link says. What the link does not mention takes its default for that design (rest pose,
 * no explode, fasteners on, the rest of the droid ghosted, the design's own look and framing), never
 * what this browser saved: a link shows everyone the same view. A design that is not there (a stale
 * id) leaves the Library list.
 */
async function restore(v: ViewState) {
  const wb = workbench;
  const at = v.at;
  const toList = async () => {
    await build.openBuild();
    build.showList('library');
  };
  wb.home();
  if (at.kind === 'lib') {
    if (!(await build.openLibrary(at.id))) return toList();
  } else if (at.kind === 'file') {
    if (!(await build.openManifest(at.id))) return toList();
  } else if (at.kind === 'list') {
    await toList();
  } else {
    await build.openBuild();
    for (const [g, id] of Object.entries(v.variants ?? {})) if (wb.variants[g] !== undefined && wb.variants[g] !== id) wb.setVariant(g, id);
    if (at.kind === 'sys') {
      const sc = wb.systemScope(at.id);
      if (sc) wb.setScope(sc);
    } else if (at.kind === 'asm') {
      const n = wb.nodeByKey(at.id);
      if (n) wb.setScope(wb.assemblyScope(n));
    }
  }
  // the look after the focus (a library design brings its own look; our build's default is Exterior)
  const set = linkSettings(v);
  if (set.look) click(`[data-look="${set.look}"]`);
  wb.setContext(set.ctx);
  const x = $<HTMLInputElement>('bv-explode');
  x.value = String(set.explode);
  x.dispatchEvent(new Event('input'));
  if (set.fasteners !== wb.fasteners) click('#bv-fasteners');
  for (const j of set.joints) {
    const n = wb.nodeByKey(j.node);
    if (n?.asm.joints.some((x) => x.id === j.joint)) wb.setJoint(n, j.joint, j.value);
  }
  if (v.cam) {
    // after the focus's own camera flight (to its framing) has landed, so the link's camera is the one that stays
    // (timers, not frames: a page opened in a background tab gets no frames until it is shown)
    const tick = () => new Promise((r) => setTimeout(r, 50));
    await tick();
    for (let k = 0; k < 60 && wb.flying; k++) await tick();
    wb.setCameraState({ pos: new THREE.Vector3(v.cam[0], v.cam[1], v.cam[2]), target: new THREE.Vector3(v.cam[3], v.cam[4], v.cam[5]) });
  }
}

/** Apply the link in the address bar (on load, and when a pasted link changes only the fragment). */
let applying = Promise.resolve();
function applyLink() {
  const v = parseView(location.hash);
  if (!v) return applying;
  applying = applying.then(async () => {
    restoring = true;
    clearTimeout(linkTimer);
    try {
      await restore(v);
    } catch (e) {
      console.warn('viewer: link not restored', e);
    }
    restoring = false;
    renderOpen();
  });
  return applying;
}
void build.ready().then(async () => {
  await applyLink();
  restoring = false;
  // (our own writes are replaceState, which fires no hashchange: only a navigation lands here)
  addEventListener('hashchange', () => void applyLink());
});

// Copy link: the current URL (always the view), confirmed on the button for a moment.
$('vb-link').onclick = async () => {
  clearTimeout(linkTimer);
  const v = readView();
  if (v) history.replaceState(null, '', location.pathname + location.search + formatView(v));
  const label = $('vb-link').querySelector('span')!;
  try {
    await navigator.clipboard.writeText(location.href);
    label.textContent = 'Copied';
  } catch {
    label.textContent = 'Copy failed';
  }
  setTimeout(() => (label.textContent = ''), 1400);
};

function frame() {
  requestAnimationFrame(frame);
  workbench.tick();
  controls.update();
  post.render();
}
requestAnimationFrame(frame);
