/**
 * The viewer's input and frame diagnostic: what the device sent, what the camera did with it, and how
 * the frames came. For "it feels off" reports that a frame-time number does not explain.
 *
 * On with `?diag` in the address, or the ` key. A small readout over the view, and a log
 * (`window.__diag.dump()`, or Copy) of every wheel and pointer event as it arrived, how the wheel was
 * read (workbench/navigate.ts: mouse or trackpad, so zoom or pan), each frame's interval and whether it
 * was drawn, and what the camera did in that frame (turned, moved its target, changed distance).
 *
 * It flags two things: a frame where the camera moved with no input behind it, and a frame whose
 * camera step is many times the gesture's usual one (a jump). It only listens (capture phase, passive)
 * and reads; nothing here changes how the viewer behaves.
 */

import * as THREE from 'three';
import type { PostPipeline } from '../post';
import { WheelClassifier, dragAction, platformOf, wheelDevice } from '../workbench/navigate';
import type { Workbench } from '../workbench/workbench';

interface Host {
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  target: () => THREE.Vector3;
  post: PostPipeline;
  workbench: Workbench;
}

type Entry = Record<string, number | string | boolean>;
const MAX = 6000;
const r2 = (v: number) => Math.round(v * 100) / 100;
const r4 = (v: number) => Math.round(v * 1e4) / 1e4;

/**
 * One frame's camera step, classified against the gesture's recent steps (updated in place): '' (an
 * ordinary step, or none), 'unprompted' (the view moved with no input for 20+ frames: an ease or a fly-to
 * is expected for a few frames after a wheel notch, a double-click or the view cube; later than that is
 * not) or 'jump' (many times the gesture's usual step). A move is a step over 0.02; 30 quiet frames
 * forget the gesture.
 */
export function classifyStep(steps: number[], step: number, quietFrames: number): '' | 'unprompted' | 'jump' {
  let flag: '' | 'unprompted' | 'jump' = '';
  if (step > 0.02) {
    const sorted = steps.slice().sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1] ?? 0;
    if (quietFrames > 20) flag = 'unprompted';
    else if (steps.length >= 8 && median > 0.05 && step > median * 6 && step > 2) flag = 'jump';
    steps.push(step);
    if (steps.length > 30) steps.shift();
  } else if (quietFrames > 30) steps.length = 0;
  return flag;
}

export function mountDiag(h: Host) {
  const el = h.renderer.domElement;
  const platform = platformOf(navigator.platform || navigator.userAgent);
  // A mirror of the navigator's classifier (same setting, same events in the same order), so reading
  // an event here never disturbs the real one's gesture state.
  const mirror = new WheelClassifier(h.workbench.nav.wheel.setting, 220, platform);
  const t0 = performance.now();
  const now = () => Math.round(performance.now() - t0);
  const log: Entry[] = [];
  const push = (e: Entry) => {
    log.push(e);
    if (log.length > MAX) log.splice(0, log.length - MAX);
  };

  // ------------------------------------------------------------------ input, as it arrives
  let lastInput = '';
  let sinceFrame = { wheel: 0, move: 0, moveDx: 0, moveDy: 0, down: 0 };
  let buttonsDown = 0;
  el.addEventListener('wheel', (e) => {
    mirror.setting = h.workbench.nav.wheel.setting;
    const device = wheelDevice(e, platform);
    const action = mirror.classify(e, performance.now());
    sinceFrame.wheel++;
    lastInput = `wheel dX ${r2(e.deltaX)} dY ${r2(e.deltaY)} mode ${e.deltaMode}${e.ctrlKey ? ' ctrl' : ''}${e.shiftKey ? ' shift' : ''} → ${action} (${device})`;
    push({ t: now(), k: 'wheel', dx: r4(e.deltaX), dy: r4(e.deltaY), mode: e.deltaMode, ctrl: e.ctrlKey, shift: e.shiftKey, device, action });
  }, { capture: true, passive: true });
  for (const type of ['pointerdown', 'pointerup', 'pointercancel'] as const) {
    el.addEventListener(type, (e) => {
      buttonsDown = e.buttons;
      const action = type === 'pointerdown' ? (e.pointerType === 'touch' ? 'orbit' : dragAction(e, platform)) : '';
      if (type === 'pointerdown') sinceFrame.down++;
      lastInput = `${type.slice(7)} ${e.pointerType} button ${e.button}${e.shiftKey ? ' shift' : ''}${e.metaKey ? ' cmd' : ''}${e.ctrlKey ? ' ctrl' : ''}${action ? ` → ${action}` : ''}`;
      push({ t: now(), k: type.slice(7), type: e.pointerType, button: e.button, buttons: e.buttons, shift: e.shiftKey, meta: e.metaKey, ctrl: e.ctrlKey, action, x: Math.round(e.clientX), y: Math.round(e.clientY) });
    }, { capture: true, passive: true });
  }
  el.addEventListener('pointermove', (e) => {
    buttonsDown = e.buttons;
    sinceFrame.move += e.getCoalescedEvents?.().length || 1;
    sinceFrame.moveDx += e.movementX;
    sinceFrame.moveDy += e.movementY;
  }, { capture: true, passive: true });

  // long main-thread tasks (a freeze the frame intervals also show, with its length)
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) push({ t: Math.round(e.startTime - t0), k: 'longtask', ms: Math.round(e.duration) });
    }).observe({ entryTypes: ['longtask'] });
  } catch { /* not supported: the frame intervals still show a freeze */ }

  // ------------------------------------------------------------------ frames and the camera
  const prev = { pos: h.camera.position.clone(), target: h.target().clone(), quat: h.camera.quaternion.clone(), frame: h.renderer.info.render.frame };
  let last = performance.now();
  const recent: { dt: number; drawn: boolean }[] = [];
  const steps: number[] = []; // the gesture's recent camera steps, for the jump test
  let jumps = 0;
  let unprompted = 0;
  let quietFrames = 0; // frames since the last input

  function frame() {
    requestAnimationFrame(frame);
    const tNow = performance.now();
    const dt = tNow - last;
    last = tNow;
    const info = h.renderer.info.render;
    const drawn = info.frame !== prev.frame;
    prev.frame = info.frame;
    const target = h.target();
    const turn = THREE.MathUtils.radToDeg(prev.quat.angleTo(h.camera.quaternion));
    const pan = prev.target.distanceTo(target) * 1000; // mm
    const d0 = prev.pos.distanceTo(prev.target);
    const d1 = h.camera.position.distanceTo(target);
    const zoom = d0 > 0 ? d1 / d0 : 1;
    prev.pos.copy(h.camera.position);
    prev.target.copy(target);
    prev.quat.copy(h.camera.quaternion);

    const input = sinceFrame.wheel + sinceFrame.move + sinceFrame.down > 0 || buttonsDown !== 0;
    quietFrames = input ? 0 : quietFrames + 1;
    // one number for "how far the view went this frame": degrees turned, plus pan and zoom on the same scale
    const step = turn + pan / (d1 * 10 || 1) + Math.abs(Math.log(zoom)) * 60;
    const moved = step > 0.02;
    const flag = classifyStep(steps, step, quietFrames);
    if (flag === 'unprompted') unprompted++;
    else if (flag === 'jump') jumps++;

    recent.push({ dt, drawn });
    if (recent.length > 180) recent.shift();
    if (moved || drawn || dt > 34 || sinceFrame.wheel || sinceFrame.move) {
      const e: Entry = { t: now(), k: 'frame', dt: r2(dt), drawn, scale: r2(h.renderer.getPixelRatio()), focus: document.hasFocus() };
      if (sinceFrame.move) Object.assign(e, { moves: sinceFrame.move, mdx: Math.round(sinceFrame.moveDx), mdy: Math.round(sinceFrame.moveDy) });
      if (sinceFrame.wheel) e.wheels = sinceFrame.wheel;
      if (moved) Object.assign(e, { turn: r2(turn), pan: r2(pan), zoom: r4(zoom) });
      if (flag) e.flag = flag;
      push(e);
    }
    sinceFrame = { wheel: 0, move: 0, moveDx: 0, moveDy: 0, down: 0 };
    if (shown && ++paint % 6 === 0) draw(d1);
  }

  // ------------------------------------------------------------------ the readout
  const box = document.createElement('div');
  box.id = 'viewer-diag';
  box.innerHTML = '<pre></pre><div class="vd-row"><button data-vd="clear">Clear</button><button data-vd="copy">Copy log</button><span class="vd-note"></span></div>';
  box.hidden = true;
  document.body.appendChild(box);
  const pre = box.querySelector('pre')!;
  const note = box.querySelector<HTMLElement>('.vd-note')!;
  let shown = false;
  let paint = 0;

  function draw(dist: number) {
    const dts = recent.map((r) => r.dt).sort((a, b) => a - b);
    const q = (p: number) => (dts.length ? dts[Math.min(dts.length - 1, Math.floor(dts.length * p))] : 0);
    const span = recent.reduce((s, r) => s + r.dt, 0) / 1000 || 1;
    const drawnN = recent.filter((r) => r.drawn).length;
    const size = h.renderer.getDrawingBufferSize(new THREE.Vector2());
    pre.textContent = [
      `frames  ${(recent.length / span).toFixed(0)}/s  drawn ${(drawnN / span).toFixed(0)}/s  interval p50 ${q(0.5).toFixed(1)}  p95 ${q(0.95).toFixed(1)}  max ${q(1).toFixed(0)} ms`,
      `canvas  ${size.x}x${size.y}  scale ${h.renderer.getPixelRatio().toFixed(2)}  window ${document.hasFocus() ? 'focused' : 'not focused'}  page ${document.visibilityState}`,
      `camera  distance ${dist.toFixed(3)} m   scroll reads as: ${h.workbench.nav.wheel.setting}   platform ${platform}`,
      `input   ${lastInput || '(none yet)'}`,
      `flags   jumps ${jumps}   moved with no input ${unprompted}   log ${log.length}`,
    ].join('\n');
  }

  const dump = () => ({
    at: new Date().toISOString(), url: location.href, platform, ua: navigator.userAgent, dpr: devicePixelRatio,
    window: [innerWidth, innerHeight], scrollSetting: h.workbench.nav.wheel.setting, jumps, unprompted, log,
  });
  box.addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('[data-vd]');
    if (!b) return;
    if (b.dataset.vd === 'clear') {
      log.length = 0;
      jumps = unprompted = 0;
      note.textContent = 'cleared';
    } else {
      void navigator.clipboard.writeText(JSON.stringify(dump())).then(() => (note.textContent = 'copied'), () => (note.textContent = 'copy blocked'));
    }
  });

  const show = (on: boolean) => {
    shown = on;
    box.hidden = !on;
  };
  addEventListener('keydown', (e) => {
    const t = e.target as HTMLElement;
    if (e.key !== '`' || e.metaKey || e.ctrlKey || e.altKey || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') return;
    show(!shown);
  });
  if (new URLSearchParams(location.search).has('diag')) show(true);
  Object.assign(window, { __diag: { dump, show, clear: () => { log.length = 0; jumps = unprompted = 0; } } });
  requestAnimationFrame(frame);
}
