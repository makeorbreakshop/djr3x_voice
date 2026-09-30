import * as THREE from 'three';
import type { PostPipeline, ToneMap } from './post';
import type { FaceLeds } from './leds';
import type { ChestLights } from './chestlights';
import './rendering.css';

/**
 * The viewer's own rendering and lighting levels (Scene -> Lighting, Scene -> Rendering).
 *
 * View-only, like the rest of the Scene panel: nothing here reaches the robot, and every value
 * is this browser's (localStorage `r3x.render`, try/catch). The values layer over the Quality
 * level (post.ts): Quality decides what the machine can afford (scale, AO sampling, bloom
 * resolution, frame rates); these decide how the picture is lit and graded.
 *
 * Every control maps to one real parameter:
 * - exposure, tone mapper, bloom, AO, environment: post.ts;
 * - shadows: castShadow and shadow.radius on the shadow-casting lights;
 * - work light / key / fill / rim / stage desk: light.intensity over the level each light was
 *   built with (booth.ts names its fixtures by desk group; scene.ts names the work light's).
 *   The desk writes colour x dimmer into light.color, never intensity, so the two compose.
 *   Key, fill and rim scale the character lights of whichever set is showing: the booth's
 *   droid_key / fill / head rims, and the work light's key / fill / rim;
 * - LED glow: per group (eyes, mouth, body = chest logic panels), a level that scales all the
 *   light the group emits, and a glow amount that scales only its halo (the face's glow
 *   sprites; for the chest, the part of each pixel above 1.0 that feeds the bloom), so the
 *   LEDs can be dimmed or de-haloed without touching bloom anywhere else.
 *
 * At the defaults nothing is scaled per frame and no hook is installed: the controls cost
 * nothing until they are moved.
 */

export interface RenderValues {
  exposure: number;
  toneMap: ToneMap;
  bloom: boolean;
  bloomStrength: number;
  bloomThreshold: number;
  bloomRadius: number;
  ao: boolean;
  aoIntensity: number;
  shadows: boolean;
  shadowSoftness: number;
  env: number;
  work: number;
  workTemp: number;
  key: number;
  fill: number;
  rim: number;
  desk: number;
  eyes: number;
  eyesGlow: number;
  mouth: number;
  mouthGlow: number;
  body: number;
  bodyGlow: number;
}

export const DEFAULT_VALUES: RenderValues = {
  exposure: 1, toneMap: 'neutral',
  bloom: true, bloomStrength: 0.6, bloomThreshold: 1, bloomRadius: 0.3,
  ao: true, aoIntensity: 4,
  shadows: true, shadowSoftness: 1,
  env: 1, work: 1, workTemp: 5000, key: 1, fill: 1, rim: 1, desk: 1,
  eyes: 1, eyesGlow: 1, mouth: 1, mouthGlow: 1, body: 1, bodyGlow: 1,
};

export type PresetName = 'default' | 'photo' | 'flat' | 'performance';

/**
 * Photo match: the StarWars.com Oga's Cantina still - warm tungsten, a touch hotter overall,
 * softer contact shadows, eyes reading yellow-white with a tight glow. Flat: even, shadowless,
 * no bloom or AO, LEDs dimmed and de-haloed, for reading parts (Build, Bench). Performance:
 * the costly effects off (AO, shadows), the look otherwise unchanged.
 */
export const PRESETS: Record<PresetName, { label: string; values: RenderValues }> = {
  default: { label: 'Default', values: DEFAULT_VALUES },
  photo: {
    label: 'Photo match',
    values: {
      ...DEFAULT_VALUES, exposure: 1.1, bloomStrength: 0.7, bloomRadius: 0.35, aoIntensity: 5, shadowSoftness: 1.6,
      env: 0.8, workTemp: 3400, key: 1.2, fill: 0.7, rim: 1.15, desk: 1.2, eyes: 1.25, eyesGlow: 1.3, body: 1.1,
    },
  },
  flat: {
    label: 'Flat / inspection',
    values: {
      ...DEFAULT_VALUES, bloom: false, ao: false, shadows: false, env: 1.8, workTemp: 5600,
      key: 0.6, fill: 2, rim: 0.5, desk: 0.6, eyes: 0.6, eyesGlow: 0, mouth: 0.6, mouthGlow: 0, body: 0.7, bodyGlow: 0,
    },
  },
  performance: {
    label: 'Performance',
    values: { ...DEFAULT_VALUES, ao: false, shadows: false },
  },
};

const KEY = 'r3x.render';

/** Collapsible parts of the panel, each remembered: the two sections, Advanced, LED halo. */
type Disclosure = 'light' | 'render' | 'adv' | 'halo';

interface Stored {
  values: Partial<RenderValues>;
  open: Record<Disclosure, boolean>;
}

function load(): Stored {
  const d: Stored = { values: {}, open: { light: false, render: false, adv: false, halo: false } };
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Stored>;
    return { values: { ...(s.values ?? {}) }, open: { ...d.open, ...(s.open ?? {}) } };
  } catch {
    return d;
  }
}

/** Stored values, checked against the defaults' types (a stale or hand-edited entry falls back). */
function sanitize(v: Partial<RenderValues>): RenderValues {
  const out = { ...DEFAULT_VALUES };
  for (const k of Object.keys(DEFAULT_VALUES) as (keyof RenderValues)[]) {
    const x = v[k];
    if (typeof x === typeof DEFAULT_VALUES[k] && (typeof x !== 'number' || Number.isFinite(x))) (out as Record<string, unknown>)[k] = x;
  }
  if (!['neutral', 'agx', 'aces'].includes(out.toneMap)) out.toneMap = 'neutral';
  return out;
}

// ------------------------------------------------------------------ colour temperature

/** Blackbody colour (Tanner Helland's fit), linear RGB. */
function kelvin(k: number, out = new THREE.Color()): THREE.Color {
  const t = k / 100;
  const cl = (x: number) => Math.min(255, Math.max(0, x)) / 255;
  let r: number, g: number, b: number;
  if (t <= 66) {
    r = 255;
    g = 99.4708025861 * Math.log(t) - 161.1195681661;
    b = t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  } else {
    r = 329.698727446 * Math.pow(t - 60, -0.1332047592);
    g = 288.1221695283 * Math.pow(t - 60, -0.0755148492);
    b = 255;
  }
  return out.setRGB(cl(r), cl(g), cl(b), THREE.SRGBColorSpace);
}

/** Multiplier that moves a light built at 5000 K to `k`, at the same luminance. */
function tint(k: number): THREE.Color {
  const ref = kelvin(5000);
  const c = kelvin(k);
  c.setRGB(c.r / ref.r, c.g / ref.g, c.b / ref.b);
  const l = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  return c.multiplyScalar(1 / l);
}

// ------------------------------------------------------------------ lights

const GROUP_LIGHTS: Record<'key' | 'fill' | 'rim' | 'desk', string[]> = {
  key: ['droid_key', 'work_key'],
  fill: ['fill', 'work_fill'],
  rim: ['head_rim_l', 'head_rim_r', 'work_rim'],
  desk: ['back_uplight', 'wall_wash_l', 'wall_wash_r', 'ceiling_down'],
};
const WORK = new Set(['work_key', 'work_fill', 'work_rim']);

type ShadowLight = THREE.Light & { shadow?: THREE.LightShadow };

interface Tracked {
  light: ShadowLight;
  intensity: number;
  color: THREE.Color;
  shadowRadius: number | null;
}

export class RenderSettings {
  values: RenderValues;
  private readonly lights = new Map<string, Tracked[]>();
  private readonly casters: Tracked[] = [];
  private leds: FaceLeds | null = null;
  private chestMats: THREE.MeshBasicMaterial[] | null = null;
  private readonly listeners: (() => void)[] = [];
  private readonly stored: Stored;

  constructor(private readonly post: PostPipeline, scene: THREE.Scene) {
    this.stored = load();
    this.values = sanitize(this.stored.values);
    scene.traverse((o) => {
      const l = o as ShadowLight;
      if (!l.isLight) return;
      const t: Tracked = {
        light: l, intensity: l.intensity, color: l.color.clone(),
        shadowRadius: l.castShadow && l.shadow ? l.shadow.radius : null,
      };
      if (l.name) (this.lights.get(l.name) ?? this.lights.set(l.name, []).get(l.name)!).push(t);
      if (t.shadowRadius !== null) this.casters.push(t);
    });
    this.apply();
  }

  /** The face and chest LEDs, once the model has loaded (main.ts). */
  attachLeds(leds: FaceLeds, chest: ChestLights) {
    this.leds = leds;
    // ChestLights writes each pixel's colour on update; the body level and glow reshape it
    // right after, only while they are off their defaults.
    this.chestMats = (chest as unknown as { mats: THREE.MeshBasicMaterial[] }).mats ?? null;
    const update = chest.update.bind(chest);
    chest.update = (px) => {
      update(px);
      const { body, bodyGlow } = this.values;
      if ((body === 1 && bodyGlow === 1) || !this.chestMats) return;
      for (const m of this.chestMats) {
        const c = m.color;
        const f = (x: number) => (Math.min(x, 1) + Math.max(x - 1, 0) * bodyGlow) * body;
        c.setRGB(f(c.r), f(c.g), f(c.b));
      }
    };
    this.applyLeds();
  }

  onChange(fn: () => void) {
    this.listeners.push(fn);
  }

  /** The preset whose values these are, if any. */
  get preset(): PresetName | null {
    for (const [name, p] of Object.entries(PRESETS) as [PresetName, (typeof PRESETS)[PresetName]][]) {
      if ((Object.keys(p.values) as (keyof RenderValues)[]).every((k) => p.values[k] === this.values[k])) return name;
    }
    return null;
  }

  set(patch: Partial<RenderValues>) {
    this.values = { ...this.values, ...patch };
    this.save();
    this.apply();
  }

  usePreset(name: PresetName) {
    this.values = { ...PRESETS[name].values };
    this.save();
    this.apply();
  }

  get openSections() {
    return this.stored.open;
  }

  setOpen(which: Disclosure, open: boolean) {
    this.stored.open[which] = open;
    this.save();
  }

  private save() {
    this.stored.values = this.values;
    try {
      localStorage.setItem(KEY, JSON.stringify(this.stored));
    } catch {
      /* storage blocked: lasts this page load */
    }
  }

  private scale(name: string, k: number) {
    for (const t of this.lights.get(name) ?? []) t.light.intensity = t.intensity * k;
  }

  private apply() {
    const v = this.values;
    this.post.setTone(v.toneMap, v.exposure);
    this.post.setBloom(v.bloom, v.bloomStrength, v.bloomThreshold, v.bloomRadius);
    this.post.setAO(v.ao, v.aoIntensity);
    this.post.envScale = v.env;

    for (const [group, names] of Object.entries(GROUP_LIGHTS) as [keyof typeof GROUP_LIGHTS, string[]][]) {
      for (const n of names) this.scale(n, v[group] * (WORK.has(n) ? v.work : 1));
    }
    const tn = tint(v.workTemp);
    for (const n of WORK) {
      for (const t of this.lights.get(n) ?? []) t.light.color.copy(t.color).multiply(tn);
    }
    for (const t of this.casters) {
      // castShadow changes the lights' shader programs: touch it only when it changes.
      if (t.light.castShadow !== v.shadows) t.light.castShadow = v.shadows;
      if (t.light.shadow) t.light.shadow.radius = (t.shadowRadius ?? 1) * v.shadowSoftness;
    }
    this.applyLeds();
    this.post.pacer.touch();
    for (const fn of this.listeners) fn();
  }

  private applyLeds() {
    const v = this.values;
    this.leds?.setLevels('eyes', v.eyes, v.eyesGlow);
    this.leds?.setLevels('mouth', v.mouth, v.mouthGlow);
  }
}

// ------------------------------------------------------------------ the panel

/** Up to two decimals, no trailing zeros: 1, 0.6, 1.25. */
const num = (x: number) => String(Math.round(x * 100) / 100);

interface Slider {
  k: keyof RenderValues;
  label: string;
  min: number;
  max: number;
  step: number;
  fmt?: (x: number) => string;
  title?: string;
  /** The on/off this slider belongs to, drawn as a checkbox in front of its label. */
  toggle?: keyof RenderValues;
}

const TONE_LABEL: Record<ToneMap, string> = { neutral: 'Neutral', agx: 'AgX', aces: 'ACES' };
const PRESET_SHORT: Record<PresetName, string> = { default: 'Default', photo: 'Photo', flat: 'Flat', performance: 'Perf' };

const svg = (d: string) => `<svg viewBox="0 0 16 16" aria-hidden="true">${d}</svg>`;
const ICON_LIGHT = svg('<path d="M6 12.5h4M6.5 14.5h3" /><path d="M8 1.5a4.2 4.2 0 0 0-2.5 7.6c.6.5.9 1.1.9 1.9h3.2c0-.8.3-1.4.9-1.9A4.2 4.2 0 0 0 8 1.5z" />');
const ICON_RENDER = svg('<circle cx="8" cy="8" r="6" /><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor" stroke="none" />');

/**
 * Build the Lighting (after Environment) and Rendering (after Performance) sections and their
 * rail icons. Both collapse (closed by default). Rendering shows the look presets and the few
 * switches people reach for; the tuning knobs sit under Advanced, LED halos under Halo. Every
 * explanation is a tooltip. Double-click a slider for its default.
 */
export function mountRenderPanel(rs: RenderSettings, isBooth: () => boolean, hasAO: () => boolean, workOn: () => boolean) {
  const body = document.getElementById('scene-body');
  if (!body) return;
  const inputs = new Map<keyof RenderValues, { input: HTMLInputElement | HTMLSelectElement; out?: HTMLOutputElement; row: HTMLElement; fmt: (x: number) => string }>();

  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', html = '') => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html) e.innerHTML = html;
    return e;
  };

  const check = (k: keyof RenderValues, label: string, title = '') => {
    const lab = el('label', 'rs-check');
    lab.innerHTML = `<input type="checkbox" id="rs-${k}" /><span>${label}</span>`;
    if (title) lab.title = title;
    const input = lab.querySelector('input')!;
    input.onchange = () => rs.set({ [k]: input.checked } as Partial<RenderValues>);
    inputs.set(k, { input, row: lab, fmt: String });
    return lab;
  };

  const slider = (s: Slider, parent: HTMLElement) => {
    const id = `rs-${s.k}`;
    const row = el('div', 'rs-row');
    if (s.title) row.title = s.title;
    row.append(s.toggle ? check(s.toggle, s.label) : el('label', '', s.label));
    if (!s.toggle) (row.firstElementChild as HTMLLabelElement).htmlFor = id;
    const input = el('input');
    input.type = 'range';
    input.id = id;
    Object.assign(input, { min: String(s.min), max: String(s.max), step: String(s.step) });
    if (s.toggle) input.setAttribute('aria-label', `${s.label} strength`);
    const out = el('output');
    out.htmlFor.add(id);
    row.append(input, out);
    input.oninput = () => rs.set({ [s.k]: Number(input.value) } as Partial<RenderValues>);
    input.ondblclick = () => rs.set({ [s.k]: DEFAULT_VALUES[s.k] } as Partial<RenderValues>);
    inputs.set(s.k, { input, out, row, fmt: s.fmt ?? num });
    parent.append(row);
    return row;
  };

  const disclosure = (which: Disclosure, cls: string, summary: string, title = '') => {
    const det = el('details', cls);
    det.open = rs.openSections[which];
    det.innerHTML = `<summary${title ? ` title="${title}"` : ''}>${summary}</summary>`;
    det.ontoggle = () => rs.setOpen(which, det.open);
    const inner = el('div', 'rs-body');
    det.append(inner);
    return { det, inner };
  };

  const section = (id: string, which: Disclosure, title: string, tip: string) => {
    const sec = el('section', 'rs-section');
    sec.id = id;
    const d = disclosure(which, '', `<h2>${title}</h2>`, tip);
    sec.append(d.det);
    return { sec, inner: d.inner };
  };

  // ---- Lighting
  const light = section('sc-light', 'light', 'Lighting', 'Light levels for this view only; not sent to R3X');
  slider({ k: 'env', label: 'Ambient', min: 0, max: 3, step: 0.05, title: 'Environment: ambient light and reflections' }, light.inner);
  const workRows = [
    slider({ k: 'work', label: 'Work light', min: 0, max: 3, step: 0.05, title: 'Key, fill and rim of the work light together' }, light.inner),
    slider({ k: 'workTemp', label: 'Warmth', min: 2700, max: 8000, step: 100, fmt: (x) => `${Math.round(x)}K`, title: 'Work light colour temperature; 5000 K as built' }, light.inner),
  ];
  slider({ k: 'key', label: 'Key', min: 0, max: 3, step: 0.05, title: 'Main light on R3X (booth key or work light key)' }, light.inner);
  slider({ k: 'fill', label: 'Fill', min: 0, max: 3, step: 0.05, title: 'Shadow-side light (booth fill or work light fill)' }, light.inner);
  slider({ k: 'rim', label: 'Rim', min: 0, max: 3, step: 0.05, title: 'Edge light from behind (booth head rims or work light rim)' }, light.inner);
  const deskRow = slider({ k: 'desk', label: 'Stage desk', min: 0, max: 3, step: 0.05, title: 'The booth fixtures the light desk drives (washes, uplight, ceiling spot)' }, light.inner);

  // ---- Rendering
  const render = section('sc-render', 'render', 'Rendering', 'How this browser draws R3X; not sent to the robot. Layers over Quality.');
  const seg = el('div', 'row seg rs-seg');
  seg.setAttribute('role', 'group');
  seg.setAttribute('aria-label', 'Look preset');
  const presetBtns = new Map<PresetName, HTMLButtonElement>();
  for (const [name, p] of Object.entries(PRESETS) as [PresetName, (typeof PRESETS)[PresetName]][]) {
    const b = el('button', '', PRESET_SHORT[name]);
    b.title = p.label;
    b.onclick = () => {
      rs.usePreset(name);
      b.blur();
    };
    presetBtns.set(name, b);
    seg.append(b);
  }
  const custom = el('div', 'rs-custom', '<span>Custom</span><button type="button" title="Back to the Default look">Reset</button>');
  custom.querySelector('button')!.onclick = (e) => {
    rs.usePreset('default');
    (e.currentTarget as HTMLElement).blur();
  };
  render.inner.append(seg, custom);
  slider({ k: 'exposure', label: 'Exposure', min: 0.3, max: 2.5, step: 0.05 }, render.inner);
  slider({ k: 'bloomStrength', label: 'Bloom', toggle: 'bloom', min: 0, max: 2, step: 0.05, title: 'Glow around the LEDs' }, render.inner);
  const toggles = el('div', 'rs-toggles');
  toggles.append(check('ao', 'AO', 'Ambient occlusion: contact darkening in seams and gaps'), check('shadows', 'Shadows'));
  render.inner.append(toggles);

  const adv = disclosure('adv', 'rs-disc', 'Advanced');
  const toneRow = el('div', 'rs-row', `<label for="rs-toneMap">Tone map</label><select id="rs-toneMap">${(Object.keys(TONE_LABEL) as ToneMap[])
    .map((t) => `<option value="${t}">${TONE_LABEL[t]}</option>`).join('')}</select>`);
  const toneSel = toneRow.querySelector('select')!;
  toneSel.onchange = () => {
    rs.set({ toneMap: toneSel.value as ToneMap });
    toneSel.blur();
  };
  inputs.set('toneMap', { input: toneSel, row: toneRow, fmt: String });
  adv.inner.append(toneRow);
  slider({ k: 'bloomThreshold', label: 'Threshold', min: 0.5, max: 3, step: 0.05, title: 'Bloom threshold: 1 = only the LEDs glow' }, adv.inner);
  slider({ k: 'bloomRadius', label: 'Radius', min: 0, max: 1, step: 0.05, title: 'Bloom radius' }, adv.inner);
  slider({ k: 'aoIntensity', label: 'AO depth', min: 0, max: 10, step: 0.25, title: 'Ambient occlusion intensity' }, adv.inner);
  slider({ k: 'shadowSoftness', label: 'Softness', min: 0, max: 4, step: 0.1, title: 'Shadow edge softness' }, adv.inner);
  render.inner.append(adv.det);

  render.inner.append(el('h3', 'rs-h', 'LED glow'));
  slider({ k: 'eyes', label: 'Eyes', min: 0, max: 2, step: 0.05, title: 'All the light the eyes emit' }, render.inner);
  slider({ k: 'mouth', label: 'Mouth', min: 0, max: 2, step: 0.05 }, render.inner);
  slider({ k: 'body', label: 'Body', min: 0, max: 2, step: 0.05, title: 'Chest logic panel LEDs' }, render.inner);
  const halo = disclosure('halo', 'rs-disc', 'Halo', 'The glow round each LED group only: not the lit parts, not bloom elsewhere');
  slider({ k: 'eyesGlow', label: 'Eyes', min: 0, max: 3, step: 0.05 }, halo.inner);
  slider({ k: 'mouthGlow', label: 'Mouth', min: 0, max: 3, step: 0.05 }, halo.inner);
  slider({ k: 'bodyGlow', label: 'Body', min: 0, max: 3, step: 0.05 }, halo.inner);
  render.inner.append(halo.det);

  document.getElementById('sc-env')?.after(light.sec);
  document.getElementById('sc-perf')?.after(render.sec);

  // Rail icons, in section order.
  const rail = document.querySelector('#scene-panel .rail');
  const railBtn = (target: string, label: string, icon: string, after: string) => {
    const b = el('button', '', icon);
    b.dataset.sceneOpen = target;
    b.setAttribute('aria-label', label);
    b.title = label;
    rail?.querySelector(`[data-scene-open="${after}"]`)?.after(b);
  };
  railBtn('sc-light', 'Lighting', ICON_LIGHT, 'sc-env');
  railBtn('sc-render', 'Rendering', ICON_RENDER, 'sc-perf');

  const sync = () => {
    const v = rs.values;
    for (const [k, { input, out, fmt }] of inputs) {
      const val = v[k];
      if (input instanceof HTMLInputElement && input.type === 'checkbox') input.checked = !!val;
      else if (document.activeElement !== input || input instanceof HTMLSelectElement) input.value = String(val);
      if (out) out.textContent = fmt(val as number);
    }
    const dim = (keys: (keyof RenderValues)[], off: boolean) => keys.forEach((k) => {
      const e = inputs.get(k);
      if (!e) return;
      e.input.disabled = off;
      e.row.classList.toggle('off', off);
    });
    dim(['bloomStrength', 'bloomThreshold', 'bloomRadius'], !v.bloom);
    const aoOk = hasAO();
    (inputs.get('ao')!.input as HTMLInputElement).disabled = !aoOk;
    inputs.get('ao')!.row.title = aoOk ? 'Ambient occlusion: contact darkening in seams and gaps' : 'Performance quality renders without AO';
    dim(['aoIntensity'], !v.ao || !aoOk);
    dim(['shadowSoftness'], !v.shadows);
    deskRow.hidden = !isBooth();
    for (const r of workRows) r.hidden = !workOn();
    const p = rs.preset;
    for (const [name, b] of presetBtns) b.setAttribute('aria-pressed', String(name === p));
    custom.hidden = p !== null;
  };
  rs.onChange(sync);
  // Backdrop (the desk needs the booth), work light and quality (Performance has no AO).
  for (const id of ['quality', 'scene-bg']) document.getElementById(id)?.addEventListener('change', () => setTimeout(sync));
  document.getElementById('scene-work')?.addEventListener('click', () => setTimeout(sync));
  sync();
  return { sync };
}
