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
        m.renderOrder = 2;
        parent.add(m);
        mats.push(mat);
        this.meshes.push(m);
      }
      this.groups.set(g.name, { mats, capped: !g.link.startsWith('head') && g.link !== 'visor' });
    }
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
    <p class="hint">${esc(pkg.description)} ${pkg.url ? link('Product page', pkg.url) : ''}</p>
    <h3>Boards <small>${pkg.boards.length}</small></h3>
    ${table(['board', 'role', 'mm', 'mount', 'power', 'support'], boards)}
    <h3>Lights <small>${pkg.lights.reduce((n, g) => n + g.pixels, 0)} px</small></h3>
    ${table(['group', 'px', 'chain', 'LED', 'shows'], lights)}
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
  opts: { boards?: () => BoardsView | null; connected?: boolean } = {},
) {
  const options = Object.values(PACKAGES)
    .map((p) => `<option value="${esc(p.id)}"${p.id === active.id ? ' selected' : ''}>${esc(p.label)}</option>`)
    .join('');
  el.innerHTML = `
    <div class="row elec-pick">
      <label for="elec-select">Package</label>
      <select id="elec-select">${options}</select>
      <span class="support ${active.support}">${active.support}</span>
    </div>
    ${opts.connected ? '<p class="hint">Connected: the runtime\'s profile decides (<code>R3X_ELECTRONICS</code>); a choice here applies to the offline demo.</p>' : ''}
    <label class="check"><input type="checkbox" id="elec-boards"> Show boards in the model</label>
    <div id="elec-detail">${packageHtml(active)}</div>`;
  el.querySelector<HTMLSelectElement>('#elec-select')!.onchange = (e) => selectPackage((e.target as HTMLSelectElement).value);
  el.querySelector<HTMLInputElement>('#elec-boards')!.onchange = (e) => opts.boards?.()?.setVisible((e.target as HTMLInputElement).checked);
}
