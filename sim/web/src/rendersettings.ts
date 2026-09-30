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

interface Stored {
  values: Partial<RenderValues>;
  open: { light: boolean; render: boolean };
}

function load(): Stored {
  const d: Stored = { values: {}, open: { light: false, render: false } };
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

  setOpen(which: 'light' | 'render', open: boolean) {
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

interface Slider {
  k: keyof RenderValues;
  label: string;
  min: number;
  max: number;
  step: number;
  fmt?: (x: number) => string;
  title?: string;
}

const x2 = (x: number) => x.toFixed(2);
const times = (x: number) => `${x.toFixed(2)}x`;

const LIGHTING: (Slider | string)[] = [
  { k: 'env', label: 'Environment', min: 0, max: 3, step: 0.05, fmt: times, title: 'Image-based ambient light and reflections, over what the backdrop or the booth desk sets' },
  'Work light',
  { k: 'work', label: 'Intensity', min: 0, max: 3, step: 0.05, fmt: times, title: 'The whole work light (key, fill and rim); turn it on under Environment' },
  { k: 'workTemp', label: 'Colour temp', min: 2700, max: 8000, step: 100, fmt: (x) => `${Math.round(x)}K`, title: 'Warm (tungsten) to cool (daylight); 5000 K is as built' },
  'Character lights',
  { k: 'key', label: 'Key', min: 0, max: 3, step: 0.05, fmt: times, title: 'The main light on R3X: the booth key, or the work light key' },
  { k: 'fill', label: 'Fill', min: 0, max: 3, step: 0.05, fmt: times, title: 'Softens the shadow side: the booth fill, or the work light fill' },
  { k: 'rim', label: 'Rim', min: 0, max: 3, step: 0.05, fmt: times, title: 'Edge light from behind: the booth head rims, or the work light rim' },
  'Stage',
  { k: 'desk', label: 'Light desk', min: 0, max: 3, step: 0.05, fmt: times, title: 'The booth room fixtures the stage-light desk drives (washes, uplight, ceiling spot); Booth only' },
];

const RENDERING: (Slider | string | ['toggle', keyof RenderValues, string])[] = [
  { k: 'exposure', label: 'Exposure', min: 0.3, max: 2.5, step: 0.05, fmt: x2 },
  ['toggle', 'bloom', 'Bloom'],
  { k: 'bloomStrength', label: 'Strength', min: 0, max: 2, step: 0.05, fmt: x2 },
  { k: 'bloomThreshold', label: 'Threshold', min: 0.5, max: 3, step: 0.05, fmt: x2, title: 'Brightness where glow starts; 1.0 = only the LEDs' },
  { k: 'bloomRadius', label: 'Radius', min: 0, max: 1, step: 0.05, fmt: x2 },
  ['toggle', 'ao', 'Ambient occlusion'],
  { k: 'aoIntensity', label: 'Intensity', min: 0, max: 10, step: 0.25, fmt: (x) => x.toFixed(1) },
  ['toggle', 'shadows', 'Shadows'],
  { k: 'shadowSoftness', label: 'Softness', min: 0, max: 4, step: 0.1, fmt: times },
  'LED glow',
  { k: 'eyes', label: 'Eyes', min: 0, max: 2, step: 0.05, fmt: times, title: 'All the light the eyes emit' },
  { k: 'eyesGlow', label: 'Eye halo', min: 0, max: 3, step: 0.05, fmt: times, title: 'Only the halo round the eyes (not the lit lens, not the bloom)' },
  { k: 'mouth', label: 'Mouth', min: 0, max: 2, step: 0.05, fmt: times },
  { k: 'mouthGlow', label: 'Mouth halo', min: 0, max: 3, step: 0.05, fmt: times },
  { k: 'body', label: 'Body', min: 0, max: 2, step: 0.05, fmt: times, title: 'The chest logic panel LEDs' },
  { k: 'bodyGlow', label: 'Body halo', min: 0, max: 3, step: 0.05, fmt: times, title: 'How much of the chest LEDs’ brightness reaches the bloom' },
];

const TONE_LABEL: Record<ToneMap, string> = { neutral: 'Neutral', agx: 'AgX', aces: 'ACES Filmic' };

const svg = (d: string) => `<svg viewBox="0 0 16 16" aria-hidden="true">${d}</svg>`;
const ICON_LIGHT = svg('<path d="M6 12.5h4M6.5 14.5h3" /><path d="M8 1.5a4.2 4.2 0 0 0-2.5 7.6c.6.5.9 1.1.9 1.9h3.2c0-.8.3-1.4.9-1.9A4.2 4.2 0 0 0 8 1.5z" />');
const ICON_RENDER = svg('<circle cx="8" cy="8" r="6" /><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor" stroke="none" />');

/**
 * Build the Lighting (after Environment) and Rendering (after Performance) sections and their
 * rail icons. Both are collapsible; closed by default, and each viewer's open/closed is kept.
 */
export function mountRenderPanel(rs: RenderSettings, isBooth: () => boolean, hasAO: () => boolean, workOn: () => boolean) {
  const body = document.getElementById('scene-body');
  if (!body) return;
  const ids = new Map<keyof RenderValues, { input: HTMLInputElement | HTMLSelectElement | HTMLButtonElement; out?: HTMLOutputElement }>();
  let n = 0;

  const slider = (s: Slider, parent: HTMLElement) => {
    const id = `rs-${s.k}`;
    const row = document.createElement('div');
    row.className = 'rs-slider';
    row.innerHTML = `<label for="${id}">${s.label}</label><input id="${id}" type="range" min="${s.min}" max="${s.max}" step="${s.step}" /><output for="${id}"></output>`;
    if (s.title) row.title = s.title;
    const input = row.querySelector('input')!;
    const out = row.querySelector('output')!;
    input.oninput = () => rs.set({ [s.k]: Number(input.value) } as Partial<RenderValues>);
    input.ondblclick = () => rs.set({ [s.k]: DEFAULT_VALUES[s.k] } as Partial<RenderValues>);
    ids.set(s.k, { input, out });
    (row as HTMLElement & { fmt?: Slider['fmt'] }).fmt = s.fmt;
    parent.appendChild(row);
  };
  const sub = (label: string, parent: HTMLElement, toggle?: keyof RenderValues) => {
    const h = document.createElement('div');
    h.className = 'rs-sub';
    h.innerHTML = `<h3 id="rs-h-${++n}">${label}</h3>`;
    if (toggle) {
      const b = document.createElement('button');
      b.className = 'rs-switch';
      b.setAttribute('aria-labelledby', `rs-h-${n}`);
      b.onclick = () => {
        rs.set({ [toggle]: !rs.values[toggle] } as Partial<RenderValues>);
        b.blur();
      };
      h.appendChild(b);
      ids.set(toggle, { input: b });
    }
    parent.appendChild(h);
  };

  const section = (id: string, title: string, which: 'light' | 'render') => {
    const sec = document.createElement('section');
    sec.id = id;
    sec.className = 'rs-section';
    const det = document.createElement('details');
    det.open = rs.openSections[which];
    det.innerHTML = `<summary><h2>${title}</h2></summary>`;
    det.ontoggle = () => rs.setOpen(which, det.open);
    sec.appendChild(det);
    const inner = document.createElement('div');
    inner.className = 'rs-body';
    det.appendChild(inner);
    return { sec, inner };
  };

  // ---- Lighting
  const light = section('sc-light', 'Lighting', 'light');
  for (const item of LIGHTING) {
    if (typeof item === 'string') sub(item, light.inner);
    else slider(item, light.inner);
  }
  const deskHint = document.createElement('p');
  deskHint.className = 'hint rs-note';
  deskHint.textContent = 'The light desk lights the booth; pick Booth under Environment.';
  light.inner.appendChild(deskHint);

  // ---- Rendering
  const render = section('sc-render', 'Rendering', 'render');
  const presets = document.createElement('div');
  presets.className = 'rs-presets';
  presets.setAttribute('role', 'group');
  presets.setAttribute('aria-label', 'Look preset');
  const presetBtns = new Map<PresetName, HTMLButtonElement>();
  for (const [name, p] of Object.entries(PRESETS) as [PresetName, (typeof PRESETS)[PresetName]][]) {
    const b = document.createElement('button');
    b.textContent = p.label;
    b.onclick = () => {
      rs.usePreset(name);
      b.blur();
    };
    presetBtns.set(name, b);
    presets.appendChild(b);
  }
  render.inner.appendChild(presets);
  const tone = document.createElement('label');
  tone.className = 'rs-slider rs-select';
  tone.innerHTML = `<span>Tone map</span><select id="rs-toneMap">${(Object.keys(TONE_LABEL) as ToneMap[])
    .map((t) => `<option value="${t}">${TONE_LABEL[t]}</option>`).join('')}</select>`;
  const toneSel = tone.querySelector('select')!;
  toneSel.onchange = () => {
    rs.set({ toneMap: toneSel.value as ToneMap });
    toneSel.blur();
  };
  ids.set('toneMap', { input: toneSel });
  for (const item of RENDERING) {
    if (typeof item === 'string') sub(item, render.inner);
    else if (Array.isArray(item)) sub(item[2], render.inner, item[1]);
    else slider(item, render.inner);
    if (!Array.isArray(item) && typeof item !== 'string' && item.k === 'exposure') render.inner.appendChild(tone);
  }
  const aoHint = document.createElement('p');
  aoHint.className = 'hint rs-note';
  aoHint.textContent = 'Performance quality renders without AO.';
  render.inner.insertBefore(aoHint, ids.get('aoIntensity')!.input.parentElement!.nextSibling);
  const foot = document.createElement('div');
  foot.className = 'row rs-foot';
  foot.innerHTML = '<span class="rs-preset-name" aria-live="polite"></span><button id="rs-reset" title="Back to the Default look (lighting and rendering)">Reset</button>';
  foot.querySelector('button')!.onclick = (e) => {
    rs.usePreset('default');
    (e.currentTarget as HTMLElement).blur();
  };
  render.inner.appendChild(foot);

  document.getElementById('sc-env')?.after(light.sec);
  document.getElementById('sc-perf')?.after(render.sec);

  // Rail icons, in section order.
  const rail = document.querySelector('#scene-panel .rail');
  const railBtn = (target: string, label: string, icon: string, after: string) => {
    const b = document.createElement('button');
    b.dataset.sceneOpen = target;
    b.setAttribute('aria-label', label);
    b.title = label;
    b.innerHTML = icon;
    rail?.querySelector(`[data-scene-open="${after}"]`)?.after(b);
  };
  railBtn('sc-light', 'Lighting', ICON_LIGHT, 'sc-env');
  railBtn('sc-render', 'Rendering', ICON_RENDER, 'sc-perf');

  const sync = () => {
    const v = rs.values;
    for (const [k, { input, out }] of ids) {
      const val = v[k];
      if (input instanceof HTMLButtonElement) {
        input.setAttribute('aria-pressed', String(val));
        input.textContent = val ? 'On' : 'Off';
      } else if (document.activeElement !== input || input instanceof HTMLSelectElement) {
        input.value = String(val);
      }
      if (out) {
        const fmt = (out.parentElement as HTMLElement & { fmt?: Slider['fmt'] }).fmt;
        out.textContent = fmt ? fmt(val as number) : String(val);
      }
    }
    const dis = (keys: (keyof RenderValues)[], off: boolean) => keys.forEach((k) => {
      const e = ids.get(k)?.input;
      if (e) {
        (e as HTMLInputElement).disabled = off;
        e.parentElement!.classList.toggle('off', off);
      }
    });
    dis(['bloomStrength', 'bloomThreshold', 'bloomRadius'], !v.bloom);
    dis(['aoIntensity'], !v.ao || !hasAO());
    aoHint.hidden = !v.ao || hasAO();
    dis(['shadowSoftness'], !v.shadows);
    dis(['desk'], !isBooth());
    dis(['work', 'workTemp'], !workOn());
    deskHint.hidden = isBooth();
    const p = rs.preset;
    for (const [name, b] of presetBtns) b.setAttribute('aria-pressed', String(name === p));
    foot.querySelector('.rs-preset-name')!.textContent = p ? '' : 'Custom';
  };
  rs.onChange(sync);
  // Backdrop (the desk needs the booth) and quality (Performance has no AO) change what applies.
  for (const id of ['quality', 'scene-bg']) document.getElementById(id)?.addEventListener('change', () => setTimeout(sync));
  document.getElementById('scene-work')?.addEventListener('click', () => setTimeout(sync));
  sync();
  return { sync };
}
