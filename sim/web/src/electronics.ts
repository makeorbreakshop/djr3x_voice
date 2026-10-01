/**
 * Electronics packages (`profiles/electronics/<id>.json`, schema in r3x-contracts
 * `electronics.rs`): which one is selected, its LEDs at their real positions, its boards in
 * the 3D model, and its power budget / wiring / BOM as HTML.
 *
 * Selection: `?electronics=<id>`, else the Electronics tab's choice (localStorage), else the
 * profile's `electronics`. The embedded performer resolves the package itself (the profile
 * JSON goes in with `electronics` set, and Rust swaps the light groups), so the LED preview
 * is the selected package's emulator. Connected, the runtime's hello carries the resolved
 * profile, `package` included, and that wins.
 *
 * Build mode hook: `mountElectronics(el, ...)` renders the selector + tables into any
 * container, and `BoardsView` shows the boards in the model - the Build panel (sim/web/src/build/,
 * once it lands) can mount both as its Electronics section.
 */

import * as THREE from 'three';
import type { ElectronicsPackage } from './generated/ElectronicsPackage';
import type { PackageLight } from './generated/PackageLight';
import type { LightWindow } from './generated/LightWindow';
import type { Rig } from './rig';
import type { RGB } from './leds';
import { BODY_LED_CAP, bodyLedColor } from './chestlights';

const FILES = import.meta.glob<ElectronicsPackage>('../../../profiles/electronics/*.json', { eager: true, import: 'default' });

/** Every package in the repo, by id, in file order. */
export const PACKAGES: Record<string, ElectronicsPackage> = Object.fromEntries(
  Object.values(FILES).map((p) => [p.id, p]),
);

const KEY = 'r3x.electronics';

/** The selected package id (URL, then the stored choice, then the profile's default). */
export function selectedPackageId(profileDefault: string | undefined): string {
  const fromUrl = new URLSearchParams(location.search).get('electronics');
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(KEY);
  } catch {
    /* storage blocked */
  }
  for (const id of [fromUrl, stored, profileDefault]) if (id && PACKAGES[id]) return id;
  return Object.keys(PACKAGES)[0];
}

/** Remember a choice and reload (the performer and the model's lights are built at load). */
export function selectPackage(id: string) {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* storage blocked: the URL param still works */
  }
  const u = new URL(location.href);
  u.searchParams.set('electronics', id);
  location.href = u.toString();
}

/** The robot profile JSON with `electronics` set to `id` (the performer resolves it). */
export function profileJsonWith(profileJson: string, id: string): string {
  const p = JSON.parse(profileJson);
  p.electronics = id;
  delete p.package;
  return JSON.stringify(p);
}

/** Does the package bring its own LED emulator (the native face + chest renderers stand down)? */
export function ownsLights(pkg: ElectronicsPackage | null | undefined): boolean {
  return !!pkg && pkg.emulator === 'grnwave';
}

// ------------------------------------------------------------------ 3D

/** The node a package `link` rides on: a rig joint's node, else the model root. */
function linkNode(rig: Rig, link: string): THREE.Object3D {
  return rig.joints.get(link)?.node ?? rig.root.getObjectByName('r3x_root') ?? rig.root;
}

/** LED kinds drawn as squares (5050 blocks, diffuser windows); the rest are round. */
const SQUARE = new Set(['block', 'block_hidden', 'window']);

/**
 * The package's LEDs at their layout positions (model kit frame, see Rig.kitToLocal), one
 * emissive disc or square each (size = the LED's `w` x `h`), parented to their group's link
 * so they turn with it. What they show comes from the performer's package emulator, as
 * frames (`frames.package`, or the gateway's `lights`). Body LEDs are capped just over the
 * bloom threshold (chestlights.ts bodyLedColor); the head's are for the face renderer's
 * bulbs (faceSlots), so they keep the plain gain.
 */
export class PackageLeds {
  private readonly groups = new Map<string, { mats: THREE.MeshBasicMaterial[]; capped: boolean }>();
  private readonly meshes: THREE.Mesh[] = [];
  private readonly byGroup = new Map<string, THREE.Mesh[]>();

  constructor(rig: Rig, lights: PackageLight[], private readonly gain = 4) {
    const z = new THREE.Vector3(0, 0, 1);
    for (const g of lights) {
      const parent = linkNode(rig, g.link);
      const mats: THREE.MeshBasicMaterial[] = [];
      for (const px of g.layout) {
        const mat = new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false, side: THREE.DoubleSide });
        const geo = SQUARE.has(px.kind)
          ? new THREE.PlaneGeometry(px.w * 0.9, px.h * 0.9)
          : new THREE.CircleGeometry(Math.max(0.0012, Math.min(px.w, px.h) / 2), 16);
        const m = new THREE.Mesh(geo, mat);
        m.name = `pkg_led_${g.name}`;
        m.position.copy(rig.kitToLocal(parent, px.pos));
        m.quaternion.setFromUnitVectors(z, new THREE.Vector3(...px.normal).normalize());
        m.visible = px.kind !== 'block_hidden';
        if (!m.visible) m.name = 'pkg_led_hidden';
        m.renderOrder = 2;
        parent.add(m);
        mats.push(mat);
        this.meshes.push(m);
      }
      this.byGroup.set(g.name, this.meshes.slice(-g.layout.length));
      this.groups.set(g.name, { mats, capped: !g.link.startsWith('head') && g.link !== 'visor' });
    }
  }

  /** Hide a group's pixels that a diffuser pane draws instead (none: show them all). */
  setCovered(group: string, indices: Iterable<number>) {
    const covered = new Set(indices);
    this.byGroup.get(group)?.forEach((m, i) => (m.visible = !covered.has(i) && m.name !== 'pkg_led_hidden'));
  }

  dispose() {
    for (const m of this.meshes) m.removeFromParent();
  }

  update(lights: Record<string, RGB[]>) {
    for (const [name, { mats, capped }] of this.groups) {
      const px = lights[name] ?? [];
      mats.forEach((mat, i) => bodyLedColor(mat.color, px[i] ?? [0, 0, 0], this.gain, capped ? BODY_LED_CAP : Infinity));
    }
  }
}

// ------------------------------------------------------------------ diffusers

/** `?diffusers=0` starts with the panes off (raw pixels); the Electronics tab toggles it. */
export function diffusersFromUrl(): boolean {
  return new URLSearchParams(location.search).get('diffusers') !== '0';
}

/**
 * Brightness across a diffuser face, 0..1 (x, y in -1..1): even in the middle, falling
 * toward the edges and corners - light spreading sideways in the acrylic and the recess
 * walls shading it - so a pane reads as a lit diffuser, not a flat sticker. Frosted keeps
 * more of a hot centre than opal.
 */
export function diffuserFalloff(x: number, y: number, kind: 'opal' | 'frosted'): number {
  const sq = Math.max(Math.abs(x), Math.abs(y));
  const round = Math.hypot(x, y) / Math.SQRT2;
  const r = 0.6 * sq + 0.4 * round;
  const s = (e0: number, e1: number, v: number) => {
    const t = Math.min(1, Math.max(0, (v - e0) / (e1 - e0)));
    return t * t * (3 - 2 * t);
  };
  return kind === 'opal' ? 1 - 0.6 * s(0.35, 1.05, r) : 1 - 0.3 * s(0, 0.7, r) - 0.35 * s(0.6, 1.05, r);
}

const falloffTex = new Map<string, THREE.DataTexture>();
function falloffTexture(kind: 'opal' | 'frosted'): THREE.DataTexture {
  let t = falloffTex.get(kind);
  if (t) return t;
  const n = 64;
  const data = new Uint8Array(n * n * 4);
  for (let j = 0; j < n; j++)
    for (let i = 0; i < n; i++) {
      const v = Math.round(255 * diffuserFalloff(((i + 0.5) / n) * 2 - 1, ((j + 0.5) / n) * 2 - 1, kind));
      data.set([v, v, v, 255], (j * n + i) * 4);
    }
  t = new THREE.DataTexture(data, n, n); // linear data: it scales the emitted light
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearFilter;
  t.needsUpdate = true;
  falloffTex.set(kind, t);
  return t;
}

/**
 * One colour a diffused window shows: the mean of its LEDs in linear light (channel values
 * are PWM duty, i.e. linear), times the cover's transmission. Same 0-255 scale as a pixel.
 */
export function windowColor(px: RGB[], w: Pick<LightWindow, 'leds' | 'diffuser'>): RGB {
  const sum: RGB = [0, 0, 0];
  for (const i of w.leds) {
    const p = px[i] ?? [0, 0, 0];
    sum[0] += p[0];
    sum[1] += p[1];
    sum[2] += p[2];
  }
  const k = w.diffuser.transmission / Math.max(1, w.leds.length);
  return [sum[0] * k, sum[1] * k, sum[2] * k];
}

/** A light group's diffused windows: which group feeds them, where they ride, how bright. */
export interface DiffuserSource {
  group: string;
  link: string;
  windows: LightWindow[];
  /** The raw pixels' renderer gain (ChestLights 3.5, PackageLeds 4). */
  gain: number;
}

/** The diffused windows the page shows for `pkg`: its own groups', else the native chest's
 * (a package with no LED boards keeps the native chest preview). */
export function diffuserSources(pkg: ElectronicsPackage): DiffuserSource[] {
  const own = ownsLights(pkg);
  const groups = own ? pkg.lights : (pkg.lights.some((g) => g.name === 'chest') ? pkg : PACKAGES.r3x_native)?.lights ?? [];
  return groups
    .filter((g) => (own || g.name === 'chest') && (g.windows ?? []).some((w) => w.diffuser.kind !== 'none'))
    .map((g) => ({ group: g.name, link: g.link, windows: (g.windows ?? []).filter((w) => w.diffuser.kind !== 'none'), gain: own ? 4 : 3.5 }));
}

/**
 * Diffused logic-panel windows: a flat emissive pane filling each window opening (kit
 * panel geometry, `LightWindow`), lit by the mean of the LEDs behind it (windowColor) with
 * an edge falloff. Capped like the raw body LEDs (bodyLedColor), so bloom stays tame. The
 * raw pixels under a pane are hidden while the panes are on (see `covered`).
 */
export class Diffusers {
  private readonly panes: { group: string; w: LightWindow; mat: THREE.MeshBasicMaterial; gain: number; mesh: THREE.Mesh }[] = [];
  private on = true;

  constructor(rig: Rig, readonly sources: DiffuserSource[]) {
    for (const src of sources) {
      const parent = linkNode(rig, src.link);
      for (const w of src.windows) {
        const kind = w.diffuser.kind === 'frosted' ? 'frosted' : 'opal';
        const mat = new THREE.MeshBasicMaterial({ color: 0x000000, map: falloffTexture(kind), toneMapped: false });
        const geo = w.shape === 'round'
          ? new THREE.CircleGeometry(Math.min(...w.size_m) / 2, 24)
          : new THREE.PlaneGeometry(w.size_m[0], w.size_m[1]);
        const mesh = new THREE.Mesh(geo, mat);
        mesh.name = `diffuser_${w.id}`;
        mesh.position.copy(rig.kitToLocal(parent, w.centre));
        // Kit-frame directions are the link's own: x = the face's width, z = out of the face.
        const n = new THREE.Vector3(...w.normal).normalize();
        const u = new THREE.Vector3(...w.u).normalize();
        mesh.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(u, n.clone().cross(u), n));
        mesh.renderOrder = 2;
        parent.add(mesh);
        this.panes.push({ group: src.group, w, mat, gain: src.gain, mesh });
      }
    }
  }

  /** Pixel indices, per group, that the panes draw (hide the raw ones while on). */
  covered(group: string): number[] {
    return this.on ? this.panes.filter((p) => p.group === group).flatMap((p) => p.w.leds) : [];
  }

  get enabled() {
    return this.on;
  }

  setEnabled(on: boolean) {
    this.on = on;
    for (const p of this.panes) p.mesh.visible = on;
  }

  update(lights: Record<string, RGB[]>) {
    if (!this.on) return;
    for (const p of this.panes) bodyLedColor(p.mat.color, windowColor(lights[p.group] ?? [], p.w), p.gain, BODY_LED_CAP);
  }

  dispose() {
    for (const p of this.panes) p.mesh.removeFromParent();
  }
}

/** Downmix a package's eyes/mouth onto the face renderer's 14 + 8 slots (bulb / pipe glow). */
export function faceSlots(lights: Record<string, RGB[]>): { eyes: RGB[]; mouth: RGB[] } {
  const e = lights.eyes ?? [];
  const eyes: RGB[] = Array.from({ length: 14 }, (_, i) => {
    const src = e.length ? e[Math.min(e.length - 1, Math.floor((i / 14) * e.length))] : [0, 0, 0];
    return src.map((v) => v * 0.6) as RGB; // one 8 mm LED behind the bulb, not a 7-LED jewel
  });
  const m = lights.mouth ?? [];
  const mouth: RGB[] = Array.from({ length: 8 }, (_, i) => m[i] ?? [0, 0, 0]);
  return { eyes, mouth };
}

const SUPPORT_COLOR = { driven: 0x3ddc84, partial: 0xf5b642, listed: 0x8a90a0 } as const;

/** The package's boards as translucent boxes at their mounts (Build / Electronics view). */
export class BoardsView {
  readonly meshes: THREE.Mesh[] = [];

  constructor(rig: Rig, pkg: ElectronicsPackage) {
    for (const b of pkg.boards) {
      const parent = linkNode(rig, b.mount.link);
      const [l, w, h] = b.dims_mm.map((v) => v / 1000);
      const mat = new THREE.MeshBasicMaterial({
        color: SUPPORT_COLOR[b.support], transparent: true, opacity: 0.35, depthTest: false, toneMapped: false,
      });
      const m = new THREE.Mesh(new THREE.BoxGeometry(l, w, h), mat);
      m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), new THREE.LineBasicMaterial({ color: SUPPORT_COLOR[b.support], depthTest: false })));
      m.name = `board_${b.id}`;
      m.position.copy(rig.kitToLocal(parent, b.mount.t));
      m.quaternion.set(...b.mount.q);
      m.renderOrder = 3;
      m.userData.board = b.id;
      m.visible = false;
      parent.add(m);
      this.meshes.push(m);
    }
  }

  setVisible(on: boolean) {
    for (const m of this.meshes) m.visible = on;
  }

  dispose() {
    for (const m of this.meshes) m.removeFromParent();
  }
}

// ------------------------------------------------------------------ HTML

/** Optional fields may be absent in the JSON (serde defaults), so tolerate undefined. */
const esc = (s: string | undefined | null) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const link = (text: string, url?: string) => (url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text)}</a>` : esc(text));
const inferred = (on: boolean | undefined, note: string | undefined) => (on ? ` <abbr class="inferred" title="${esc(note)}">inferred</abbr>` : '');
const amps = (ma: number) => (ma >= 1000 ? `${(ma / 1000).toFixed(2)} A` : `${Math.round(ma)} mA`);

/** "9 x 4 px, opal 55 %" for a group's windows. */
function windowsNote(g: PackageLight): string {
  const ws = g.windows ?? [];
  if (!ws.length) return '';
  const per = [...new Set(ws.map((w) => w.leds.length))].join('/');
  const kinds = [...new Set(ws.map((w) => `${w.diffuser.kind} ${Math.round(w.diffuser.transmission * 100)} %`))].join(', ');
  return `${ws.length} x ${per} px, ${esc(kinds)}`;
}

/** Boards, lights, power budget, wiring and BOM of one package. */
export function packageHtml(pkg: ElectronicsPackage): string {
  const boards = pkg.boards.map((b) => `<tr>
      <td>${link(b.model, b.url)}${inferred(b.inferred, b.inferred_note)}</td>
      <td>${esc(b.role)}</td>
      <td class="num">${b.dims_mm.map((v) => v.toFixed(0)).join(' x ')}</td>
      <td>${esc(b.mount.bracket)} <small>(${esc(b.mount.link)})</small>${inferred(b.mount.inferred, b.mount.inferred_note)}</td>
      <td class="num">${b.volts} V, ${amps(b.current.typical_ma)} / ${amps(b.current.max_ma)}</td>
      <td><span class="support ${b.support}">${b.support}</span>${b.driver ? ` <code>${esc(b.driver)}</code>` : ''}${b.firmware ? `<br><code>${esc(b.firmware)}</code>` : ''}</td>
    </tr>`).join('');
  const lights = pkg.lights.map((g) => `<tr>
      <td><code>${esc(g.name)}</code>${inferred(g.inferred, g.inferred_note)}</td>
      <td class="num">${g.pixels}</td>
      <td><code>${esc(g.data_line)}</code> @${g.chain_start}</td>
      <td>${esc(g.led)}</td>
      <td>${esc(g.serves.join(', '))}</td>
      <td>${windowsNote(g)}</td>
    </tr>`).join('');
  const rails = pkg.power.map((r) => `<tr>
      <td>${esc(r.name)}</td><td class="num">${r.volts} V</td>
      <td class="num">${amps(r.typical_ma)}</td><td class="num">${amps(r.max_ma)}</td>
      <td>${esc(r.psu)}${r.note ? `<br><small>${esc(r.note)}</small>` : ''}</td>
    </tr>`).join('');
  const wires = pkg.wiring.map((w) => `<tr>
      <td><code>${esc(w.from)}</code></td><td><code>${esc(w.to)}</code></td>
      <td>${esc(w.signal)}</td><td>${esc(w.connector)}</td><td class="num">${w.awg}</td>
      <td><small>${esc(w.note)}</small></td>
    </tr>`).join('');
  const bom = pkg.bom.map((i) => `<tr>
      <td class="num">${i.qty}</td><td>${link(i.item, i.url ?? undefined)}</td><td>${esc(i.category)}</td>
      <td class="num">${i.unit_usd != null ? `$${i.unit_usd.toFixed(2)}` : ''}</td><td><small>${esc(i.note)}</small></td>
    </tr>`).join('');
  const drives = pkg.actuators.map((a) => `<li><code>${esc(a.board)}</code> drives ${esc(a.actuators.join(', '))} via <code>${esc(a.driver)}</code> <span class="support ${a.support}">${a.support}</span>${a.note ? ` <small>${esc(a.note)}</small>` : ''}</li>`).join('');
  const table = (head: string[], body: string) =>
    body ? `<div class="table-wrap"><table class="elec"><thead><tr>${head.map((h) => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>` : '<p class="hint">None.</p>';
  return `
    ${pkg.url ? `<p>${link('Product page', pkg.url)}</p>` : ''}
    <h3>Boards <small>${pkg.boards.length}</small></h3>
    ${table(['board', 'role', 'mm', 'mount', 'power', 'support'], boards)}
    <h3>Lights <small>${pkg.lights.reduce((n, g) => n + g.pixels, 0)} px</small></h3>
    ${table(['group', 'px', 'chain', 'LED', 'shows', 'windows'], lights)}
    ${drives ? `<h3>Actuators</h3><ul class="elec-list">${drives}</ul>` : ''}
    <h3>Power budget</h3>
    ${table(['rail', 'V', 'typical', 'max', 'supply'], rails)}
    <h3>Wiring <small>${pkg.wiring.length}</small></h3>
    ${table(['from', 'to', 'signal', 'connector', 'AWG', ''], wires)}
    <h3>BOM</h3>
    ${table(['qty', 'item', 'kind', 'each', ''], bom)}
    ${pkg.notes.length ? `<ul class="elec-list">${pkg.notes.map((n) => `<li><small>${esc(n)}</small></li>`).join('')}</ul>` : ''}`;
}

/**
 * The Electronics section: a package selector (reloads with the new package) plus the
 * selected package's tables, and a "show boards" toggle for `boards`. `active` = the package
 * the page is running (a gateway's resolved profile, else the selection).
 */
export function mountElectronics(
  el: HTMLElement,
  active: ElectronicsPackage,
  opts: {
    boards?: () => BoardsView | null;
    connected?: boolean;
    /** The diffuser panes and a setter for them (on = panes, off = the raw pixels). */
    diffusers?: { on: boolean; count: number; set: (on: boolean) => void };
  } = {},
) {
  const options = Object.values(PACKAGES)
    .map((p) => `<option value="${esc(p.id)}"${p.id === active.id ? ' selected' : ''}>${esc(p.label)}</option>`)
    .join('');
  // The tab keeps the choice and two switches; the reference tables (boards, lights, power,
  // wiring, BOM) open in a sheet over the viewport, wide enough to read them.
  el.innerHTML = `
    <div class="row elec-pick">
      <label for="elec-select">Package</label>
      <select id="elec-select" title="${esc(active.description)}${opts.connected ? '\n\nConnected: the runtime\'s profile decides (R3X_ELECTRONICS); a choice here applies to the offline demo.' : ''}">${options}</select>
    </div>
    <label class="check"><input type="checkbox" id="elec-boards"> Show boards</label>
    ${opts.diffusers?.count ? `<label class="check" title="${opts.diffusers.count} windows; off shows the raw pixels"><input type="checkbox" id="elec-diffusers"${opts.diffusers.on ? ' checked' : ''}> Diffusers</label>` : ''}
    <div class="row"><button id="elec-sheet-open" aria-haspopup="dialog">Boards, wiring and power</button></div>`;
  let sheet = document.getElementById('elec-sheet') as HTMLDialogElement | null;
  if (!sheet) {
    sheet = document.createElement('dialog');
    sheet.id = 'elec-sheet';
    sheet.className = 'sheet';
    document.body.append(sheet);
    sheet.addEventListener('click', (e) => {
      if (e.target === sheet || (e.target as HTMLElement).closest('[data-sheet-close]')) sheet!.close();
    });
  }
  sheet.setAttribute('aria-label', `${active.label}: boards, wiring and power`);
  sheet.innerHTML = `<div class="sheet-body"><header><h2>${esc(active.label)} <span class="support ${active.support}">${active.support}</span></h2>
    <button class="icon" data-sheet-close aria-label="Close" title="Close (Esc)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button></header>
    <p class="sheet-desc">${esc(active.description)}</p>
    <div id="elec-detail">${packageHtml(active)}</div></div>`;
  el.querySelector<HTMLButtonElement>('#elec-sheet-open')!.onclick = () => sheet!.showModal();
  el.querySelector<HTMLSelectElement>('#elec-select')!.onchange = (e) => selectPackage((e.target as HTMLSelectElement).value);
  el.querySelector<HTMLInputElement>('#elec-boards')!.onchange = (e) => opts.boards?.()?.setVisible((e.target as HTMLInputElement).checked);
  const diff = el.querySelector<HTMLInputElement>('#elec-diffusers');
  if (diff) diff.onchange = () => opts.diffusers?.set(diff.checked);
}
