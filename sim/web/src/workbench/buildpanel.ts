/**
 * Build mode's panels. Scene (left) gets the view controls - explode, shell solid/x-ray/
 * hidden, section plane, fasteners - which never change the model. The R3X panel's Build
 * tabs are the builder: Parts (tree, visibility, isolate, what a picked part is), Joints
 * (sliders within limits, Home, sweep to the first contact), Checks (click to pose the problem)
 * and BOM. The assembly sequence is Instructions (guide.ts), full screen, from the view bar. Every joint names
 * the profile joint it is (`head_tilt` in Bench/Studio/Show), or says it is not in the profile.
 */

import './build.css';
import { mayExist } from './published';
import { contextVis, type AsmNode, type Context, type Look, type Workbench } from './workbench';
import { partKey, type Vis } from './visibility';
import { mountGuide } from './guide';
import { assemblyLabel, jointLabel, libraryAvailable, libraryFrom, printList, subtreeParts, type MotionSystem } from './systems';
import { variantOptions, joinUrl, loadIndex, MECH_BASE, type IndexEntry, type MAssembly, type MCheck, type MJoint, type MPart } from './manifest';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const num = (v: number, d = 1) => (Math.abs(v) < 0.05 && d <= 1 ? '0' : v.toFixed(d).replace(/\.0+$/, ''));
const signed = (v: number, d = 1) => (v > 0.049 ? '+' : '') + num(v, d);
const KEY = 'r3x.build';
/** The index entry that is our build (the configured droid); every other entry is a library design. */
const OUR_BUILD = 'r3x_droid';
const LOOKS: Look[] = ['exterior', 'mechanism', 'inspect'];
const unit = (j: MJoint) => (j.unit === 'mm' ? ' mm' : '°');

interface Stored { explode?: number; look?: Look; context?: Context; fasteners?: boolean; closed?: string[]; opened?: string[] }

/** A system's motion in a few words: its joints ("tilt · roll"), or one joint's range. */
function systemMotion(s: MotionSystem): string {
  const skip = new Set([...s.name.toLowerCase().split(/\s+/), 'arm', 'head', 'ring']);
  const words = s.joints.map(({ joint }) => jointLabel(joint.name).toLowerCase().split(/\s+/).find((w) => !skip.has(w)) ?? '');
  if (s.joints.length > 1 && words.every(Boolean)) return [...new Set(words)].join(' · ');
  const j = s.joints[0].joint;
  const u = j.unit === 'mm' ? ' mm' : '°';
  return j.limits.min === -j.limits.max ? `±${num(j.limits.max)}${u}` : `${num(j.limits.min)}…${num(j.limits.max)}${u}`;
}

function load(): Stored {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Stored;
  } catch {
    return {};
  }
}
function save(s: Stored) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked */
  }
}

/** Where a part's geometry comes from, as one glyph: our parametric model, the vendor's CAD, or a mesh. */
type Src = 'parametric' | 'vendor' | 'mesh' | 'placeholder';
const srcOf = (c?: string): Src => (c === 'parametric' || c === 'vendor' || c === 'placeholder' ? c : 'mesh');
const SRC_LABEL: Record<Src, string> = { parametric: 'Parametric', vendor: 'Vendor CAD', mesh: 'Mesh', placeholder: 'Placeholder' };
const SRC_TITLE: Record<Src, string> = {
  parametric: 'Parametric: our build123d model, to the source or the published spec',
  vendor: "Vendor CAD: the manufacturer's B-rep",
  mesh: 'Mesh: the source STL, not yet remodelled',
  placeholder: 'Placeholder: a sized box, no model yet',
};
const srcDot = (c?: string) => `<i class="src ${srcOf(c)}" role="img" aria-label="${SRC_LABEL[srcOf(c)]}" title="${esc(SRC_TITLE[srcOf(c)])}"></i>`;

/** The name without a parenthetical the glyph already says, or a long aside (full name on hover). */
const shortName = (n: string) => n.replace(/\s*\((parametric|ours)\)\s*$/i, '');
const bare = (n: string) => n.replace(/\s*\([^)]*\)\s*/g, ' ').trim();


/**
 * Build's markup, added to the page's two panels before they are wired (main.ts calls this
 * before mountPanels / ControlPanel so their tab and rail bindings see it). Kept here rather
 * than in index.html so Build stays one module.
 *
 * Left (Scene panel, titled Build): the navigator - search, systems, assemblies, parts, and the
 * view controls that never change the model. Right (R3X panel): the inspector for what is in
 * focus (Inspect), every check result (Checks, with a count), BOM; Electronics under More. The
 * look (Exterior / Mechanism / X-ray) is the viewport bar's (index.html), and so is Instructions.
 */
export function injectBuildDom() {
  if (document.getElementById('sc-build')) return;
  const html = (s: string) => {
    const t = document.createElement('template');
    t.innerHTML = s.trim();
    return t.content;
  };
  // Scene panel: rail icon + the navigator, first.
  const rail = document.querySelector('#scene-panel .rail')!;
  rail.prepend(html(`<button data-scene-open="sc-build" data-build aria-label="Build navigator" title="Build navigator">
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5 14 5v6l-6 3.5L2 11V5z"/><path d="M2 5l6 3.5L14 5M8 8.5v6"/></svg></button>`));
  document.getElementById('scene-body')!.prepend(html(`<section id="sc-build" class="first" data-build>
    <div class="bv-head">
      <div class="seg bv-src" role="tablist" aria-label="Model">
        <button role="tab" id="bv-tab-build" data-src="build" aria-selected="true" aria-controls="bv-build-pane">Our build</button>
        <button role="tab" id="bv-tab-lib" data-src="library" aria-selected="false" aria-controls="bv-lib-pane">Library</button>
      </div>
      <button id="bp-reload" class="icon" aria-label="Reload the built assembly">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5V5h-2.5"/></svg></button>
    </div>
    <select id="bp-assembly" aria-label="Manifest" hidden></select>
    <p id="bp-status" class="bh-status" role="status" hidden></p>
    <input id="bv-search" type="search" placeholder="Find a part, joint or assembly&hellip;" aria-label="Find in the build" autocomplete="off" spellcheck="false">
    <div id="bv-build-pane" role="tabpanel" aria-labelledby="bv-tab-build">
      <details id="bv-systems-h" class="bv-sec" data-sec="systems" open><summary>Systems</summary>
        <ul id="bv-systems" class="bv-list" aria-label="Motion systems"></ul></details>
    </div>
    <div id="bv-lib-pane" role="tabpanel" aria-labelledby="bv-tab-lib" hidden>
      <ul id="bv-library" class="bv-list" aria-label="Published designs"></ul>
    </div>
    <details id="bv-ptree-d" class="bv-sec" data-sec="ptree" open><summary>Parts <small id="bv-ptree-n"></small></summary>
      <button id="bp-show-all" class="link" data-bp="show-all" hidden>Show all</button>
      <ul id="bv-ptree" class="ptree" aria-label="Parts by assembly"></ul></details>
    <h3 class="bv-h">View</h3>
    <label class="inline">Explode <input id="bv-explode" type="range" min="0" max="1" step="0.01" value="0"><output id="bv-explode-out">0%</output></label>
    <p id="bv-explode-hint" class="bv-hint" hidden>Focus a system to explode it in detail.</p>
    <button id="bv-fasteners" class="toggle" aria-pressed="true" title="Screws, inserts, nuts and washers">Fasteners</button>
    <details class="bv-section" id="bv-section-d"><summary>Section</summary>
      <div class="row">
        <button id="bv-section" class="toggle-btn" aria-pressed="false" title="Cut the model with a plane">Cut</button>
        <select id="bv-axis" aria-label="Section axis"><option value="x">X (side)</option><option value="y">Y (level)</option><option value="z">Z (front)</option></select>
        <button id="bv-flip" class="icon" aria-pressed="false" aria-label="Flip the kept side" title="Flip the kept side">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 5h8l-2-2M12 11H4l2 2"/></svg></button>
      </div>
      <label class="inline">Position <input id="bv-cut" type="range" min="0" max="1" step="0.005" value="0.5" disabled></label>
    </details>
    <div class="row"><button id="bv-frame" title="Point the camera at what is in focus (F)">Frame <kbd>F</kbd></button></div>
  </section>`));
  // Operating-mode switches: Build first.
  for (const sel of ['.stage-modes', '.rail-modes']) {
    const box = document.querySelector(sel)!;
    const rail = sel === '.rail-modes';
    box.prepend(html(`<button role="radio" aria-checked="false" data-stage-mode="build" ${rail ? 'aria-label="Build" ' : ''}title="Build: inspect and assemble the mechanics. Sim only - never drives hardware">${rail ? 'Bu' : 'Build'}</button>`));
  }
  // The view bar: Instructions (the assembly guide, full screen), Build only.
  document.getElementById('view-bar')!.append(html(`<button id="bb-guide" class="bb-guide" data-build title="Step-by-step assembly, full screen">
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3.5h4a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 0-1.5-1.5h-4zM13.5 3.5h-4A1.5 1.5 0 0 0 8 5v8a1.5 1.5 0 0 1 1.5-1.5h4z"/></svg>Instructions</button>`));
  // Tabs + bodies: Inspect, Checks, BOM (and Electronics, index.html, under More).
  const tabs = document.querySelector('#panel .tabs')!;
  tabs.prepend(html(`<button role="tab" data-tab="inspect" data-modes="build">Inspect</button>
    <button role="tab" data-tab="checks" data-modes="build">Checks <span id="bp-check-badge" class="count" hidden></span></button>
    <button role="tab" data-tab="bom" data-modes="build">BOM</button>`));
  const bodies = html(`
    <div class="tab-body" data-body="inspect" role="tabpanel" hidden>
      <h3 id="bp-title" class="bh-title" hidden></h3>
      <div id="bp-detail" class="bp-drawer" role="region" aria-label="Selected part" hidden></div>
      <div class="bp-bar"><span id="bp-joint-n" class="bp-count"></span>
        <button class="bp-small" data-bp="home" title="Every joint to 0, the rest pose. Moves the model only, never the robot">Home</button></div>
      <div id="bp-joints"></div>
      <details id="bp-load" class="bp-load"><summary>Servo load</summary></details>
    </div>
    <div class="tab-body" data-body="checks" role="tabpanel" hidden>
      <p id="bp-check-sum" class="bp-sum"></p>
      <div id="bv-inspect" hidden>
        <h3 class="bv-h">Overlaps at rest <small id="bv-intf-n"></small></h3>
        <ul id="bv-intf-list" class="intf"></ul>
      </div>
      <div id="bp-torque" hidden><h3 class="bv-h">Servo torque</h3><ul id="bp-torque-list" class="intf"></ul></div>
      <ul id="bp-checks" class="checks"></ul>
    </div>
    <div class="tab-body" data-body="bom" role="tabpanel" hidden>
      <p id="bp-bom-sum" class="bp-sum"></p>
      <div class="table-wrap"><table class="bom"><thead><tr><th scope="col" class="num">Qty</th><th scope="col">Item</th><th scope="col"><span class="sr">Files</span></th></tr></thead>
        <tbody id="bp-bom"></tbody></table></div>
    </div>`);
  tabs.parentElement!.querySelector('#more-menu')!.after(bodies);
}

/**
 * `viewer`: the shared viewer (viewer.html, src/viewer/page.ts) rather than the sim's Build. It
 * opens on the Library, and stepping out of a design goes back to the Library list (in the sim,
 * to our build); the part drawer leaves out the modelling facts (params, fit, mates). Everything
 * else the viewer hides is CSS on body[data-viewer] (src/viewer/viewer.css).
 */
export function mountBuildPanel(wb: Workbench, opts: { viewer?: boolean } = {}) {
  const viewer = !!opts.viewer;
  const st = load();
  // Instructions: the guide opens on what is in focus (its section), else at the start
  const guide = mountGuide(wb, { onClose: () => document.getElementById('bb-guide')?.focus() });
  const openGuide = () => guide.open(wb.scope?.owner ?? null);
  $('bb-guide').onclick = openGuide;
  let index: IndexEntry[] = [];
  /** The manifest open: our build, or a standalone design from the library. */
  let openAsm = OUR_BUILD;
  let loadedOnce = false;
  let jointsDirty = true;
  /** Scene panel: Our build or Library. */
  let src: 'build' | 'library' = viewer ? 'library' : 'build';
  let lastKind: string | null = null;
  /** The navigator's search: filters systems, assemblies and parts by name. */
  let query = '';
  /** The suite's servo torque results (mech/out/checks.json), fetched with the index. */
  let torque: TorqueRow[] = [];

  // ------------------------------------------------------------------ scene panel
  const explode = $<HTMLInputElement>('bv-explode');
  explode.value = String(st.explode ?? 0);
  wb.explode = Number(explode.value);
  explode.oninput = () => {
    wb.setExplode(Number(explode.value));
    $('bv-explode-out').textContent = `${Math.round(Number(explode.value) * 100)}%`;
    st.explode = Number(explode.value);
    save(st);
  };
  $('bv-explode-out').textContent = `${Math.round(wb.explode * 100)}%`;
  if (st.look === 'exterior' || st.look === 'mechanism' || st.look === 'inspect') wb.look = st.look;
  if (st.context === 'ghost' || st.context === 'hide' || st.context === 'solid') wb.context = st.context;
  /** The viewport bar's look is shared: Build's look here, the Model elsewhere (mechrig, `r3x:look`). */
  const setLook = (l: Look) => {
    if (!wb.active) {
      window.dispatchEvent(new CustomEvent('r3x:look', { detail: l }));
      return;
    }
    wb.setLook(l);
    st.look = l;
    save(st);
  };
  document.querySelectorAll<HTMLButtonElement>('[data-look]').forEach((b) => (b.onclick = () => setLook(b.dataset.look as Look)));
  // Look: arrow keys move within the radio group
  $('view-bar').addEventListener('keydown', (e) => {
    const t = e.target as HTMLElement;
    if (!t.dataset.look || (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft')) return;
    const i = LOOKS.indexOf(t.dataset.look as Look);
    const next = LOOKS[(i + (e.key === 'ArrowRight' ? 1 : LOOKS.length - 1)) % LOOKS.length];
    setLook(next);
    document.querySelector<HTMLElement>(`[data-look="${next}"]`)?.focus();
    e.preventDefault();
  });
  document.querySelectorAll<HTMLButtonElement>('[data-src]').forEach((b) => {
    b.onclick = () => {
      src = b.dataset.src as 'build' | 'library';
      // back to our build: the build's own model, whole
      if (src === 'build' && (openAsm !== OUR_BUILD || wb.scope?.kind === 'library')) void openBuild();
      render();
    };
  });
  $('bv-section').onclick = () => wb.setSection({ on: !wb.section.on });
  $<HTMLSelectElement>('bv-axis').onchange = (e) => wb.setSection({ axis: (e.target as HTMLSelectElement).value as 'x' | 'y' | 'z', on: true });
  $<HTMLInputElement>('bv-cut').oninput = (e) => wb.setSection({ at: Number((e.target as HTMLInputElement).value), on: true });
  $('bv-flip').onclick = () => wb.setSection({ flip: !wb.section.flip, on: true });
  wb.fasteners = st.fasteners ?? true;
  $('bv-fasteners').onclick = () => {
    wb.setFasteners(!wb.fasteners);
    st.fasteners = wb.fasteners;
    save(st);
  };
  $('bv-frame').onclick = () => wb.frame(false, true);
  // Navigator sections and tree nodes fold, and stay folded (per browser).
  const closed = new Set(st.closed ?? []);
  document.querySelectorAll<HTMLDetailsElement>('#sc-build details.bv-sec').forEach((d) => {
    if (closed.has(d.dataset.sec!)) d.open = false;
    d.addEventListener('toggle', () => {
      if (d.open) closed.delete(d.dataset.sec!);
      else closed.add(d.dataset.sec!);
      st.closed = [...closed];
      save(st);
    });
  });
  // The parts tree: a caret folds its row; a state button sets the row (and everything under it) solid,
  // ghost or hidden - the context row sets the rest of the droid. Rows the operator opened stay open.
  $('bv-ptree').addEventListener('click', (e) => {
    const t = e.target as HTMLElement;
    const fold = t.closest<HTMLElement>('[data-fold]');
    if (fold) {
      e.stopPropagation();
      const k = fold.dataset.fold!;
      if (opened.has(k)) opened.delete(k);
      else opened.add(k);
      st.opened = [...opened];
      save(st);
      renderTree(true);
      return;
    }
    const v = t.closest<HTMLElement>('[data-vis]');
    if (v) {
      e.stopPropagation();
      const state = v.dataset.vis as Vis;
      if (v.dataset.key === CTX_ROW) {
        wb.setContext(state === 'hidden' ? 'hide' : state);
        st.context = wb.context;
        save(st);
      } else wb.setOverride(v.dataset.key!, state);
    }
  });
  // How it works: a system's joints loop through their range
  $('bv-systems').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('[data-demo]');
    if (!b) return;
    e.stopPropagation();
    if (wb.demoing === b.dataset.demo) wb.stopDemo();
    else wb.startDemo(b.dataset.demo!);
  });
  // Joints off rest outside the focus: one click back
  $('bb-crumbs').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('[data-rest]');
    if (!b) return;
    e.stopPropagation();
    const [key, joint] = b.dataset.rest!.split('|');
    const n = wb.nodeByKey(key);
    if (n) wb.rest(n, joint);
  });
  $<HTMLInputElement>('bv-search').oninput = (e) => {
    query = (e.target as HTMLInputElement).value.trim().toLowerCase();
    render();
  };
  $('bv-intf-list').onclick = (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-k]');
    if (b) wb.frameInterference(Number(b.dataset.k));
  };
  // Systems, assemblies, library: one delegated handler for the scene panel's lists.
  $('sc-build').addEventListener('click', (e) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[data-sys], [data-node], [data-lib], [data-manifest]');
    if (!el) return;
    const d = el.dataset;
    if (d.sys) {
      const on = wb.scope?.kind === 'system' && wb.scope.id === d.sys;
      wb.setScope(on ? null : wb.systemScope(d.sys));
      // the focus's row stays in view; the long lists below fold away
      if (!on) {
        $('bv-systems').querySelector(`[data-sys="${CSS.escape(d.sys)}"]`)?.scrollIntoView({ block: 'nearest' });
      }
    } else if (d.node) {
      const n = wb.nodeByKey(d.node);
      const on = wb.scope?.kind === 'assembly' && wb.scope.id === d.node;
      if (n) wb.setScope(on ? null : wb.assemblyScope(n));
    } else if (d.lib) void openLibrary(d.lib);
    else if (d.manifest) void openManifest(d.manifest);
  });
  $('sc-build').addEventListener('change', (e) => {
    const el = e.target as HTMLSelectElement;
    if (el.dataset.bp !== 'variant-sel') return;
    wb.setVariant(el.dataset.group!, el.value);
    el.blur();
  });
  $('bb-crumbs').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-crumb]');
    if (!b) return;
    if (b.dataset.crumb === 'library') {
      if (viewer) void toLibrary();
      else {
        src = 'library';
        render();
      }
    } else if (b.dataset.crumb === 'build') void openBuild();
  });

  // Keys while Build is open (not while typing): Esc steps out, 1-3 pick the look, F frames.
  addEventListener('keydown', (e) => {
    if (!wb.active || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target as HTMLElement;
    if (t.tagName === 'INPUT' && (t as HTMLInputElement).type !== 'range') return;
    if (t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return;
    if (e.key === 'Escape') {
      if (wb.back()) e.preventDefault();
      else if (viewer && openAsm !== OUR_BUILD) void toLibrary();
      else if (openAsm !== OUR_BUILD) void openBuild();
      return;
    }
    if (t.tagName === 'INPUT') return;
    const k = ['1', '2', '3'].indexOf(e.key);
    if (k >= 0) {
      setLook(LOOKS[k]);
      e.preventDefault();
    } else if (e.key === 'f' || e.key === 'F') {
      wb.frameSelection(); // the selected part, else everything
      e.preventDefault();
    }
  });

  // ------------------------------------------------------------------ manifests
  const pick = $<HTMLSelectElement>('bp-assembly');
  pick.onchange = () => void openManifest(pick.value);
  $('bp-reload').onclick = () => void refreshIndex(true);

  async function openManifest(id: string) {
    const e = index.find((x) => x.id === id);
    if (!e) return;
    openAsm = id;
    pick.value = id;
    await wb.load(MECH_BASE + e.manifest);
    render();
  }

  /** Our build, whole (from a library design or a standalone manifest). */
  async function openBuild() {
    src = 'build';
    if (openAsm !== OUR_BUILD && index.some((x) => x.id === OUR_BUILD)) await openManifest(OUR_BUILD);
    wb.setScope(null);
    wb.frame(false, true);
  }

  /** The viewer: out of a design, back to the Library list (the droid whole behind it). */
  async function toLibrary() {
    await openBuild();
    src = 'library';
    render();
  }

  async function openLibrary(id: string) {
    const item = libraryFrom(wb.manifest?.root).find((x) => x.id === id);
    if (!item) return;
    if (openAsm !== OUR_BUILD) await openManifest(OUR_BUILD);
    const on = wb.scope?.kind === 'library' && wb.scope.id === id;
    if (on) return;
    const sc = wb.libraryScope(item);
    if (sc) wb.setScope(sc);
  }

  let readyR: () => void = () => {};
  const readyP = new Promise<void>((res) => (readyR = res));
  async function refreshIndex(reload = false) {
    index = await loadIndex();
    torque = await loadTorque();
    pick.innerHTML = index.map((e) => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('');
    if (!index.some((e) => e.id === openAsm)) openAsm = index.find((e) => e.id === OUR_BUILD)?.id ?? index[0]?.id ?? '';
    pick.value = openAsm;
    const e = index.find((x) => x.id === openAsm);
    if (e && (reload || !loadedOnce)) {
      loadedOnce = true;
      await wb.load(MECH_BASE + e.manifest);
    }
    render();
    readyR();
  }

  // Live reload (dev server, src/workbench/mech.mjs): a rebuilt manifest reloads the open assembly in
  // place. A child's manifest (Hunter's head under the droid) counts too, so any manifest reloads it.
  if (import.meta.hot) {
    import.meta.hot.on('r3x:mech-manifest', (d: { paths: string[]; t: number }) => {
      if (!wb.url) return;
      const t0 = performance.now();
      void wb.reload().then(() => ((window as unknown as { __r3xBuildReloadAt?: number }).__r3xBuildReloadAt = Date.now()) && console.info(`build: reloaded ${d.paths.join(', ')} in ${Math.round(performance.now() - t0)} ms (file written ${Date.now() - d.t} ms ago)`));
    });
  }

  // Leaving Build: the view bar's looks are the Model's again, none greyed out by a shells-only design
  window.addEventListener('r3x:mode', (e) => {
    if ((e as CustomEvent).detail !== 'build') document.querySelectorAll<HTMLButtonElement>('[data-look]').forEach((b) => (b.disabled = false));
  });
  /** Build opens: fetch the index and our build once. */
  window.addEventListener('r3x:mode', (e) => {
    if ((e as CustomEvent).detail === 'build' && !loadedOnce) void refreshIndex();
  });

  // ------------------------------------------------------------------ delegated clicks
  const onBp = (e: Event) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[data-bp]');
    if (!el) return;
    const d = el.dataset;
    const node = (d.asm && wb.nodeByKey(d.asm)) || wb.focus;
    switch (d.bp) {
      case 'select': wb.select(wb.selected === d.id ? null : d.id!); break;
      case 'deselect': wb.select(null); break;
      case 'isolate': wb.isolate(wb.isolated?.size === 1 && wb.isolated.has(d.id!) ? null : [d.id!]); break;
      case 'isolate-link': {
        const ids = node?.asm.parts.filter((p) => p.link === d.link && (!wb.scope || wb.scope.parts.has(p.id))).map((p) => p.id) ?? [];
        const same = wb.isolated && ids.length === wb.isolated.size && ids.every((i) => wb.isolated!.has(i));
        wb.isolate(same ? null : ids);
        break;
      }
      case 'isolate-node': {
        const ids = wb.partsUnder(d.key!).filter((id) => !wb.scope || wb.scope.parts.has(id));
        const same = wb.isolated && ids.length === wb.isolated.size && ids.every((i) => wb.isolated!.has(i));
        wb.isolate(same ? null : ids);
        break;
      }
      case 'show-all': wb.hidden.clear(); wb.isolate(null); break;
      case 'focus': if (node) wb.setFocus(node); break;
      case 'scope-out': wb.setScope(null); break;
      case 'home': wb.home(); jointsDirty = true; break;
      case 'sweep': if (node) wb.startSweep(node, d.joint!); break;
      case 'guide': openGuide(); break;
      case 'check': {
        const chk = node?.asm.checks?.find((c) => c.id === d.id) ?? null;
        wb.showCheck(wb.check?.id === d.id ? null : chk, node ?? undefined);
        jointsDirty = true;
        break;
      }
      case 'variant': wb.setVariant(d.group!, d.id!); break;
    }
  };
  // Inspect, Checks, BOM in the R3X panel; the parts list in the navigator.
  document.getElementById('panel')!.addEventListener('click', onBp);
  document.getElementById('sc-build')!.addEventListener('click', onBp);
  document.getElementById('panel')!.addEventListener('change', (e) => {
    const el = e.target as HTMLSelectElement;
    if (el.dataset.bp !== 'variant-sel') return;
    wb.setVariant(el.dataset.group!, el.value);
    el.blur();
  });
  document.getElementById('panel')!.addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.dataset.bp !== 'joint') return;
    const node = wb.nodeByKey(el.dataset.asm!);
    if (!node) return;
    wb.setJoint(node, el.dataset.joint!, Number(el.value));
    wb.setHover(node, el.dataset.joint!);
  });
  // A joint under the pointer (or being dragged, or focused) tints the parts it moves.
  const jointsBody = $('bp-joints');
  let dragging = false;
  const hoverFrom = (el: Element | null) => {
    const row = el?.closest<HTMLElement>('.joint[data-asm]');
    const node = row ? wb.nodeByKey(row.dataset.asm!) : null;
    wb.setHover(node, row?.dataset.joint ?? null);
  };
  jointsBody.addEventListener('pointerover', (e) => { if (!dragging) hoverFrom(e.target as Element); });
  jointsBody.addEventListener('pointerleave', () => { if (!dragging) wb.setHover(null, null); });
  jointsBody.addEventListener('pointerdown', () => (dragging = true));
  addEventListener('pointerup', (e) => {
    if (!dragging) return;
    dragging = false;
    if (!jointsBody.contains(e.target as Node)) wb.setHover(null, null);
  });
  jointsBody.addEventListener('focusin', (e) => hoverFrom(e.target as Element));
  jointsBody.addEventListener('focusout', (e) => { if (!jointsBody.contains(e.relatedTarget as Node)) wb.setHover(null, null); });

  // Parts: Up/Down move between rows, Escape closes the part drawer.
  $('bv-ptree').addEventListener('keydown', (e) => {
    const t = e.target as HTMLElement;
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const rows = [...$('bv-ptree').querySelectorAll<HTMLElement>('button.nm, button.nm-n')];
    const i = rows.indexOf(t);
    if (i < 0) return;
    rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))].focus();
    e.preventDefault();
  });
  $('sc-build').addEventListener('keydown', (e) => {
    if ((e as KeyboardEvent).key === 'Escape' && wb.selected) {
      e.stopPropagation(); // only the drawer closes, the scope stays
      const id = wb.selected;
      wb.select(null);
      $('bv-ptree').querySelector<HTMLElement>(`button.nm[data-id="${CSS.escape(id)}"]`)?.focus();
    }
  });

  // Entering and leaving the Checks tab.
  document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => b.addEventListener('click', () => onTab(b.dataset.tab!)));
  function onTab(tab: string) {
    if (tab !== 'checks' && wb.check) wb.showCheck(null);
    wb.setMarkers(tab === 'checks'); // the overlap markers are the Checks tab's, not a look's
  }


  wb.onChange(() => render());
  // a drag on the model: the sliders follow it every frame (the rest of the panel when it ends)
  wb.onPose(() => { if (wb.active) renderJoints(); });

  // ------------------------------------------------------------------ render
  function render() {
    renderView();
    if (!wb.active) return;
    // overlap markers follow the Checks tab (also when Build reopens on it)
    wb.setMarkers(document.querySelector('[data-tab="checks"]')?.getAttribute('aria-selected') === 'true');
    renderNav();
    renderHead();
    renderTree();
    renderDetail();
    renderJoints();
    renderChecks();
    renderBom();
  }

  function renderView() {
    const press = (id: string, on: boolean) => $(id).setAttribute('aria-pressed', String(on));
    // The viewport bar's look is Build's only while Build is open (mechrig's Model otherwise).
    if (wb.active) document.querySelectorAll<HTMLButtonElement>('[data-look]').forEach((b) => {
      // the presets set the tree: once the tree is changed (wb.custom), none of them is what is shown
      const on = !wb.custom && b.dataset.look === wb.look;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = b.dataset.look === wb.look ? 0 : -1;
      // a shells-only design has the one look (workbench shellsOnly): the others greyed out, not gone
      b.disabled = wb.shellsOnly && b.dataset.look !== 'exterior';
    });
    // (the rest of the droid is the parts tree's context row, there only with a focus: renderTree)
    $('bv-explode-hint').hidden = !(wb.explode > 0.05 && !wb.scope);
    press('bv-section', wb.section.on);
    press('bv-flip', wb.section.flip);
    press('bv-fasteners', wb.fasteners);
    // the pairs in focus (a scope: those touching it); `k` stays the pair's index in the suite's list
    const pairs = wb.interference.map((p, k) => ({ ...p, k })).filter((p) => wb.pairInScope(p));
    const open = pairs.filter((p) => !p.explained).length;
    // Overlaps live in Checks (with the rest of the suite's results), whatever the look.
    $('bv-inspect').hidden = !wb.interference.length;
    $('bv-intf-n').textContent = open ? `${open} to fix` : '';
    const list = $('bv-intf-list');
    // the picked pair (its marker is in the view): what it is, and whether the pose still allows it
    const sigSel = wb.pairSel !== null ? `#${wb.pairSel}:${wb.pairState(wb.pairSel)}` : '';
    const sig = (pairs.map((p) => `${p.a}|${p.b}|${p.depth_mm}`).join() || 'none') + sigSel;
    if (list.dataset.sig !== sig) {
      list.dataset.sig = sig;
      const short = (id: string) => shortName(wb.partInfo(id.includes('/') && !wb.partInfo(id) ? id.slice(id.indexOf('/') + 1) : id)?.part.name ?? id);
      const row = (p: (typeof pairs)[number]) => {
        const on = wb.pairSel === p.k;
        const apart = on && wb.pairState(p.k) === 'apart';
        return `<li class="${p.explained ? 'known' : 'hot'}${on ? ' on' : ''}"><button data-k="${p.k}" aria-pressed="${on}" title="${esc(`${p.a} x ${p.b}: ${p.depth_mm} mm deep${p.volume_mm3 != null ? `, ${p.volume_mm3} mm³ shared` : ''}${p.explained ? ' (explained)' : ''}. Click to show it`)}">
        <span>${esc(short(p.a))} × ${esc(short(p.b))}</span><span class="d">${num(p.depth_mm, 1)} mm</span></button>
        ${on ? `<p class="intf-sel">${esc(short(p.a))} and ${esc(short(p.b))} share ${num(p.depth_mm, 1)} mm${p.volume_mm3 != null ? ` (${num(p.volume_mm3, 0)} mm³)` : ''}${p.explained ? ', explained' : ''}.${apart ? ` <span class="dim">Checked at the rest pose.</span> <button class="link" data-bp="home">Go to rest pose</button>` : ''}</p>` : ''}</li>`;
      };
      const known = pairs.filter((p) => p.explained);
      list.innerHTML = (pairs.filter((p) => !p.explained).map(row).join('')
        + (known.length ? `<li class="known-group"><details class="bp-disc"><summary>${known.length} explained</summary><ul class="intf">${known.map(row).join('')}</ul></details></li>` : ''))
        || '<li class="empty">No overlaps here.</li>';
    }
    $<HTMLSelectElement>('bv-axis').value = wb.section.axis;
    $<HTMLInputElement>('bv-cut').disabled = !wb.section.on;
  }

  /** Scene panel lists (systems, assemblies, library) and the viewport's breadcrumb. */
  function renderNav() {
    const sc = wb.scope;
    const lib = sc?.kind === 'library' || openAsm !== OUR_BUILD;
    if (lib) src = 'library';
    // stepped out of a design: back on our build (the viewer: the list its caller picked - Library, or Our build)
    else if (lastKind === 'library' && !viewer) src = 'build';
    lastKind = lib ? 'library' : sc?.kind ?? null;
    document.querySelectorAll<HTMLButtonElement>('[data-src]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.src === src)));
    $('bv-build-pane').hidden = src !== 'build';
    $('bv-lib-pane').hidden = src !== 'library';

    // breadcrumb: Our build > Neck, or Library > Head gimbal
    const here = sc?.label ?? (openAsm !== OUR_BUILD ? index.find((x) => x.id === openAsm)?.name ?? openAsm : '');
    const root = lib ? `<button data-crumb="library" title="${viewer ? 'Back to the Library' : 'Back to the build'} (Esc)">Library</button>` : `<button data-crumb="build" ${sc ? 'title="The whole build (Esc)"' : 'aria-current="page" disabled'}>Our build</button>`;
    // what upstream is off rest and moves the focus (a turned ring under the arm): shown, one click home
    const up = wb.upstream().map(({ node, joint, value }) => `<button class="bb-chip" data-rest="${esc(node.key)}|${esc(joint.id)}" title="Off rest: it moves ${esc(sc?.label ?? '')}. Click to put it back to 0">${esc(jointLabel(joint.name))} ${signed(value)}${unit(joint)} <span aria-hidden="true">⟲</span></button>`).join('');
    $('bb-crumbs').innerHTML = here ? `${root}<span aria-hidden="true">›</span><b aria-current="page">${esc(here)}</b>${up}` : '';
    $('bb-crumbs').hidden = !here; // at the top there is nowhere to step out to

    if (!wb.top) return;
    // systems, with their joints as one short line
    const hit = (...names: string[]) => !query || names.some((n) => n.toLowerCase().includes(query));
    const systems = wb.systems().filter((x) => hit(x.name, ...x.joints.map((j) => jointLabel(j.joint.name))));
    const sysSig = systems.map((x) => x.id + x.joints.map((j) => j.joint.id).join()).join('|') + (sc?.kind === 'system' ? sc.id : '') + '?' + query + '>' + (wb.demoing ?? '');
    const sysEl = $('bv-systems');
    if (sysEl.dataset.sig !== sysSig) {
      sysEl.dataset.sig = sysSig;
      sysEl.innerHTML = systems.map((x) => {
        const on = sc?.kind === 'system' && sc.id === x.id;
        const tip = x.joints.map((j) => `${jointLabel(j.joint.name)}: ${num(j.joint.limits.min)}…${signed(j.joint.limits.max)} ${unit(j.joint).trim()}`).join('\n');
        const play = wb.demoing === x.id;
        return `<li class="sys${on ? ' on' : ''}"><button data-sys="${esc(x.id)}" aria-pressed="${on}" title="${esc(tip)}"><span>${esc(x.name)}</span><small>${esc(systemMotion(x))}</small></button>
          <button class="ic demo" data-demo="${esc(x.id)}" aria-pressed="${play}" aria-label="${play ? 'Stop' : 'How it works'}: ${esc(x.name)}" title="${play ? 'Stop' : 'How it works: loop its joints, slowly'}">${play ? STOP : PLAY}</button></li>`;
      }).join('') || (query ? '' : '<li class="empty">No moving joints.</li>');
    }
    // A search shows only the groups it found something in.
    $('bv-systems-h').hidden = !!query && !systems.length;
    // library: the designs the droid is assembled from, and standalone manifests
    const items = libraryFrom(wb.manifest?.root).filter((x) => libraryAvailable(x, wb.top!));
    const standalone = index.filter((e) => e.id !== OUR_BUILD && !items.some((x) => x.nodes.includes(e.id)));
    const libSig = items.map((x) => x.id).join() + standalone.map((x) => x.id).join() + (sc?.kind === 'library' ? sc.id : '') + openAsm;
    const libEl = $('bv-library');
    if (libEl.dataset.sig !== libSig) {
      libEl.dataset.sig = libSig;
      libEl.innerHTML = items.map((x) => `<li><button class="lib" data-lib="${esc(x.id)}" aria-pressed="${sc?.kind === 'library' && sc.id === x.id}" title="${esc(x.source ?? '')}"><span>${esc(x.name)}</span><small>${esc(x.by)}</small></button></li>`).join('')
        + standalone.map((x) => `<li><button data-manifest="${esc(x.id)}" aria-pressed="${openAsm === x.id}"><span>${esc(assemblyLabel(x.name))}</span><small>own build</small></button></li>`).join('');
    }
  }

  function renderHead() {
    const m = wb.manifest;
    const status = $('bp-status');
    let msg = '';
    if (wb.loading) msg = 'Loading…';
    else if (wb.error) msg = esc(wb.error);
    else if (!index.length) msg = 'Nothing built yet: <code>cd mech &amp;&amp; .venv/bin/python -m workbench build r3x_droid</code>';
    status.innerHTML = msg;
    status.hidden = !msg;
    status.classList.toggle('err', !!wb.error);
    $('bp-reload').title = m ? `Reload · built ${new Date(m.generated_at).toLocaleString()}` : 'Reload';
    // The inspector's title: what is in focus (nothing at the top of our build: the navigator says it).
    const sc = wb.scope;
    const title = sc ? sc.label : openAsm === OUR_BUILD ? '' : assemblyLabel(m?.root.name ?? openAsm);
    const sub = sc?.kind === 'library' ? sc.item?.by ?? '' : '';
    $('bp-title').hidden = !title;
    const steps = sc ? ` <button class="link bh-guide" data-bp="guide" title="Step-by-step assembly of ${esc(title)}, full screen">Instructions</button>` : '';
    $('bp-title').innerHTML = !title ? '' : `<span class="bh-name">${esc(title)}</span>${sub ? ` <small>${esc(sub)}</small>` : ''}${steps}${sc ? ' <button class="ic" data-bp="scope-out" aria-label="Back to the whole build" title="Back to the whole build (Esc)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button>' : ''}`;
  }

  // ---------------------------------------------------------------- the parts tree
  /**
   * The focus's parts by assembly (visibility.ts): the context row (the rest of the droid, with a focus),
   * then the focus's root - the deepest assembly holding all of its parts, named for the focus - and under
   * it sub-assemblies, then parts. Every row sets itself and everything under it solid, ghost or hidden; a
   * row whose parts differ shows mixed. Rows open by the caret (remembered); the path to a part picked in
   * the view opens by itself. A search shows the rows with a match, open.
   */
  const opened = new Set(st.opened ?? []);
  let treeHtml = '';
  let lastSel: string | null = null;
  const tri = (key: string, state: Vis | 'mixed' | null, label: string) => `<span class="tri${state === 'mixed' ? ' mixed' : ''}" role="radiogroup" aria-label="${esc(label)}">${
    VIS_BUTTONS.map(([v, icon, name]) => `<button role="radio" data-vis="${v}" data-key="${esc(key)}" aria-checked="${state === v}" aria-label="${name}" title="${name}">${icon}</button>`).join('')}</span>`;

  function renderTree(force = false) {
    const el = $('bv-ptree');
    const top = wb.top;
    if (!top) {
      el.innerHTML = treeHtml = '';
      return;
    }
    const sc = wb.scope;
    const inFocus = (id: string) => !sc || sc.parts.has(id);
    const label = (n: AsmNode) => assemblyLabel(n.asm.name);
    const named = (p: MPart, n: AsmNode) => {
      if (!query) return true;
      if (p.name.toLowerCase().includes(query) || p.id.toLowerCase().includes(query)) return true;
      for (let m: AsmNode | null = n; m; m = m.parent) if (label(m).toLowerCase().includes(query)) return true;
      return false;
    };
    // each shown node's own focus parts, and how many sit under each node
    const own = new Map<string, MPart[]>();
    const count = new Map<string, number>();
    let total = 0;
    wb.forEachNode((n) => {
      if (!wb.nodeShown(n)) return;
      const ps = n.asm.parts.filter((p) => inFocus(p.id) && named(p, n));
      own.set(n.key, ps);
      total += ps.length;
      for (let m: AsmNode | null = n; m; m = m.parent) count.set(m.key, (count.get(m.key) ?? 0) + ps.length);
    });
    // the root: the deepest node holding every one of them
    let root = top;
    for (;;) {
      const all = root.children.filter((c) => (count.get(c.key) ?? 0) === total && total > 0);
      if (all.length !== 1 || (own.get(root.key)?.length ?? 0) > 0) break;
      root = all[0];
    }
    // the path to the part picked in the view opens
    const sel = wb.selected ? wb.partInfo(wb.selected)?.node ?? null : null;
    const selPath = new Set<string>();
    for (let m: AsmNode | null = sel; m; m = m.parent) selPath.add(m.key);
    const rows: string[] = [];
    if (sc) {
      rows.push(`<li class="pt-row pt-ctx" style="--d:0"><span class="fold"></span><span class="nm-l">Rest of the droid</span>${tri(CTX_ROW, contextVis(wb.context), 'Rest of the droid')}</li>`);
    }
    const seenGroups = new Set<string>();
    const solo = (ids: string[]) => !!wb.isolated && ids.length === wb.isolated.size && ids.every((i) => wb.isolated!.has(i));
    const walk = (n: AsmNode, d: number) => {
      const isRoot = n === root;
      const kids = n.children.filter((c) => wb.nodeShown(c) && (count.get(c.key) ?? 0) > 0);
      const parts = own.get(n.key) ?? [];
      const open = isRoot || !!query || opened.has(n.key) || selPath.has(n.key);
      const caret = !isRoot && (kids.length || parts.length)
        ? `<button class="fold" data-fold="${esc(n.key)}" aria-expanded="${open}" aria-label="${open ? 'Collapse' : 'Expand'} ${esc(label(n))}">${open ? '▾' : '▸'}</button>` : '<span class="fold"></span>';
      const name = isRoot ? (sc?.label ?? label(n)) : label(n);
      const on = sc?.kind === 'assembly' && sc.id === n.key;
      const nm = isRoot ? `<span class="nm-l" title="${esc(n.asm.name)}">${esc(name)}</span>`
        : `<button class="nm-n" data-node="${esc(n.key)}" aria-pressed="${on}" title="${esc(n.asm.name)}">${esc(name)}</button>`;
      const ids = wb.partsUnder(n.key).filter(inFocus);
      rows.push(`<li class="pt-row pt-node${isRoot ? ' pt-root' : ''}" style="--d:${d}">${caret}${nm}<small>${count.get(n.key) ?? 0}</small>`
        + `<button class="ic solo" data-bp="isolate-node" data-key="${esc(n.key)}" aria-pressed="${solo(ids)}" aria-label="Show only ${esc(name)}" title="Solo">${SOLO}</button>`
        + `${tri(n.key, wb.rowState(n.key, inFocus), name)}</li>`);
      if (!open) return;
      // variant groups hang where their options do (our build's own picks; a library design brings its own)
      if (!query && sc?.kind !== 'library') {
        for (const v of variantOptions(n.asm)) {
          if (seenGroups.has(v.group)) continue;
          seenGroups.add(v.group);
          const opts: { id: string; name: string }[] = [];
          wb.forEachNode((m) => variantOptions(m.asm).filter((o) => o.group === v.group && !opts.some((x) => x.id === o.id)).forEach((o) => opts.push({ id: o.id, name: o.name })));
          rows.push(`<li class="pt-var" style="--d:${d + 1}">${variantSelect(v.group, opts, wb.variants[v.group])}</li>`);
        }
      }
      for (const c of kids) walk(c, d + 1);
      for (const p of parts) {
        const s1 = wb.isolated?.size === 1 && wb.isolated.has(p.id);
        rows.push(`<li class="pt-row pt-part${wb.selected === p.id ? ' sel' : ''}" style="--d:${d + 1}"><span class="fold"></span>`
          + `<button class="nm" data-bp="select" data-id="${esc(p.id)}" title="${esc(p.name)}" aria-current="${wb.selected === p.id}">${esc(shortName(p.name))}</button>`
          + `<button class="ic solo" data-bp="isolate" data-id="${esc(p.id)}" aria-pressed="${s1}" aria-label="Show only ${esc(p.name)}" title="Solo">${SOLO}</button>`
          + `${tri(partKey(p.id), wb.treeStates.get(p.id) ?? null, p.name)}</li>`);
      }
    };
    if (total) walk(root, 0);
    else rows.push(`<li class="empty">${query ? 'No parts match.' : 'No parts here.'}</li>`);
    const html = rows.join('');
    $('bv-ptree-n').textContent = String(total);
    $('bp-show-all').hidden = !wb.isolated && !wb.hidden.size;
    if (html === treeHtml && !force) return;
    // rebuilt: keep the keyboard where it was (the same row's same button)
    const a = document.activeElement as HTMLElement | null;
    const keep = a && el.contains(a) ? (a.dataset.vis ? `[data-vis="${a.dataset.vis}"][data-key="${CSS.escape(a.dataset.key ?? '')}"]`
      : a.dataset.id ? `[data-bp="${a.dataset.bp}"][data-id="${CSS.escape(a.dataset.id)}"]` : a.dataset.fold ? `[data-fold="${CSS.escape(a.dataset.fold)}"]` : '') : '';
    el.innerHTML = treeHtml = html;
    if (keep) el.querySelector<HTMLElement>(keep)?.focus();
    if (wb.selected !== lastSel) {
      lastSel = wb.selected;
      el.querySelector('.pt-part.sel')?.scrollIntoView({ block: 'nearest' });
    }
  }

  function renderDetail() {
    const el = $('bp-detail');
    const id = wb.selected;
    el.hidden = !id;
    if (!id) {
      el.innerHTML = '';
      return;
    }
    const head = (name: string, glyph: string) => `<div class="dr-head">${glyph}<b title="${esc(name)}">${esc(name)}</b>
      <button class="icon" data-bp="deselect" aria-label="Close" title="Close (Esc)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button></div>`;
    const row = (k: string, v: string, tip = '') => (v ? `<dt>${k}</dt><dd${tip ? ` title="${esc(tip)}"` : ''}>${v}</dd>` : '');
    const info = wb.partInfo(id);
    if (!info) {
      const f = wb.fastenerInfo(id);
      if (!f) {
        el.hidden = true;
        return;
      }
      el.innerHTML = head(fastLabel(f.spec), srcDot(f.cad ?? 'parametric')) + `<dl class="facts">
        ${row('Joins', f.joins.map((p) => esc(shortName(wb.partInfo(p)?.part.name ?? p))).join(' + '))}
        ${row('Step', esc(stepTitle(wb.focus?.asm, f.step)))}
        ${row('Catalog', esc(f.catalog ?? ''))}</dl>
        ${f.inferred ? `<p class="dr-inf">Inferred: ${esc(f.inferred_note || 'not confirmed')}</p>` : ''}`;
      return;
    }
    const { part: p, joint: j, node } = info;
    const src = p.source ?? {};
    const exp = p.export ?? {};
    const mates = ((node.asm as MAssembly & { mates?: { a: { part: string }; b: { part: string } }[] }).mates ?? [])
      .filter((m) => m.a?.part === p.id || m.b?.part === p.id).length;
    const params = src.params as Record<string, unknown> | undefined;
    const reg = src.regression as { p95_mm?: number; mean_mm?: number; volume_ratio?: number } | undefined;
    const file = (src.file as string | undefined) ?? (src.reference as string | undefined) ?? (src.model as string | undefined) ?? '';
    el.innerHTML = head(shortName(p.name), srcDot(p.cad)) + `<dl class="facts">
      ${viewer ? '' : row('Source', SRC_LABEL[srcOf(p.cad)], SRC_TITLE[srcOf(p.cad)] + (file ? `\n${file}` : '') + (src.placement ? `\nPlaced: ${src.placement}` : ''))}
      ${row('Material', esc(p.material ?? ''))}
      ${row('Joint', j ? `${esc(bare(j.name))}${j.profile_joint ? ` <span class="mono dim">${esc(j.profile_joint)}</span>` : ''}` : 'fixed', j?.name)}
      ${viewer ? '' : row('Mates', mates ? String(mates) : '')}
      ${params && !viewer ? row('Params', Object.entries(params).map(([k, v]) => `<span class="mono">${esc(k)} ${esc(v)}</span>`).join(' · ')) : ''}
      ${reg && !viewer ? row('Fit', `p95 ${num(reg.p95_mm ?? 0, 2)} mm`, `vs ${String(src.reference ?? 'reference')}: mean ${num(reg.mean_mm ?? 0, 2)} mm, volume ${num((reg.volume_ratio ?? 1) * 100, 1)}%`) : ''}
      ${p.mass_g ? row('Mass', `${num(p.mass_g)} g`, p.mass_note ?? '') : ''}
      ${!viewer && (exp.stl || exp['3mf']) ? row('Export', `${exp.stl ? `<a href="${esc(joinUrl(node.asm.base ?? '/', exp.stl))}" download>STL</a>` : ''}${exp['3mf'] ? `<a href="${esc(joinUrl(node.asm.base ?? '/', exp['3mf']))}" download>3MF</a>` : ''}`) : ''}</dl>
      ${p.inferred && !viewer ? `<p class="dr-inf">Inferred: ${esc(p.inferred_note || 'placement or part not confirmed')}</p>` : ''}
      ${p.note && !viewer ? `<details class="bp-disc"><summary>Note</summary><p>${esc(p.note)}</p></details>` : ''}`;
  }

  // ---------------------------------------------------------------- Joints
  /** The joints in focus: the scope's, else every fitted joint under the focused node. Only the
   *  picked variants' (the column's lift, not Anderson's as well). */
  function focusJoints(): { n: AsmNode; j: MJoint }[] {
    if (wb.scope) return wb.scope.joints.map(({ node, joint }) => ({ n: node, j: joint }));
    const out: { n: AsmNode; j: MJoint }[] = [];
    wb.forEachNode((n) => { if (wb.nodeShown(n)) n.asm.joints.forEach((j) => out.push({ n, j })); }, wb.focus ?? undefined);
    return out;
  }

  let jointSig = '';
  function renderJoints() {
    const body = $('bp-joints');
    if (!wb.focus) {
      body.innerHTML = '';
      return;
    }
    const list = focusJoints();
    const total = list.length;
    $('bp-joint-n').textContent = total ? `${total} joint${total === 1 ? '' : 's'}` : '';
    const sig = list.map(({ n, j }) => `${n.key}:${j.id}`).join('|') + wb.sweeping;
    if (sig !== jointSig || jointsDirty) {
      jointSig = sig;
      jointsDirty = false;
      body.innerHTML = list.map(({ n, j }) => {
        const v = n.pose[j.id] ?? 0;
        const pl = j.profile_limits;
        const u = unit(j);
        const tip = `${j.name}\n${num(j.limits.min)}…${signed(j.limits.max)}${u}${pl ? ` (profile ${num(pl.min)}…${signed(pl.max)})` : ''} · ${driveLabel(j.drive)}`;
        const k = `${esc(n.key)}:${esc(j.id)}`;
        const fixed = !(j.limits.max > j.limits.min);
        return `<div class="joint${fixed ? ' fixed' : ''}" data-asm="${esc(n.key)}" data-joint="${esc(j.id)}">
          <div class="jhead"><b title="${esc(tip)}">${esc(jointLabel(j.name))}</b>${j.drive?.servos?.length || fixed ? '' : ' <span class="ptag none" title="No servo: it moves by hand">free</span>'}${profileTag(j.profile_joint, j.name)}${j.inferred ? `<sup class="inf" title="Inferred: ${esc(j.inferred_note ?? '')}">?</sup>` : ''}
            <output data-out="${k}">${signed(v)}${u}</output>
            <button class="ic" data-bp="sweep" data-asm="${esc(n.key)}" data-joint="${esc(j.id)}" aria-label="Sweep ${esc(j.name)}" title="Sweep through the range; the first contact lights up" aria-pressed="${wb.sweeping === j.id}" ${fixed ? 'disabled' : ''}>${SWEEP}</button></div>
          <input type="range" data-bp="joint" data-asm="${esc(n.key)}" data-joint="${esc(j.id)}"
            min="${j.limits.min}" max="${j.limits.max}" step="0.5" value="${v}" aria-label="${esc(jointLabel(j.name))}" ${fixed ? 'disabled' : ''}>
          <div class="jlive" data-jlive="${k}"></div></div>`;
      }).join('') || '<p class="empty">No joints here.</p>';
    }
    // live values: slider positions, servo angles, contact
    for (const { n, j } of list) {
      const k = `${n.key}:${j.id}`;
      const v = n.pose[j.id] ?? 0;
      const inp = body.querySelector<HTMLInputElement>(`input[data-asm="${CSS.escape(n.key)}"][data-joint="${CSS.escape(j.id)}"]`);
      if (inp && document.activeElement !== inp) inp.value = String(v);
      const out = body.querySelector(`[data-out="${CSS.escape(k)}"]`);
      if (out) out.textContent = `${signed(v)}${unit(j)}`;
      const live = body.querySelector<HTMLElement>(`[data-jlive="${CSS.escape(k)}"]`);
      if (!live) continue;
      const bits: string[] = [];
      for (const lid of j.drive?.linkages ?? []) {
        const r = n.rods.get(lid);
        const lk = n.asm.linkages?.find((x) => x.id === lid);
        bits.push(r ? `${esc(lk?.servo ?? lid)} ${signed(r.servoDeg)}°` : `<span class="bad">${esc(lk?.servo ?? lid)} out of reach</span>`);
      }
      const chk = n.asm.checks?.find((c) => c.id === `interference_${j.id}`);
      if (chk?.value != null) bits.push(`contact ${signed(chk.value)}${unit(j)}`);
      if (wb.sweeping === j.id && wb.contact) bits.push(`<span class="${wb.contact.status === 'fail' ? 'bad' : 'meh'}">touching ${wb.contact.parts.map(esc).join(' × ')}</span>`);
      live.innerHTML = bits.join(' · ');
      live.hidden = !bits.length;
    }
  }

  // ---------------------------------------------------------------- Checks
  function renderChecks() {
    const a = wb.focus?.asm;
    const sc = wb.scope;
    const scJoints = new Set(sc?.joints.map((x) => x.joint.id) ?? []);
    // a scope's: checks on its parts or its joints
    const checks = (a?.checks ?? []).filter((c) => !sc || sc.kind === 'assembly' || (c.parts ?? []).some((p) => sc.parts.has(p)) || (!!c.joint && scJoints.has(c.joint)));
    const counts = { pass: 0, warn: 0, fail: 0, explained: 0 };
    checks.forEach((c) => counts[c.status]++);
    // Every result the suite has for this focus: the checks, the overlaps at rest, servo torque.
    const pairs = wb.interference.filter((p) => wb.pairInScope(p));
    const overlapsOpen = pairs.filter((p) => !p.explained).length;
    const tq = torqueInScope();
    const tqFail = tq.filter((t) => t.status === 'fail').length;
    const tqWarn = tq.filter((t) => t.status !== 'fail' && (t.status === 'warn' || t.speed_ok === false)).length;
    const v = checkVerdict({ checks: counts, overlapsOpen, overlapsExplained: pairs.length - overlapsOpen, torqueFail: tqFail, torqueWarn: tqWarn, torquePass: tq.length - tqFail - tqWarn });
    $('bp-check-sum').textContent = v.summary;
    const badge = $('bp-check-badge');
    badge.textContent = v.toFix ? String(v.toFix) : '';
    badge.hidden = !v.toFix;
    renderTorque(tq);
    const row = (c: MCheck) => {
      const b = brief(c);
      const on = wb.check?.id === c.id;
      const more = b.items.length > 1 || c.assumptions?.length;
      return `<li class="chk ${c.status}${on ? ' on' : ''}">
      <button data-bp="check" data-id="${esc(c.id)}" data-asm="${esc(wb.focus!.key)}" aria-pressed="${on}" title="${on ? 'Back to the rest pose' : 'Pose the model where this happens'}">
        <span class="st ${c.status}" role="img" aria-label="${c.status}" title="${c.status}">${STATUS_GLYPH[c.status]}</span><b title="${esc(c.title)}">${esc(c.title)}</b>
        ${b.cause ? `<span class="l1">${esc(b.cause)}</span>` : ''}${b.fix ? `<span class="l2">→ ${esc(b.fix)}</span>` : ''}</button>
      ${more ? `<details class="bp-disc"><summary${c.seconds != null ? ` title="Ran in ${num(c.seconds, 2)} s"` : ''}>Details</summary>
        ${b.items.length > 1 ? `<ul>${b.items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${c.assumptions?.length ? `<p class="dim">Assumes: ${c.assumptions.map(esc).join('; ')}</p>` : ''}</details>` : ''}</li>`;
    };
    const order = { fail: 0, explained: 1, warn: 2, pass: 3 };
    const tests = checks.filter((c) => c.kind === 'test').sort((x, y) => order[x.status] - order[y.status]);
    const eng = checks.filter((c) => c.kind !== 'test');
    $('bp-checks').innerHTML = (tests.length ? `<li class="grp">Assembly tests</li>${tests.map(row).join('')}` : '')
      + (eng.length ? `<li class="grp">Engineering checks</li>${eng.map(row).join('')}` : '')
      || `<li class="empty">${a?.checks?.length ? 'No checks here.' : 'No checks in this build. Build without <code>--no-checks</code>.'}</li>`;
    const tab = document.querySelector<HTMLElement>('[data-tab="checks"]');
    if (tab) tab.dataset.status = v.toFix ? 'fail' : v.warn ? 'warn' : 'pass';
  }

  /** The suite's torque rows for the joints in focus (every row at the top). */
  function torqueInScope(): TorqueRow[] {
    const sc = wb.scope;
    if (!sc) return torque;
    const mine = new Set(sc.joints.map((x) => x.joint.profile_joint ?? x.joint.id));
    return torque.filter((t) => mine.has(t.joint));
  }

  function renderTorque(rows: TorqueRow[]) {
    $('bp-torque').hidden = !rows.length;
    const glyph = (t: TorqueRow) => (t.status === 'fail' ? 'fail' : t.status === 'warn' || t.speed_ok === false ? 'warn' : 'pass');
    // The profile joint as the build names it ("head_pan" -> "Head pan" from the manifest).
    const names = new Map<string, string>();
    wb.forEachNode((n) => n.asm.joints.forEach((j) => { if (j.profile_joint && !names.has(j.profile_joint)) names.set(j.profile_joint, jointLabel(j.name)); }));
    const label = (id: string) => names.get(id) ?? (id.charAt(0).toUpperCase() + id.slice(1).replace(/_/g, ' '));
    $('bp-torque-list').innerHTML = rows.map((t) => {
      const g = glyph(t);
      const tip = `${t.servo}: ${num(t.servo_torque_kgcm ?? 0, 2)} of ${num(t.stall_kgcm ?? 0, 1)} kg·cm at the worst pose`
        + (t.speed_ok === false ? `; too slow - needs ${num(t.servo_speed_needed_dps ?? 0, 0)}°/s, gets ${num(t.loaded_speed_dps ?? 0, 0)}°/s loaded` : '');
      return `<li class="tq tq-${g}" title="${esc(tip)}"><span class="st ${g}" role="img" aria-label="${g}">${STATUS_GLYPH[g]}</span><span>${esc(label(t.joint))}</span>
        <span class="d">${Math.round((t.fraction_of_stall ?? 0) * 100)}% of stall${t.speed_ok === false ? ' · slow' : ''}</span></li>`;
    }).join('');
  }

  // ---------------------------------------------------------------- BOM
  function renderBom() {
    const a = wb.focus?.asm;
    const sc = wb.scope;
    const fastOn = (ids: string[] = []) => ids.some((f) => wb.fastenerInfo(f)?.joins.some((p) => sc!.parts.has(p)));
    // a scope's: lines for its parts (and the fasteners that join them)
    const lines = (a?.bom_rollup?.length ? a.bom_rollup : a?.bom ?? [])
      .filter((b) => !sc || sc.kind === 'assembly' || (b.parts ?? []).some((p) => sc.parts.has(p)) || fastOn(b.fasteners));
    const cats = [...new Set(lines.map((b) => b.category))];
    const exportsFor = (ids: string[] = []) => ids.map((id) => wb.partInfo(id)).filter(Boolean).map((i) => {
      const e = i!.part.export ?? {};
      const base = i!.node.asm.base ?? '/';
      return [e.stl ? `<a href="${esc(joinUrl(base, e.stl))}" download>STL</a>` : '', e['3mf'] ? `<a href="${esc(joinUrl(base, e['3mf']))}" download>3MF</a>` : ''].filter(Boolean).join(' ');
    }).join(' ');
    const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
    $('bp-bom').innerHTML = cats.map((cat) => `<tr class="grp"><th colspan="3" scope="rowgroup">${esc(cap(cat))} <span>${lines.filter((b) => b.category === cat).length}</span></th></tr>` + lines.filter((b) => b.category === cat).map((b) => `<tr>
      <td class="num">${num(b.qty, 0)}</td>
      <td>${b.source ? `<a href="${esc(b.source)}" target="_blank" rel="noopener">${esc(b.item)}</a>` : esc(b.item)}${b.inferred ? `<sup class="inf" title="Inferred: ${esc(b.inferred_note ?? '')}">?</sup>` : ''}</td>
      <td class="exp">${cat === 'printed' ? exportsFor(b.parts) : ''}</td></tr>`).join('')).join('');
    // the print list: this view's printed parts (current picks, replaced ones out) by filament and colour
    const ids = sc ? sc.parts : a && wb.focus ? subtreeParts(wb.focus, (n) => wb.nodeShown(n)) : new Set<string>();
    const pl = printList([...ids].map((id) => wb.partInfo(id)?.part).filter((p): p is MPart => !!p));
    const nPrint = pl.groups.reduce((k, g) => k + g.count, 0);
    const printRows = pl.groups.length || pl.unknown.length
      ? `<tr class="grp"><th colspan="3" scope="rowgroup">Print list <span>${nPrint} parts · ${pl.groups.length} filaments</span></th></tr>`
        + pl.groups.map((g) => `<tr title="${esc(g.parts.join(', '))}">
      <td class="num">${g.count}</td>
      <td><span class="swatch" style="background:${esc(g.color)}" aria-hidden="true"></span>${esc(g.filament)} · ${esc(g.colorName)}</td>
      <td class="exp dim">${esc(g.color)}</td></tr>`).join('')
        + (pl.unknown.length ? `<tr title="${esc(pl.unknown.join(', '))}"><td class="num">${pl.unknown.length}</td>
      <td class="warn">No filament or colour in the manifest</td><td></td></tr>` : '')
      : '';
    $('bp-bom').innerHTML += printRows;
    $('bp-bom-sum').textContent = lines.length || nPrint ? `${lines.length} items · ${nPrint} parts to print` : '';
  }

  return {
    refreshIndex,
    /** What is open, for the viewer's header and link: the manifest (our build or a standalone
     *  design) and which list the navigator shows. */
    where: () => ({ manifest: openAsm, ourBuild: OUR_BUILD, list: src }),
    /** Open a library design / a standalone manifest / our build whole; false when the id is unknown. */
    openLibrary: async (id: string) => {
      if (!libraryFrom(wb.manifest?.root).some((x) => x.id === id)) return false;
      await openLibrary(id);
      return true;
    },
    openManifest: async (id: string) => {
      if (!index.some((x) => x.id === id)) return false;
      await openManifest(id);
      return true;
    },
    openBuild,
    /** The navigator on a list (the viewer's link back to the Library or Our build). */
    showList: (l: 'build' | 'library') => {
      src = l;
      render();
    },
    /** Resolves once the index and the first manifest are in. */
    ready: () => readyP,
  };
}

// ------------------------------------------------------------------ helpers

/** One row of the suite's servo torque check (mech/out/checks.json `torque`). */
export interface TorqueRow {
  joint: string;
  servo: string;
  status: string;
  fraction_of_stall?: number;
  servo_torque_kgcm?: number;
  stall_kgcm?: number;
  speed_ok?: boolean;
  servo_speed_needed_dps?: number;
  loaded_speed_dps?: number;
}

async function loadTorque(): Promise<TorqueRow[]> {
  try {
    if (!mayExist(`${MECH_BASE}checks.json`)) return []; // a published snapshot without one (published.ts)
    const r = await fetch(`${MECH_BASE}checks.json`, { cache: 'no-store' });
    if (!r.ok) return [];
    const d = (await r.json()) as { torque?: TorqueRow[] };
    return Array.isArray(d.torque) ? d.torque : [];
  } catch {
    return [];
  }
}

/**
 * The Checks tab's one verdict across every result the suite has: the manifest's checks, the
 * overlaps at rest (unexplained = to fix) and servo torque. `toFix` is the tab's count; the
 * summary is the line at the top of the tab ("10 to fix · 27 explained · 1 passing").
 */
export function checkVerdict(r: {
  checks: { pass: number; warn: number; fail: number; explained: number };
  overlapsOpen: number;
  overlapsExplained: number;
  torqueFail: number;
  torqueWarn: number;
  torquePass?: number;
}): { toFix: number; warn: number; summary: string } {
  const toFix = r.checks.fail + r.overlapsOpen + r.torqueFail;
  const warn = r.checks.warn + r.torqueWarn;
  const explained = r.checks.explained + r.overlapsExplained;
  const bits = [
    toFix ? `${toFix} to fix` : '',
    warn ? `${warn} to watch` : '',
    explained ? `${explained} explained` : '',
    r.checks.pass + (r.torquePass ?? 0) ? `${r.checks.pass + (r.torquePass ?? 0)} passing` : '',
  ].filter(Boolean);
  return { toFix, warn, summary: bits.join(' · ') };
}

/** The parts tree's context row (the rest of the droid, outside the focus). */
const CTX_ROW = '@rest';
/** A tree row's three states, as one compact control: a filled disc, a dashed ring, a struck eye. */
const VIS_BUTTONS: [Vis, string, string][] = [
  ['solid', '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4.5" fill="currentColor"/></svg>', 'Solid'],
  ['ghost', '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="4.5" stroke-dasharray="2.2 1.8"/></svg>', 'Ghost'],
  ['hidden', '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2l12 12M6.5 4a6.5 6.5 0 0 1 8 4 9 9 0 0 1-1.6 2.2M9.8 12.3A6.4 6.4 0 0 1 1.5 8a9 9 0 0 1 2.3-2.8"/></svg>', 'Hidden'],
];
const STATUS_GLYPH: Record<string, string> = { fail: '✕', warn: '!', explained: 'i', pass: '✓' };
const SOLO = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>';
const PLAY = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7.5-4.5z"/></svg>';
const STOP = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4.5" y="4.5" width="7" height="7" rx="1"/></svg>';
const SWEEP = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 11a5.5 5.5 0 0 1 10 0"/><path d="m13 11-.3-2.6M13 11l-2.4-.9"/></svg>';

/**
 * A check's summary as two short lines: the cause and the fix ("explained: X -> fix: Y"), or
 * the first finding with a count of the rest. Long coordinates are rounded and dropped from the
 * line (the Details list keeps every finding).
 */
export function brief(c: { summary: string }): { cause: string; fix: string; items: string[] } {
  const round = (t: string) => t.replace(/-?\d+\.\d{3,}/g, (m) => String(Math.round(Number(m) * 10) / 10));
  const s = round(c.summary.replace(/^\s*explained:\s*/i, ''));
  const arrow = s.split(/\s*->\s*/);
  if (arrow.length > 1) return { cause: arrow[0], fix: arrow.slice(1).join(' -> ').replace(/^fix:\s*/i, ''), items: [s] };
  const items = s.split(/;\s+/).filter(Boolean);
  const first = (items[0] ?? '').replace(/\s+at \[[^\]]*\]/g, '');
  return { cause: items.length > 1 ? `${first} (+${items.length - 1} more)` : first, fix: '', items };
}


/** The robot-profile joint a joint is, when its name does not already say it. */
function profileTag(p: string | null | undefined, name = '') {
  const slug = jointLabel(name).toLowerCase().replace(/\s+/g, '_');
  if (p && slug === p) return '';
  return p
    ? ` <span class="ptag" title="This joint is ${esc(p)} in the robot profile (Bench, Studio, Show)">${esc(p)}</span>`
    : ' <span class="ptag none" title="The robot profile has no such joint">not in profile</span>';
}

/** One variant group as a labelled select: short option names (full on hover). */
function variantSelect(group: string, opts: { id: string; name: string }[], picked: string | undefined) {
  const label = group.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const cur = opts.find((o) => o.id === picked);
  return `<label class="bp-var" title="${esc(cur?.name ?? label)}"><span>${esc(label)}</span><select data-bp="variant-sel" data-group="${esc(group)}">${
    opts.map((o) => `<option value="${esc(o.id)}"${o.id === picked ? ' selected' : ''}>${esc(bare(o.name))}</option>`).join('')}</select></label>`;
}

function driveLabel(d?: { kind?: string; servos?: string[]; gear_ratio?: number | null }) {
  if (!d?.kind) return 'undriven';
  const k = { push_rod_pair: 'two push rods', push_rod: 'push rod', direct: 'direct', gear: 'gear', external: 'outside this assembly', none: 'undriven' }[d.kind] ?? d.kind;
  return `${k}${d.servos?.length ? ` (${d.servos.join(', ')})` : ''}${d.gear_ratio ? ` ${d.gear_ratio}:1` : ''}`;
}

function fastLabel(s: { type: string; thread?: string; length_mm?: number; mcmaster?: string }) {
  const names: Record<string, string> = {
    shcs: 'socket head cap screw', bhcs: 'button head screw', insert: 'heat-set insert', lock_nut: 'lock nut',
    nut: 'nut', washer: 'washer', threaded_rod: 'threaded rod', pin: 'alignment pin',
  };
  const size = s.thread ? (s.length_mm && s.type !== 'insert' ? `${s.thread} × ${s.length_mm}` : s.thread) : '';
  return `${size} ${names[s.type] ?? s.type}`.trim() + (s.mcmaster ? ` (McMaster ${s.mcmaster})` : '');
}

function stepTitle(a: MAssembly | undefined, id: string) {
  const s = a?.steps?.find((x) => x.id === id);
  return s ? `${s.n}. ${s.title}` : id;
}
